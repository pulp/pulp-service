# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
# ]
# ///
"""
Verify that repair_metadata worked for Python repositories across all domains.

Checks the simple page for each repository's packages and verifies that:
1. All wheel links have a `data-core-metadata` attribute set.
2. The metadata file (package URL + .metadata) is downloadable.

By default, samples one package per repo. Use --thorough to check all packages.

Authentication uses credentials from ~/.netrc.

Usage:
    uv run management_tools/verify-python-metadata.py --env stage
    uv run management_tools/verify-python-metadata.py --env prod --thorough
    uv run management_tools/verify-python-metadata.py --base-url https://packages.stage.redhat.com
"""

import argparse
import logging
import sys
from html.parser import HTMLParser

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ENVIRONMENTS = {
    "stage": "https://packages.stage.redhat.com",
    "prod": "https://packages.redhat.com",
}

CONTENT_SOURCES_LABEL = "contentsources"


def get_all_pages(url, session, retries=3):
    """Paginate through all results from a Pulp API endpoint."""
    while url:
        for attempt in range(retries):
            response = session.get(url)
            if response.status_code == 500 and attempt < retries - 1:
                log.warning(f"500 error on {url}, retrying ({attempt + 1}/{retries})...")
                continue
            response.raise_for_status()
            break
        data = response.json()
        yield from data.get("results", [])
        url = data.get("next")


def get_domains(session, base_url):
    """Get all domains, excluding content-sources domains."""
    url = (
        f"{base_url}/api/pulp/default/api/v3/domains/"
        f"?limit=50&fields=name,pulp_href,pulp_labels"
    )
    all_domains = list(get_all_pages(url, session))
    domains = [
        d for d in all_domains if d.get("pulp_labels", {}).get(CONTENT_SOURCES_LABEL) != "true"
    ]
    log.info(
        f"Found {len(all_domains)} total domains, "
        f"{len(domains)} after excluding content-sources"
    )
    return domains


def get_python_repos(session, base_url, domain_name):
    """Get all Python repositories for a given domain."""
    url = f"{base_url}/api/pulp/{domain_name}/api/v3/repositories/python/python/?limit=100"
    return list(get_all_pages(url, session))


def get_distributions(session, base_url, domain_name, repo_href):
    """Get Python distributions for a given repository."""
    url = (
        f"{base_url}/api/pulp/{domain_name}/api/v3/distributions/python/pypi/"
        f"?repository={repo_href}&fields=name,base_path,pulp_href&limit=100"
    )
    return list(get_all_pages(url, session))


class SimplePageParser(HTMLParser):
    """Parse a PEP 503 simple page and extract link info."""

    def __init__(self):
        super().__init__()
        self.links = []
        self._current_attrs = {}
        self._current_href = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            attr_dict = dict(attrs)
            self._current_href = attr_dict.get("href")
            self._current_attrs = attr_dict

    def handle_data(self, data):
        if self._current_href is not None:
            self.links.append(
                {
                    "filename": data.strip(),
                    "href": self._current_href,
                    "attrs": self._current_attrs,
                }
            )
            self._current_href = None
            self._current_attrs = {}


def get_simple_index(session, base_url, domain_name, base_path):
    """Get the list of packages from the simple index for a distribution."""
    url = f"{base_url}/api/pypi/{domain_name}/{base_path}/simple/"
    response = session.get(url)
    response.raise_for_status()
    parser = SimplePageParser()
    parser.feed(response.text)
    return [link["filename"] for link in parser.links]


def get_simple_page(session, base_url, domain_name, base_path, package_name):
    """Get the simple page for a specific package and return parsed links."""
    url = f"{base_url}/api/pypi/{domain_name}/{base_path}/simple/{package_name}/"
    response = session.get(url)
    response.raise_for_status()
    parser = SimplePageParser()
    parser.feed(response.text)
    return parser.links


def resolve_link_url(base_url, href):
    """Resolve a link href to a full content URL for .metadata download.

    Strips both fragment (#sha256=...) and query string (?redirect=...) to get
    the direct content URL that can have .metadata appended.
    """
    # Strip fragment first
    url = href.split("#")[0]
    # Strip query string (e.g. ?redirect=...)
    url = url.split("?")[0]
    if url.startswith("http://") or url.startswith("https://"):
        return url
    # Relative URL
    return f"{base_url}/{url.lstrip('/')}"


