import hashlib
import subprocess

from pulp_service.tests.functional.constants import NOT_YANKED_PACKAGES, YANKED_PACKAGES

ALL_TEST_PACKAGES = YANKED_PACKAGES + NOT_YANKED_PACKAGES


def _django_admin_shell(code):
    subprocess.run(["django-admin", "shell", "-c", code], check=True)


def _create_packages():
    lines = [
        "import hashlib",
        "from pulp_python.app.models import PythonPackageContent",
    ]
    for pkg in ALL_TEST_PACKAGES:
        sha256 = hashlib.sha256(pkg["filename"].encode()).hexdigest()
        lines.append(
            f"PythonPackageContent.objects.create("
            f"name={pkg['name']!r}, version={pkg['version']!r}, "
            f"filename={pkg['filename']!r}, packagetype={pkg['packagetype']!r}, "
            f"sha256={sha256!r})"
        )
    _django_admin_shell("\n".join(lines))


def _delete_packages():
    names = [pkg["name"] for pkg in ALL_TEST_PACKAGES]
    versions = [pkg["version"] for pkg in ALL_TEST_PACKAGES]
    code = (
        "from pulp_python.app.models import PythonPackageContent\n"
        f"PythonPackageContent.objects.filter(name__in={names!r}, version__in={versions!r}).delete()"
    )
    _django_admin_shell(code)


def test_pypi_yank_check(pypi_yank_report_api, monitor_task, add_to_cleanup):
    """
    Verify that check_python_packages_for_yanked detects packages stored in Pulp
    that have been yanked from PyPI, and does not include packages that have not
    been yanked.

    PythonPackageContent records are created via django-admin shell because
    yanked packages cannot be uploaded through the Pulp API.
    """
    _create_packages()
    try:
        response = pypi_yank_report_api.create()
        task = monitor_task(response.task)
        report_href = task.created_resources[0]
        add_to_cleanup(pypi_yank_report_api, report_href)

        report = pypi_yank_report_api.read(service_yanked_package_report_href=report_href)

        for pkg in YANKED_PACKAGES:
            key = f"{pkg['name']}=={pkg['version']}"
            assert key in report.report, f"Expected {key} to be in the yanked package report"

        for pkg in NOT_YANKED_PACKAGES:
            key = f"{pkg['name']}=={pkg['version']}"
            assert key not in report.report, f"Expected {key} to not be in the yanked package report"
    finally:
        _delete_packages()
