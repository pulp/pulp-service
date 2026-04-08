import asyncio
import logging

import aiohttp

from asgiref.sync import sync_to_async
from pulpcore.plugin.tasking import dispatch
from pulp_python.app.models import PythonPackageContent
from pulp_service.app.constants import PYPI_JSON_URL, PYPI_CHECK_CONCURRENCY
from pulp_service.app.models import YankedPackageReport


_logger = logging.getLogger(__name__)


async def _gather_packages(content_qs):
    """Return {name: {version, ...}} from a PythonPackageContent queryset."""
    qs = await sync_to_async(list)(
        content_qs.values_list("name", "version").distinct()
    )
    packages = {}
    for name, version in qs:
        if name and version:
            packages.setdefault(name, set()).add(version)
    return packages


async def _run_yank_check(packages):
    """Check packages against PyPI and return {name==version: {yanked_reason}} for yanked ones."""
    yanked = {}
    semaphore = asyncio.Semaphore(PYPI_CHECK_CONCURRENCY)
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *[
                _check_package(session, semaphore, name, versions)
                for name, versions in packages.items()
            ],
            return_exceptions=True,
        )
    for result in results:
        if isinstance(result, Exception):
            _logger.error("PyPI yank check failed for a package: %s", result)
        else:
            yanked.update(result)
    return yanked



def dispatch_pypi_yank_checks():
    """Dispatch a per-monitor yank check task for every registered PyPIYankMonitor."""
    from pulp_service.app.models import PyPIYankMonitor

    monitors = PyPIYankMonitor.objects.select_related(
        "repository", "repository_version__repository"
    )
    for monitor in monitors:
        repo = monitor.repository or monitor.repository_version.repository
        dispatch(
            check_packages_for_monitor,
            shared_resources=[repo],
            kwargs={"monitor_pk": str(monitor.pk)},
        )


async def check_packages_for_monitor(monitor_pk):
    """Run a PyPI yank check scoped to a single PyPIYankMonitor's repository/version."""
    from django.utils import timezone
    from pulp_service.app.models import PyPIYankMonitor

    monitor = await sync_to_async(
        PyPIYankMonitor.objects.select_related(
            "repository", "repository_version__repository"
        ).get
    )(pk=monitor_pk)

    repo_version, repo_name = await sync_to_async(monitor.get_repo_version_and_name)()

    packages = await _gather_packages(
        PythonPackageContent.objects.filter(pk__in=repo_version.content)
    )
    yanked = await _run_yank_check(packages)

    await sync_to_async(YankedPackageReport.objects.create)(
        report=yanked, monitor=monitor, repository_name=repo_name
    )
    monitor.last_checked = timezone.now()
    await sync_to_async(monitor.save)(update_fields=["last_checked"])


async def _check_package(session, semaphore, name, versions):
    """
    Fetch the PyPI JSON for a package and return yanked {name==version: {yanked_reason}}
    entries for any of the given versions that are yanked.
    Returns an empty dict if the package is not on PyPI or no versions are yanked.
    """
    url = PYPI_JSON_URL.format(package=name)
    async with semaphore:
        try:
            async with session.get(url, raise_for_status=False) as response:
                if response.status == 404:
                    return {}
                response.raise_for_status()
                data = await response.json()
        except aiohttp.ClientError:
            return {}

    releases = data.get("releases", {})
    yanked = {}
    for version in versions:
        files = releases.get(version, [])
        if files and all(f.get("yanked") for f in files):
            reason = next(
                (f.get("yanked_reason") for f in files if f.get("yanked_reason")), None
            )
            yanked[f"{name}=={version}"] = {"yanked_reason": reason}
    return yanked
