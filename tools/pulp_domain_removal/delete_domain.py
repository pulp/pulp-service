#!/usr/bin/env python3
"""
Pulp Domain Deletion Script

You need to install the client bindings:
pip3 install crc-{pulpcore,pulp-python,pulp-npm,pulp-gem,pulp-rpm,pulp-maven,pulp-file,pulp-service}-client

This script cleans up all resources in a Pulp domain and then deletes the domain itself.
It uses the Pulp client bindings to:

1. Delete all repositories in the domain (python, file, rpm, maven)
2. Delete all distributions in the domain
3. Delete all remotes in the domain
4. Delete all publications in the domain
5. Clean up orphaned content
6. Wait for pending tasks and retry domain deletion
7. Delete the domain itself

Usage:
    python delete_domain.py --domain <domain_name>
    
    # With custom config file
    python delete_domain.py --domain <domain_name> --config ~/.config/pulp/cli.toml

Configuration:
    Uses the standard pulp CLI configuration file (~/.config/pulp/cli.toml)
    with certificate-based authentication.
"""

import argparse
import json
import os
import sys
import time
import tomllib
from typing import Optional

import pulpcore.client.pulpcore


def config_from_pulp_cli_config(
    path: str = "",
    profile: str = "cli",
) -> pulpcore.client.pulpcore.Configuration:
    """
    Create a Pulp client configuration from a standard pulp CLI config file.
    
    Args:
        path: Path to the config file (default: ~/.config/pulp/cli.toml)
        profile: The profile name in the config file (default: "cli")
    
    Returns:
        A configured pulpcore Configuration object
    """
    path = os.path.expanduser(path or "~/.config/pulp/cli.toml")
    with open(path, "rb") as fp:
        config = tomllib.load(fp)[profile]
        configuration = pulpcore.client.pulpcore.Configuration()
        configuration.host = config["base_url"]
        configuration.cert_file = config["cert"]
        configuration.key_file = config["key"]
        configuration.domain = config.get("domain", "default")
    return configuration


