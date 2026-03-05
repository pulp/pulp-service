# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
# ]
# ///
"""
Repair Python metadata for all Python repositories across all domains.

Iterates over all Pulp domains (excluding content-sources domains), finds
Python repositories in each, and triggers the repair_metadata endpoint.

Authentication uses credentials from ~/.netrc.

Usage:
    uv run management_tools/repair-python-metadata.py --env stage
    uv run management_tools/repair-python-metadata.py --env prod
    uv run management_tools/repair-python-metadata.py --base-url https://packages.stage.redhat.com
"""

import argparse
import logging
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ENVIRONMENTS = {
    "stage": "https://packages.stage.redhat.com",
    "prod": "https://packages.redhat.com",
}

CONTENT_SOURCES_LABEL = "contentsources"


def get_all_pages(url, session):
    """Paginate through all results from a Pulp API endpoint."""
    while url:
        response = session.get(url)
        response.raise_for_status()
        data = response.json()
        yield from data.get("results", [])
        url = data.get("next")


def get_domains(session, base_url):
    """Get all domains, excluding content-sources domains."""
    url = f"{base_url}/api/pulp/api/v3/domains/?limit=100"
    domains = list(get_all_pages(url, session))
    filtered = [
        d for d in domains
        if d.get("pulp_labels", {}).get(CONTENT_SOURCES_LABEL) != "true"
    ]
    log.info(
        f"Found {len(domains)} total domains, {len(filtered)} after excluding content-sources"
    )
    return filtered


def get_python_repos(session, base_url, domain_name):
    """Get all Python repositories for a given domain."""
    url = f"{base_url}/api/pulp/{domain_name}/api/v3/repositories/python/python/?limit=100"
    return list(get_all_pages(url, session))


def repair_metadata(session, base_url, repo):
    """Trigger repair_metadata for a repository."""
    repair_url = repo["pulp_href"].rstrip("/") + "/repair_metadata/"
    full_url = f"{base_url}{repair_url}"
    response = session.post(full_url)
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(
        description="Repair Python metadata for all repositories across all domains."
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
        "--dry-run",
        action="store_true",
        help="List repositories that would be repaired without triggering repair",
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

    session = requests.Session()

    domains = get_domains(session, base_url)

    total_repos = 0
    total_dispatched = 0

    for domain in domains:
        domain_name = domain["name"]
        repos = get_python_repos(session, base_url, domain_name)

        if not repos:
            continue

        total_repos += len(repos)
        for repo in repos:
            if args.dry_run:
                log.info(f"[DRY RUN] Would repair: {domain_name}/{repo['name']}")
            else:
                try:
                    result = repair_metadata(session, base_url, repo)
                    task_href = result.get("task", "unknown")
                    log.info(f"Dispatched repair for {domain_name}/{repo['name']} -> {task_href}")
                    total_dispatched += 1
                except requests.HTTPError as e:
                    log.error(f"Failed to repair {domain_name}/{repo['name']}: {e}")

    if args.dry_run:
        log.info(f"Dry run complete: {total_repos} repos across {len(domains)} domains")
    else:
        log.info(f"Done: {total_dispatched}/{total_repos} repair tasks dispatched")


if __name__ == "__main__":
    main()
