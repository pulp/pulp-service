import aiohttp
import json
import threading

from asgiref.sync import sync_to_async
from queue import Queue

from pulpcore.app.util import get_domain
from pulpcore.plugin.models import CreatedResource, RepositoryVersion, PulpTemporaryFile
from pulp_npm.app.models import Package as NPMPackage
from pulp_service.app.constants import PKG_ECOSYSTEM, OSV_QUERY_URL
from pulp_service.app.models import VulnerabilityReport

# Create a thread-safe queue to share Content units between threads
content_queue = Queue()


async def check_content_from_repo_version(repo_version_pk):
    """
    Spawn a thread to retrieve the list of packages in RepoVersion(repo_version_pk) and
    make a request to osv.dev API to check the vulnerabilities.
    """
    # create a thread to collect the Contents from RepositoryVersion
    background_thread = threading.Thread(
        target=_get_content_from_repo_version, args=(repo_version_pk,)
    )
    await _start_thread_and_run_scan(background_thread)


async def check_npm_package(npm_package):
    """
    Spawn a thread to parse the package-json.lock file and make a request to osv.dev API
    with each dependency found.
    """
    # create a thread to collect the Contents from package-lock.json file
    background_thread = threading.Thread(target=_parse_npm_pkg_dependencies, args=(npm_package,))
    await _start_thread_and_run_scan(background_thread)


async def _start_thread_and_run_scan(background_thread):
    """
    Start the background_thread and make the API requests (scan) to osv.dev with the packages
    from Queue
    """
    background_thread.start()
    await _scan_packages()
    background_thread.join()


async def _scan_packages():
    """
    Makes a request to the osv.dev API and store the results in VulnerabilityReport model.
    """
    scanned_packages = {}
    async with aiohttp.ClientSession() as session:
        for osv_data in iter(content_queue.get, None):
            data = json.dumps(osv_data)
            async with session.post(url=OSV_QUERY_URL, data=data) as response:
                response_body = await response.text()
                json_body = json.loads(response_body)
                if json_body.get("vulns"):
                    package_name = "{package}-{version}".format(
                        package=osv_data["package"]["name"],
                        version=osv_data["version"],
                    )
                    scanned_packages[package_name] = json_body["vulns"]
    vuln_report, created = await sync_to_async(VulnerabilityReport.objects.get_or_create)(
        vulns=scanned_packages, pulp_domain=get_domain()
    )
    if created:
        await CreatedResource.objects.acreate(content_object=vuln_report)


def _get_content_from_repo_version(repo_version_pk: str):
    """
    Populate content_queue Queue with the content_units found in RepositoryVersion
    """
    repo_version = RepositoryVersion.objects.get(pk=repo_version_pk)
    for content_unit in repo_version.content:
        content = content_unit.cast()
        ecosystem = _identify_package_ecosystem(content)
        osv_data = _build_osv_data(content.name, ecosystem, content.version)
        content_queue.put(osv_data)
    content_queue.put(None)  # signal that there is no more content_units


def _parse_npm_pkg_dependencies(package_lock_content):
    """
    Parse the package-lock.json file to extract the packages[name][dependencies] and
    add them to content_queue Queue

    notes:
    - we are striping the "~" and "^" from versions because osv.dev has no support to version range
    - the old/legacy packages[dependencies] field is not supported
    """
    temp_file = PulpTemporaryFile.objects.get(pk=package_lock_content)
    package_lock_content = json.loads(temp_file.file.read())
    temp_file.delete()
    for pkg in package_lock_content.get("packages", None):
        if not package_lock_content["packages"][pkg].get("dependencies", None):
            continue
        for package_name, package_version in package_lock_content["packages"][pkg][
            "dependencies"
        ].items():
            # we will not handle version range yet, for now, we will consider
            # only the specific version
            package_version = package_version.strip("^~")
            osv_data = _build_osv_data(package_name, PKG_ECOSYSTEM.npm, package_version)
            content_queue.put(osv_data)
    content_queue.put(None)  # signal that there is no more content_units


def _build_osv_data(name, ecosystem, version=None):
    """
    Helper function to build the osv.dev request data based on content object
    """
    osv_data = {"package": {"name": name, "ecosystem": ecosystem}}
    if version:
        osv_data["version"] = version
    return osv_data


def _identify_package_ecosystem(content: any) -> str:
    """
    Returns an osv.dev ecosystem (string) based on the content_type
    """
    if isinstance(content, NPMPackage):
        return getattr(PKG_ECOSYSTEM, "npm", None)
    elif content.TYPE == "python":
        return getattr(PKG_ECOSYSTEM, content.TYPE, None)
    else:
        raise RuntimeError("Package type not supported!")