class PulpDomainCleanup:
    """Handles cleanup of a Pulp domain and all its resources."""

    TASK_POLL_INTERVAL = 2  # seconds
    TASK_TIMEOUT = 300  # seconds (5 minutes)
    BATCH_SIZE = 100  # Number of resources to fetch per request
    DOMAIN_DELETE_RETRIES = 5  # Number of retries for domain deletion
    DOMAIN_DELETE_RETRY_DELAY = 10  # seconds between retries
    PENDING_TASK_WAIT_TIMEOUT = 120  # seconds to wait for pending tasks

    def __init__(
        self,
        configuration: pulpcore.client.pulpcore.Configuration,
        domain: Optional[str] = None,
    ):
        """
        Initialize the cleanup handler.

        Args:
            configuration: Pulp API configuration with cert auth
            domain: The Pulp domain name to clean up (overrides config domain)
        """
        self.configuration = configuration
        self.domain = domain or configuration.domain

        # Create API client
        self.api_client = pulpcore.client.pulpcore.ApiClient(configuration)

        # Initialize core APIs (these handle all content types via generic endpoints)
        self.domains_api = pulpcore.client.pulpcore.DomainsApi(self.api_client)
        self.repositories_api = pulpcore.client.pulpcore.RepositoriesApi(self.api_client)
        self.distributions_api = pulpcore.client.pulpcore.DistributionsApi(self.api_client)
        self.remotes_api = pulpcore.client.pulpcore.RemotesApi(self.api_client)
        self.publications_api = pulpcore.client.pulpcore.PublicationsApi(self.api_client)
        self.tasks_api = pulpcore.client.pulpcore.TasksApi(self.api_client)
        self.contentguards_api = pulpcore.client.pulpcore.ContentguardsApi(self.api_client)
        self.orphans_cleanup_api = pulpcore.client.pulpcore.OrphansCleanupApi(self.api_client)

    def _delete_resource(self, href: str) -> Optional[str]:
        """
        Delete a resource by its href using the REST client directly.
        
        Args:
            href: The pulp_href of the resource to delete
            
        Returns:
            The task href if deletion is async, None if sync or failed
        """
        # Build full URL
        url = f"{self.configuration.host}{href}"
        
        # Make DELETE request using the rest client
        response = self.api_client.rest_client.request(
            method='DELETE',
            url=url,
        )
        response.read()
        
        # Parse response to get task href
        if response.data:
            task_data = json.loads(response.data.decode('utf-8'))
            return task_data.get('task')
        return None

    def wait_for_task(self, task_href: str) -> bool:
        """
        Wait for a task to complete.

        Args:
            task_href: The href of the task to wait for

        Returns:
            True if task completed successfully, False otherwise
        """
        start_time = time.time()
        while time.time() - start_time < self.TASK_TIMEOUT:
            try:
                task = self.tasks_api.read(task_href)
                if task.state == "completed":
                    return True
                elif task.state in ("failed", "canceled"):
                    print(f"  ❌ Task {task_href} {task.state}")
                    if task.error:
                        print(f"     Error: {task.error}")
                    return False
                # Task still running, wait and retry
                time.sleep(self.TASK_POLL_INTERVAL)
            except Exception as e:
                print(f"  ⚠️ Error checking task status: {e}")
                time.sleep(self.TASK_POLL_INTERVAL)

        print(f"  ⏰ Task {task_href} timed out after {self.TASK_TIMEOUT} seconds")
        return False

    def wait_for_pending_tasks(self) -> bool:
        """
        Wait for all pending/running tasks in the domain to complete.

        Returns:
            True if all tasks completed, False if timeout
        """
        print(f"\n⏳ Waiting for pending tasks in domain '{self.domain}'...")
        start_time = time.time()

        while time.time() - start_time < self.PENDING_TASK_WAIT_TIMEOUT:
            try:
                # Check for running or waiting tasks
                running_tasks = self.tasks_api.list(
                    pulp_domain=self.domain,
                    state__in=["running", "waiting"],
                    limit=10,
                )

                if not running_tasks.results:
                    print(f"  ✅ No pending tasks in domain")
                    return True

                task_count = running_tasks.count or len(running_tasks.results)
                print(f"  ⏳ {task_count} task(s) still running/waiting, checking again in {self.TASK_POLL_INTERVAL}s...")
                time.sleep(self.TASK_POLL_INTERVAL)

            except Exception as e:
                print(f"  ⚠️ Error checking pending tasks: {e}")
                time.sleep(self.TASK_POLL_INTERVAL)

        print(f"  ⏰ Timeout waiting for pending tasks after {self.PENDING_TASK_WAIT_TIMEOUT} seconds")
        return False

    def cleanup_orphans(self, protection_time: int = 0) -> bool:
        """
        Clean up orphaned content in the domain.

        Args:
            protection_time: Orphan protection time in minutes (0 = immediate cleanup)

        Returns:
            True if cleanup completed successfully
        """
        print(f"\n🗑️ Cleaning up orphaned content in domain '{self.domain}'...")

        try:
            # Create orphan cleanup request
            orphan_cleanup = pulpcore.client.pulpcore.OrphansCleanup(
                orphan_protection_time=protection_time
            )

            result = self.orphans_cleanup_api.cleanup(
                orphans_cleanup=orphan_cleanup,
                pulp_domain=self.domain,
            )

            task_href = result.task
            print(f"  ⏳ Orphan cleanup task started: {task_href}")

            if self.wait_for_task(task_href):
                print(f"  ✅ Orphan cleanup completed successfully")
                return True
            else:
                print(f"  ❌ Orphan cleanup failed")
                return False

        except Exception as e:
            print(f"  ⚠️ Error during orphan cleanup: {e}")
            return False

    def delete_repositories(self) -> int:
        """
        Delete all repositories in the domain.

        Returns:
            Number of repositories deleted
        """
        print(f"\n🗑️ Deleting repositories in domain '{self.domain}'...")
        deleted_count = 0
        offset = 0

        while True:
            try:
                # List repositories using the generic API
                repos = self.repositories_api.list(
                    pulp_domain=self.domain,
                    limit=self.BATCH_SIZE,
                    offset=offset,
                )

                if not repos.results:
                    break

                for repo in repos.results:
                    href = repo.pulp_href
                    pulp_type = getattr(repo, 'pulp_type', 'unknown')
                    name = getattr(repo, 'name', href)
                    
                    print(f"  Deleting {pulp_type} repository: {name}")
                    
                    try:
                        task_href = self._delete_resource(href)
                        if task_href:
                            if self.wait_for_task(task_href):
                                deleted_count += 1
                                print(f"  ✅ Deleted: {name}")
                            else:
                                print(f"  ❌ Failed to delete: {name}")
                        else:
                            # Synchronous deletion
                            deleted_count += 1
                            print(f"  ✅ Deleted: {name}")
                    except Exception as e:
                        print(f"  ❌ Error deleting {name}: {e}")

                # Check if there are more results
                if repos.next is None:
                    break
                offset += self.BATCH_SIZE

            except Exception as e:
                print(f"  ⚠️ Error listing repositories: {e}")
                break

        print(f"  📊 Deleted {deleted_count} repositories")
        return deleted_count

    def delete_distributions(self) -> int:
        """
        Delete all distributions in the domain.

        Returns:
            Number of distributions deleted
        """
        print(f"\n🗑️ Deleting distributions in domain '{self.domain}'...")
        deleted_count = 0
        offset = 0

        while True:
            try:
                distributions = self.distributions_api.list(
                    pulp_domain=self.domain,
                    limit=self.BATCH_SIZE,
                    offset=offset,
                )

                if not distributions.results:
                    break

                for dist in distributions.results:
                    href = dist.pulp_href
                    name = getattr(dist, 'name', href)
                    
                    print(f"  Deleting distribution: {name}")
                    
                    try:
                        task_href = self._delete_resource(href)
                        if task_href:
                            if self.wait_for_task(task_href):
                                deleted_count += 1
                                print(f"  ✅ Deleted: {name}")
                            else:
                                print(f"  ❌ Failed to delete: {name}")
                        else:
                            deleted_count += 1
                            print(f"  ✅ Deleted: {name}")
                    except Exception as e:
                        print(f"  ❌ Error deleting {name}: {e}")

                if distributions.next is None:
                    break
                offset += self.BATCH_SIZE

            except Exception as e:
                print(f"  ⚠️ Error listing distributions: {e}")
                break

        print(f"  📊 Deleted {deleted_count} distributions")
        return deleted_count

    def delete_remotes(self) -> int:
        """
        Delete all remotes in the domain.

        Returns:
            Number of remotes deleted
        """
        print(f"\n🗑️ Deleting remotes in domain '{self.domain}'...")
        deleted_count = 0
        offset = 0

        while True:
            try:
                remotes = self.remotes_api.list(
                    pulp_domain=self.domain,
                    limit=self.BATCH_SIZE,
                    offset=offset,
                )

                if not remotes.results:
                    break

                for remote in remotes.results:
                    href = remote.pulp_href
                    name = getattr(remote, 'name', href)
                    
                    print(f"  Deleting remote: {name}")
                    
                    try:
                        task_href = self._delete_resource(href)
                        if task_href:
                            if self.wait_for_task(task_href):
                                deleted_count += 1
                                print(f"  ✅ Deleted: {name}")
                            else:
                                print(f"  ❌ Failed to delete: {name}")
                        else:
                            deleted_count += 1
                            print(f"  ✅ Deleted: {name}")
                    except Exception as e:
                        print(f"  ❌ Error deleting {name}: {e}")

                if remotes.next is None:
                    break
                offset += self.BATCH_SIZE

            except Exception as e:
                print(f"  ⚠️ Error listing remotes: {e}")
                break

        print(f"  📊 Deleted {deleted_count} remotes")
        return deleted_count

    def delete_publications(self) -> int:
        """
        Delete all publications in the domain.

        Returns:
            Number of publications deleted
        """
        print(f"\n🗑️ Deleting publications in domain '{self.domain}'...")
        deleted_count = 0
        offset = 0

        while True:
            try:
                publications = self.publications_api.list(
                    pulp_domain=self.domain,
                    limit=self.BATCH_SIZE,
                    offset=offset,
                )

                if not publications.results:
                    break

                for pub in publications.results:
                    href = pub.pulp_href
                    
                    print(f"  Deleting publication: {href}")
                    
                    try:
                        task_href = self._delete_resource(href)
                        if task_href:
                            if self.wait_for_task(task_href):
                                deleted_count += 1
                                print(f"  ✅ Deleted publication")
                            else:
                                print(f"  ❌ Failed to delete publication")
                        else:
                            deleted_count += 1
                            print(f"  ✅ Deleted publication")
                    except Exception as e:
                        print(f"  ❌ Error deleting publication: {e}")

                if publications.next is None:
                    break
                offset += self.BATCH_SIZE

            except Exception as e:
                print(f"  ⚠️ Error listing publications: {e}")
                break

        print(f"  📊 Deleted {deleted_count} publications")
        return deleted_count

    def delete_contentguards(self) -> int:
        """
        Delete all content guards in the domain.

        Returns:
            Number of content guards deleted
        """
        print(f"\n🗑️ Deleting content guards in domain '{self.domain}'...")
        deleted_count = 0
        offset = 0

        while True:
            try:
                contentguards = self.contentguards_api.list(
                    pulp_domain=self.domain,
                    limit=self.BATCH_SIZE,
                    offset=offset,
                )

                if not contentguards.results:
                    break

                for cg in contentguards.results:
                    href = cg.pulp_href
                    name = getattr(cg, 'name', href)
                    pulp_type = getattr(cg, 'pulp_type', 'unknown')
                    
                    print(f"  Deleting {pulp_type} content guard: {name}")
                    
                    try:
                        task_href = self._delete_resource(href)
                        if task_href:
                            if self.wait_for_task(task_href):
                                deleted_count += 1
                                print(f"  ✅ Deleted: {name}")
                            else:
                                print(f"  ❌ Failed to delete: {name}")
                        else:
                            deleted_count += 1
                            print(f"  ✅ Deleted: {name}")
                    except Exception as e:
                        print(f"  ❌ Error deleting {name}: {e}")

                if contentguards.next is None:
                    break
                offset += self.BATCH_SIZE

            except Exception as e:
                print(f"  ⚠️ Error listing content guards: {e}")
                break

        print(f"  📊 Deleted {deleted_count} content guards")
        return deleted_count

    def delete_container_namespaces(self) -> int:
        """
        Delete all container namespaces in the domain using REST API directly.
        
        Returns:
            Number of container namespaces deleted
        """
        print(f"\n🗑️ Deleting container namespaces in domain '{self.domain}'...")
        deleted_count = 0
        offset = 0

        while True:
            try:
                # Use REST API directly since there's no container client
                url = f"{self.configuration.host}/api/pulp/{self.domain}/api/v3/pulp_container/namespaces/"
                params = f"?limit={self.BATCH_SIZE}&offset={offset}"
                
                response = self.api_client.rest_client.request(
                    method='GET',
                    url=url + params,
                )
                response.read()
                
                if not response.data:
                    break
                    
                data = json.loads(response.data.decode('utf-8'))
                results = data.get('results', [])
                
                if not results:
                    break

                for ns in results:
                    href = ns.get('pulp_href')
                    name = ns.get('name', href)
                    
                    print(f"  Deleting container namespace: {name}")
                    
                    try:
                        task_href = self._delete_resource(href)
                        if task_href:
                            if self.wait_for_task(task_href):
                                deleted_count += 1
                                print(f"  ✅ Deleted: {name}")
                            else:
                                print(f"  ❌ Failed to delete: {name}")
                        else:
                            deleted_count += 1
                            print(f"  ✅ Deleted: {name}")
                    except Exception as e:
                        print(f"  ❌ Error deleting {name}: {e}")

                if data.get('next') is None:
                    break
                offset += self.BATCH_SIZE

            except Exception as e:
                # Container plugin might not be installed, which is fine
                if "404" in str(e) or "Not Found" in str(e):
                    print(f"  ℹ️ Container plugin not available, skipping")
                else:
                    print(f"  ⚠️ Error listing container namespaces: {e}")
                break

        print(f"  📊 Deleted {deleted_count} container namespaces")
        return deleted_count

    def get_domain_href(self) -> Optional[str]:
        """
        Get the pulp_href for the domain.

        Returns:
            The domain href or None if not found
        """
        try:
            domains = self.domains_api.list(name=self.domain)
            if domains.results:
                return domains.results[0].pulp_href
        except Exception as e:
            print(f"  ⚠️ Error getting domain href: {e}")
        return None

    def delete_domain(self) -> bool:
        """
        Delete the domain itself with retry logic.

        Waits for pending tasks, performs orphan cleanup, and retries
        domain deletion if it fails (e.g., due to server-side timeouts).

        Returns:
            True if domain was deleted successfully
        """
        print(f"\n🗑️ Deleting domain '{self.domain}'...")

        domain_href = self.get_domain_href()
        if not domain_href:
            print(f"  ❌ Domain '{self.domain}' not found")
            return False

        for attempt in range(1, self.DOMAIN_DELETE_RETRIES + 1):
            print(f"\n  🔄 Domain deletion attempt {attempt}/{self.DOMAIN_DELETE_RETRIES}")

            # Wait for any pending tasks before attempting deletion
            self.wait_for_pending_tasks()

            # Run orphan cleanup before deletion
            self.cleanup_orphans(protection_time=0)

            # Wait again after orphan cleanup
            self.wait_for_pending_tasks()

            try:
                result = self.domains_api.delete(domain_href)
                task_href = result.task

                if self.wait_for_task(task_href):
                    print(f"  ✅ Domain '{self.domain}' deleted successfully")
                    return True
                else:
                    print(f"  ⚠️ Domain deletion task failed on attempt {attempt}")

            except Exception as e:
                error_msg = str(e)
                if "timed out" in error_msg.lower():
                    print(f"  ⚠️ Domain deletion timed out on attempt {attempt}: {e}")
                else:
                    print(f"  ❌ Error deleting domain on attempt {attempt}: {e}")

            if attempt < self.DOMAIN_DELETE_RETRIES:
                print(f"  ⏳ Waiting {self.DOMAIN_DELETE_RETRY_DELAY}s before retry...")
                time.sleep(self.DOMAIN_DELETE_RETRY_DELAY)

        print(f"  ❌ Failed to delete domain '{self.domain}' after {self.DOMAIN_DELETE_RETRIES} attempts")
        return False

    def cleanup(self, delete_domain: bool = True) -> dict:
        """
        Perform the full cleanup of the domain.

        Args:
            delete_domain: Whether to delete the domain after cleaning up resources

        Returns:
            Dictionary with cleanup statistics
        """
        print(f"\n{'='*60}")
        print(f"🧹 Starting cleanup of Pulp domain: {self.domain}")
        print(f"{'='*60}")

        stats = {
            "repositories_deleted": 0,
            "distributions_deleted": 0,
            "remotes_deleted": 0,
            "publications_deleted": 0,
            "contentguards_deleted": 0,
            "container_namespaces_deleted": 0,
            "orphan_cleanup": False,
            "domain_deleted": False,
        }

        # Order matters: delete in dependency order
        # 1. Publications (depend on repos)
        stats["publications_deleted"] = self.delete_publications()

        # 2. Distributions (may reference publications/repos/content guards)
        stats["distributions_deleted"] = self.delete_distributions()

        # 3. Repositories
        stats["repositories_deleted"] = self.delete_repositories()

        # 4. Remotes (referenced by repos, so delete after repos)
        stats["remotes_deleted"] = self.delete_remotes()

        # 5. Content guards (may be referenced by distributions)
        stats["contentguards_deleted"] = self.delete_contentguards()

        # 6. Container namespaces
        stats["container_namespaces_deleted"] = self.delete_container_namespaces()

        # 7. Clean up orphaned content
        stats["orphan_cleanup"] = self.cleanup_orphans(protection_time=0)

        # 8. Wait for all tasks to complete before domain deletion
        self.wait_for_pending_tasks()

        # 9. Finally, delete the domain (includes retry logic)
        if delete_domain:
            stats["domain_deleted"] = self.delete_domain()

        # Print summary
        print(f"\n{'='*60}")
        print("📊 Cleanup Summary")
        print(f"{'='*60}")
        print(f"  Repositories deleted: {stats['repositories_deleted']}")
        print(f"  Distributions deleted: {stats['distributions_deleted']}")
        print(f"  Remotes deleted: {stats['remotes_deleted']}")
        print(f"  Publications deleted: {stats['publications_deleted']}")
        print(f"  Content guards deleted: {stats['contentguards_deleted']}")
        print(f"  Container namespaces deleted: {stats['container_namespaces_deleted']}")
        print(f"  Orphan cleanup: {'✅' if stats['orphan_cleanup'] else '❌'}")
        if delete_domain:
            print(f"  Domain deleted: {'✅' if stats['domain_deleted'] else '❌'}")
        print(f"{'='*60}\n")

        return stats


