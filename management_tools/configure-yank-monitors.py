# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
# ]
# ///
"""
Configure and verify PyPI yank monitors for Python repositories in a Pulp domain.

Creates a PyPIYankMonitor for each Python repository that doesn't already have one.
Use --verify to check which repositories have monitors configured.

Authentication uses credentials from ~/.netrc.

Usage:
    uv run management_tools/configure-yank-monitors.py --env prod --domain public-rhai --dry-run
    uv run management_tools/configure-yank-monitors.py --env prod --domain public-rhai
    uv run management_tools/configure-yank-monitors.py --env prod --domain public-rhai --verify
    uv run management_tools/configure-yank-monitors.py --env prod --domain public-rhai --repository my-repo
    uv run management_tools/configure-yank-monitors.py --env prod --domain public-rhai --repository repo1 --repository repo2
"""

import argparse
import netrc
import sys
from urllib.parse import urlparse

import requests

ENVIRONMENTS = {
    "stage": "https://packages.stage.redhat.com",
    "prod": "https://packages.redhat.com",
}


def _raise_for_status(response: requests.Response) -> None:
    """Raise with the response body included for diagnostics."""
    try:
        response.raise_for_status()
    except requests.HTTPError as err:
        body = response.text[:500]
        raise requests.HTTPError(
            f"{err}\nResponse body: {body}",
            response=response,
        ) from err


def _safe_next_url(next_url: str | None, expected_host: str) -> str | None:
    """Return next_url only if it points to the expected host.

    Relative URLs (no hostname) are treated as same-host and returned as-is.
    """
    if next_url is None:
        return None
    parsed = urlparse(next_url)
    if parsed.hostname is not None and parsed.hostname != expected_host:
        raise ValueError(
            f"Pagination URL host mismatch: expected {expected_host}, "
            f"got {parsed.hostname}"
        )
    return next_url


def get_session(base_url: str) -> requests.Session:
    """Create a requests session using .netrc credentials."""
    hostname = urlparse(base_url).hostname

    try:
        nrc = netrc.netrc()
        auth_tuple = nrc.authenticators(hostname)
    except (FileNotFoundError, netrc.NetrcParseError) as err:
        print(f"Error reading .netrc: {err}", file=sys.stderr)
        sys.exit(1)

    if auth_tuple is None:
        print(f"No .netrc entry found for {hostname}", file=sys.stderr)
        sys.exit(1)

    login, _, password = auth_tuple

    session = requests.Session()
    session.auth = (login, password)

    response = session.get(f"{base_url}/api/pulp/api/v3/status/")
    _raise_for_status(response)
    return session


def list_python_repositories(
    session: requests.Session, api_base: str, expected_host: str
) -> list[dict]:
    """List all Python repositories in the domain, handling pagination."""
    repositories = []
    url = f"{api_base}repositories/python/python/"

    while url:
        response = session.get(url)
        _raise_for_status(response)
        data = response.json()
        repositories.extend(data.get("results", []))
        url = _safe_next_url(data.get("next"), expected_host)

    return repositories


def list_existing_monitors(
    session: requests.Session, api_base: str, expected_host: str
) -> dict[str, dict]:
    """List existing yank monitors, returning a dict keyed by repository pulp_href."""
    monitors = {}
    url = f"{api_base}pypi_yank_monitor/"

    while url:
        response = session.get(url)
        _raise_for_status(response)
        data = response.json()
        for monitor in data.get("results", []):
            repo_href = monitor.get("repository")
            if repo_href:
                monitors[repo_href] = monitor
        url = _safe_next_url(data.get("next"), expected_host)

    return monitors


def create_monitor(
    session: requests.Session,
    api_base: str,
    name: str,
    repository_href: str,
) -> dict:
    """Create a PyPIYankMonitor for a repository."""
    url = f"{api_base}pypi_yank_monitor/"
    payload = {
        "name": name,
        "repository": repository_href,
    }
    response = session.post(url, json=payload)
    _raise_for_status(response)
    return response.json()


