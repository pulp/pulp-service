import aiohttp
import json
import threading

from asgiref.sync import sync_to_async
from queue import Queue

from pulpcore.plugin.models import RepositoryVersion
from pulp_npm.app.models import Package as NPMPackage
from pulp_service.app.constants import PKG_ECOSYSTEM, OSV_QUERY_URL
from pulp_service.app.models import VulnerabilityReport

# Create a thread-safe queue to share Content units between threads
content_queue = Queue()


async def check_content(repo_version_pk):
    """
    Spawn a thread to retrieve the list of packages in RepoVersion(repo_version_pk) and
    make a request to osv.dev API to check the vulnerabilities.
    """
    # create a thread to collect the Contents from RepositoryVersion
    background_thread = threading.Thread(target=_get_content_from_repo, args=(repo_version_pk,))
    background_thread.start()
    await _scan_packages()
    background_thread.join()


async def _scan_packages():
    """
    Makes a request to the osv.dev API and store the results in VulnerabilityReport model.
    """
    scanned_packages = {}
    async with aiohttp.ClientSession() as session:
        for content in iter(content_queue.get, None):
            package = _build_osv_data(content)
            osv_data = json.dumps(package["osv_data"])
            async with session.post(url=OSV_QUERY_URL, data=osv_data) as response:
                response_body = await response.text()
                json_body = json.loads(response_body)
                if json_body.get("vulns"):
                    package_name = "{package}-{version}".format(
                        package=package["osv_data"]["package"]["name"],
                        version=package["osv_data"]["version"],
                    )
                    scanned_packages[package_name] = json_body["vulns"]
    await sync_to_async(VulnerabilityReport.objects.get_or_create)(
        vulns=scanned_packages,
    )


def _get_content_from_repo(repo_version_pk: str):
    """
    Populate content_queue Queue with the content_units found in RepositoryVersion
    """
    repo_version = RepositoryVersion.objects.get(pk=repo_version_pk)
    for content_unit in repo_version.content:
        content_queue.put(content_unit.cast())
    content_queue.put(None)  # signal that there is no more content_units


def _build_osv_data(content):
    """
    Helper function to build the osv.dev request data based on content object
    """
    ecosystem = _identify_package_ecosystem(content)
    return {
        "osv_data": {
            "package": {"name": content.name, "ecosystem": ecosystem},
            "version": content.version,
        },
    }


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
