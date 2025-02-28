import aiohttp
import logging
import json

from asgiref.sync import sync_to_async

from pulpcore.app.models import Content, RepositoryVersion
from pulp_rpm.app.models.package import Package as RPMPackage
from pulp_npm.app.models import Package as NPMPackage

from pulp_service.app.constants import (
    RH_REPO_TO_CPE_URL,
    PKG_ECOSYSTEM,
    OSV_QUERY_URL,
)
from pulp_service.app.models import ArtifactVulnerability

_logger = logging.getLogger(__name__)
cache_rh_cpe = {}


async def check_content(repo_version_pk):
    """
    Get the list of contents from reop_version, build a package_list and makes an API request to
    osv.dev using this package_list
    """
    contents = await sync_to_async(_get_content_from_repo)(repo_version_pk)
    osv_packages = await sync_to_async(_define_osv_package_list)(contents)
    if not osv_packages:
        return
    await _scan_packages(osv_packages)


async def _scan_packages(packages):
    """
    Makes a request to the osv.dev API and store the results in ArtifactVulnerability model
    """
    async with aiohttp.ClientSession() as session:
        for package in packages:
            osv_data = json.dumps(package["osv_data"])
            async with session.post(url=OSV_QUERY_URL, data=osv_data) as response:
                response_body = await response.text()
                json_body = json.loads(response_body)
                if json_body.get("vulns"):
                    await sync_to_async(ArtifactVulnerability.objects.get_or_create)(
                        vulns=json_body["vulns"],
                    )


def _get_content_from_repo(repo_version_pk: str) -> list[any]:
    """
    Return the list of contents in the repository_version
    """
    repo_version = RepositoryVersion.objects.get(pk=repo_version_pk)
    return Content.objects.filter(pk__in=repo_version.content)


def _define_osv_package_list(content_types: list[any]) -> list[dict]:
    """
    Build a list of dictionaries from contents following the osv.dev expected format:
    { "package": {"name": "<package name>", "ecosystem": "<ecosystem>" }, "version": "<version>" }
    """
    if not content_types:
        return

    package_list = []
    for content in content_types:
        content = content.cast()
        ecosystem = _identify_package_ecosystem(content)
        if not ecosystem:
            break
        package_list.append(
            {
                "osv_data": {
                    "package": {"name": content.name, "ecosystem": ecosystem},
                    "version": content.version,
                },
                "content": content,
            }
        )
    return package_list


def _identify_package_ecosystem(content: any) -> str:
    """
    Returns an osv.dev ecosystem (string) based on the content_type
    """
    model_type = content.TYPE
    # if isinstance(content, RPMPackage):
    #    model_type = 'rpm'
    #    await _identify_package_ecosystem()
    if isinstance(content, NPMPackage):
        model_type = "npm"
    return getattr(PKG_ECOSYSTEM, model_type, None)


async def _identify_rh_cpe():
    async with aiohttp.ClientSession() as session:
        async with session.post(url=RH_REPO_TO_CPE_URL) as response:
            response_body = await response.text()
            json_body = json.loads(response_body)
            global cache_rh_cpe
            cache_rh_cpe = json_body["data"]
            _logger.info(f"RESPONSE JSON: {json.dumps(cache_rh_cpe, indent=2)}")
            # return json_body
