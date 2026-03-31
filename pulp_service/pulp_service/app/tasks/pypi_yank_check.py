import asyncio
import aiohttp

from asgiref.sync import sync_to_async
from pulpcore.plugin.models import CreatedResource
from pulp_python.app.models import PythonPackageContent
from pulp_service.app.constants import PYPI_JSON_URL, PYPI_CHECK_CONCURRENCY
from pulp_service.app.models import YankedPackageReport


async def check_python_packages_for_yanked():
    """
    Query all PythonPackageContent in Pulp, check PyPI for yanked versions,
    and store a YankedPackageReport.
    """
    packages = {}
    qs = await sync_to_async(list)(
        PythonPackageContent.objects.values_list("name", "version").distinct()
    )
    for name, version in qs:
        if name and version:
            packages.setdefault(name, set()).add(version)

    yanked = {}
    semaphore = asyncio.Semaphore(PYPI_CHECK_CONCURRENCY)

    async with aiohttp.ClientSession() as session:
        tasks = [
            _check_package(session, semaphore, name, versions)
            for name, versions in packages.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, dict):
            yanked.update(result)

    report = await sync_to_async(YankedPackageReport.objects.create)(report=yanked)
    await CreatedResource.objects.acreate(content_object=report)


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