def main():
    parser = argparse.ArgumentParser(
        description="Clean up a Pulp domain and optionally delete it",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Clean up and delete a domain (uses domain from config)
    python delete_domain.py --domain my-domain

    # Clean up resources but keep the domain
    python delete_domain.py --domain my-domain --keep-domain

    # Use custom config file and profile
    python delete_domain.py --domain my-domain --config ~/.config/pulp/cli.toml --profile cli-prod

Configuration:
    Uses the standard pulp CLI configuration file (~/.config/pulp/cli.toml)
    with certificate-based authentication.
        """
    )

    parser.add_argument(
        "--domain",
        "-d",
        required=True,
        help="Name of the Pulp domain to clean up",
    )
    parser.add_argument(
        "--config",
        "-c",
        default="~/.config/pulp/cli.toml",
        help="Path to pulp CLI config file (default: ~/.config/pulp/cli.toml)",
    )
    parser.add_argument(
        "--profile",
        "-p",
        default="cli",
        help="Profile name in the config file (default: cli)",
    )
    parser.add_argument(
        "--keep-domain",
        "-k",
        action="store_true",
        help="Clean up resources but don't delete the domain itself",
    )

    args = parser.parse_args()


    # Confirmation prompt - require user to retype domain name
    print(f"\n⚠️  WARNING: You are about to delete all resources in domain '{args.domain}'")
    if not args.keep_domain:
        print(f"   The domain itself will also be deleted!")
    print(f"\n   To confirm, please type the domain name: ", end="")
    confirmation = input().strip()
    if confirmation != args.domain:
        print(f"\n❌ Domain name mismatch. Expected '{args.domain}', got '{confirmation}'")
        print("   Aborting cleanup.")
        sys.exit(1)
    print()

    try:
        # Load configuration from pulp CLI config file
        print(f"📂 Loading configuration from {args.config} (profile: {args.profile})...")
        configuration = config_from_pulp_cli_config(
            path=args.config,
            profile=args.profile,
        )

        cleanup = PulpDomainCleanup(
            configuration=configuration,
            domain=args.domain,
        )

        stats = cleanup.cleanup(delete_domain=not args.keep_domain)

        # Exit with error if domain deletion failed (when requested)
        if not args.keep_domain and not stats["domain_deleted"]:
            sys.exit(1)

    except FileNotFoundError as e:
        print(f"❌ Configuration file not found: {e}")
        print("\nMake sure you have a valid pulp CLI config file at:")
        print(f"  {args.config}")
        sys.exit(1)
    except KeyError as e:
        print(f"❌ Configuration profile not found: {e}")
        print(f"\nMake sure the profile '{args.profile}' exists in your config file")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️ Cleanup interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