def verify_package(session, base_url, domain_name, base_path, package_name):
    """Verify metadata for all wheels in a package's simple page.

    Returns (wheels_found, failures) where failures is a list of error strings.
    """
    links = get_simple_page(session, base_url, domain_name, base_path, package_name)
    wheel_links = [link for link in links if link["filename"].endswith(".whl")]

    if not wheel_links:
        return 0, [], []

    failures = []
    warnings = []
    for link in wheel_links:
        filename = link["filename"]
        attrs = link["attrs"]

        # Check 1: data-core-metadata attribute
        core_metadata = attrs.get("data-core-metadata")
        has_attribute = bool(core_metadata)

        if core_metadata and not core_metadata.startswith("sha256="):
            failures.append(f"{filename}: data-core-metadata has unexpected value: {core_metadata}")

        # Check 2: download the .metadata file
        download_url = resolve_link_url(base_url, link["href"])
        metadata_url = f"{download_url}.metadata"
        metadata_downloadable = False
        try:
            resp = session.get(metadata_url, allow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 0:
                metadata_downloadable = True
            elif resp.status_code != 200:
                failures.append(f"{filename}: .metadata returned HTTP {resp.status_code}")
            else:
                failures.append(f"{filename}: .metadata response is empty")
        except requests.RequestException as e:
            failures.append(f"{filename}: .metadata download failed: {e}")

        if not has_attribute and metadata_downloadable:
            warnings.append(
                f"{filename}: missing data-core-metadata attribute but .metadata is downloadable"
            )
        elif not has_attribute and not metadata_downloadable:
            failures.append(
                f"{filename}: missing data-core-metadata attribute and .metadata not available"
            )

    return len(wheel_links), failures, warnings


def verify_repo(session, base_url, domain_name, repo, thorough=False):
    """Verify metadata for a repository via its distribution's simple page.

    Returns a dict with verification results.
    """
    repo_name = repo["name"]
    repo_href = repo["pulp_href"]
    result = {
        "domain": domain_name,
        "repo": repo_name,
        "status": "pass",
        "wheels_checked": 0,
        "packages_checked": 0,
        "failures": [],
        "warnings": [],
    }

    try:
        distros = get_distributions(session, base_url, domain_name, repo_href)
    except requests.HTTPError as e:
        result["status"] = "error"
        result["failures"].append(f"Failed to fetch distributions: {e}")
        return result

    if not distros:
        result["status"] = "skip"
        result["failures"].append("No distribution found for repository")
        return result

    base_path = distros[0]["base_path"]

    try:
        packages = get_simple_index(session, base_url, domain_name, base_path)
    except requests.HTTPError as e:
        result["status"] = "error"
        result["failures"].append(f"Failed to fetch simple index: {e}")
        return result

    if not packages:
        result["status"] = "skip"
        return result

    packages_to_check = packages if thorough else packages[:1]

    for package_name in packages_to_check:
        try:
            wheels_found, failures, warnings = verify_package(
                session, base_url, domain_name, base_path, package_name
            )
        except requests.HTTPError as e:
            result["failures"].append(f"{package_name}: failed to fetch simple page: {e}")
            result["status"] = "fail"
            continue

        result["wheels_checked"] += wheels_found
        result["packages_checked"] += 1

        if warnings:
            result["warnings"].extend(warnings)

        if failures:
            result["failures"].extend(failures)
            result["status"] = "fail"

    if result["wheels_checked"] == 0 and result["status"] == "pass":
        result["status"] = "skip"

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Verify Python metadata repair across all domains."
    )
    parser.add_argument(
        "--env",
        choices=["stage", "prod"],
        help="Target environment (stage or prod)",
    )
    parser.add_argument(
        "--base-url",
        help="Base URL for the Pulp API (overrides --env)",
    )
    parser.add_argument(
        "--domain",
        help="Only verify this specific domain (by name)",
    )
    parser.add_argument(
        "--thorough",
        action="store_true",
        help="Check all packages in each repo instead of sampling one",
    )
    args = parser.parse_args()

    if args.base_url:
        base_url = args.base_url.rstrip("/")
    elif args.env:
        base_url = ENVIRONMENTS[args.env]
    else:
        parser.error("Either --env or --base-url is required")
        sys.exit(1)

    log.info(f"Target: {base_url}")
    if args.thorough:
        log.info("Thorough mode: checking all packages in each repo")

    session = requests.Session()

    if args.domain:
        domains = [{"name": args.domain}]
        log.info(f"Targeting single domain: {args.domain}")
    else:
        domains = get_domains(session, base_url)

    passed = 0
    warned = 0
    failed = 0
    skipped = 0
    errored = 0

    for domain in domains:
        domain_name = domain["name"]
        try:
            repos = get_python_repos(session, base_url, domain_name)
        except requests.HTTPError as e:
            log.error(f"Failed to list repos for domain {domain_name}: {e}")
            errored += 1
            continue

        if not repos:
            continue

        for repo in repos:
            repo_name = repo["name"]
            result = verify_repo(session, base_url, domain_name, repo, args.thorough)

            for w in result["warnings"]:
                log.warning(f"WARN {domain_name}/{repo_name}: {w}")

            if result["status"] == "pass":
                has_warnings = len(result["warnings"]) > 0
                label = "WARN" if has_warnings else "PASS"
                log.info(
                    f"{label} {domain_name}/{repo_name} "
                    f"({result['wheels_checked']} wheels in "
                    f"{result['packages_checked']} packages)"
                )
                if has_warnings:
                    warned += 1
                else:
                    passed += 1
            elif result["status"] == "skip":
                log.info(f"SKIP {domain_name}/{repo_name} (no wheels found)")
                skipped += 1
            elif result["status"] == "error":
                for f in result["failures"]:
                    log.error(f"ERROR {domain_name}/{repo_name}: {f}")
                errored += 1
            else:
                for f in result["failures"]:
                    log.warning(f"FAIL {domain_name}/{repo_name}: {f}")
                failed += 1

    total = passed + warned + failed + skipped + errored
    log.info(
        f"Summary: {passed}/{total} passed, {warned} warned, "
        f"{failed} failed, {skipped} skipped, {errored} errors"
    )

    sys.exit(1 if (failed > 0 or errored > 0) else 0)


if __name__ == "__main__":
    main()