def verify_monitors(
    repositories: list[dict], existing_monitors: dict[str, dict]
) -> bool:
    """Check which repositories have yank monitors configured. Returns True if all covered."""
    monitored = []
    unmonitored = []

    for repo in repositories:
        repo_name = repo["name"]
        repo_href = repo["pulp_href"]
        monitor = existing_monitors.get(repo_href)

        if monitor:
            last_checked = monitor.get("last_checked") or "never"
            monitored.append((repo_name, monitor["name"], last_checked))
        else:
            unmonitored.append(repo_name)

    if monitored:
        print(f"\nMonitored ({len(monitored)}):")
        for repo_name, monitor_name, last_checked in monitored:
            print(f"  OK  {repo_name} — monitor '{monitor_name}', last checked: {last_checked}")

    if unmonitored:
        print(f"\nNot monitored ({len(unmonitored)}):")
        for repo_name in unmonitored:
            print(f"  MISSING  {repo_name}")

    all_covered = len(unmonitored) == 0
    print(f"\nCoverage: {len(monitored)}/{len(repositories)} repositories monitored")
    return all_covered


def configure_monitors(
    session: requests.Session,
    api_base: str,
    repositories: list[dict],
    existing_monitors: dict[str, dict],
    dry_run: bool,
) -> None:
    """Create monitors for repositories that don't have one."""
    created_count = 0
    skipped_count = 0
    failed_count = 0
    would_create_count = 0

    for repo in repositories:
        repo_name = repo["name"]
        repo_href = repo["pulp_href"]
        monitor_name = f"yank-monitor-{repo_name}"

        if repo_href in existing_monitors:
            existing = existing_monitors[repo_href]
            print(f"  SKIP: {repo_name} — monitor '{existing['name']}' already exists")
            skipped_count += 1
            continue

        if dry_run:
            print(f"  DRY RUN: would create monitor '{monitor_name}' for {repo_name}")
            would_create_count += 1
            continue

        print(f"  CREATING: monitor '{monitor_name}' for {repo_name}...", end=" ")
        try:
            monitor = create_monitor(session, api_base, monitor_name, repo_href)
            print(f"OK ({monitor['pulp_href']})")
            created_count += 1
        except requests.HTTPError as err:
            print(f"FAILED\n    {err}", file=sys.stderr)
            failed_count += 1

    if dry_run:
        print(f"\nDry run complete. Would create: {would_create_count}, Skipped: {skipped_count}")
    else:
        print(f"\nDone. Created: {created_count}, Skipped: {skipped_count}, Failed: {failed_count}")


def main():
    parser = argparse.ArgumentParser(
        description="Configure and verify PyPI yank monitors for Python repositories."
    )
    parser.add_argument(
        "--env",
        choices=ENVIRONMENTS.keys(),
        help="Environment (stage or prod)",
    )
    parser.add_argument(
        "--base-url",
        help="Pulp base URL (alternative to --env)",
    )
    parser.add_argument(
        "--domain",
        default="public-rhai",
        help="Pulp domain name (default: public-rhai)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Check which repositories have yank monitors configured (read-only)",
    )
    parser.add_argument(
        "--repository",
        action="append",
        dest="repositories",
        metavar="NAME",
        help="Target specific repository name(s). Can be repeated. If omitted, targets all.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List repositories and planned monitors without creating anything",
    )
    args = parser.parse_args()

    if args.base_url:
        base_url = args.base_url
    elif args.env:
        base_url = ENVIRONMENTS[args.env]
    else:
        parser.error("Either --env or --base-url is required")

    api_base = f"{base_url}/api/pulp/{args.domain}/api/v3/"
    expected_host = urlparse(base_url).hostname

    print(f"Connecting to {base_url} (domain: {args.domain})")
    session = get_session(base_url)

    print("Listing Python repositories...")
    all_repositories = list_python_repositories(session, api_base, expected_host)
    if not all_repositories:
        print("No Python repositories found in this domain.")
        sys.exit(0)

    if args.repositories:
        available_names = {repo["name"] for repo in all_repositories}
        unknown = set(args.repositories) - available_names
        if unknown:
            print(f"Repository(ies) not found in domain: {', '.join(sorted(unknown))}", file=sys.stderr)
            print(f"Available: {', '.join(sorted(available_names))}", file=sys.stderr)
            sys.exit(1)
        repositories = [repo for repo in all_repositories if repo["name"] in set(args.repositories)]
    else:
        repositories = all_repositories

    print(f"Targeting {len(repositories)} of {len(all_repositories)} Python repository(ies):")
    for repo in repositories:
        print(f"  - {repo['name']}")

    print("\nFetching existing yank monitors...")
    existing_monitors = list_existing_monitors(session, api_base, expected_host)

    if args.verify:
        all_covered = verify_monitors(repositories, existing_monitors)
        sys.exit(0 if all_covered else 1)

    configure_monitors(session, api_base, repositories, existing_monitors, args.dry_run)


if __name__ == "__main__":
    main()
