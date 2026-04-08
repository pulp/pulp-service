import subprocess
from uuid import uuid4

import pytest

from pulpcore.client.pulp_service import ServicePyPIYankMonitor
from pulpcore.client.pulp_service.exceptions import ApiException


def _django_admin_shell(code):
    subprocess.run(["django-admin", "shell", "-c", code], check=True)


def test_pypi_yank_monitor_crud(pypi_yank_monitor_api, python_repo_factory, add_to_cleanup):
    repo = python_repo_factory()

    # Create
    monitor = pypi_yank_monitor_api.create(
        ServicePyPIYankMonitor(name=str(uuid4()), repository=repo.pulp_href)
    )
    add_to_cleanup(pypi_yank_monitor_api, monitor.pulp_href)
    assert monitor.repository == repo.pulp_href
    assert monitor.repository_version is None

    # List
    result = pypi_yank_monitor_api.list()
    assert any(m.pulp_href == monitor.pulp_href for m in result.results)

    # Retrieve
    retrieved = pypi_yank_monitor_api.read(
        service_py_p_i_yank_monitor_href=monitor.pulp_href
    )
    assert retrieved.repository == repo.pulp_href

    # Delete
    pypi_yank_monitor_api.delete(
        service_py_p_i_yank_monitor_href=monitor.pulp_href
    )
    result = pypi_yank_monitor_api.list()
    assert not any(m.pulp_href == monitor.pulp_href for m in result.results)


def test_pypi_yank_monitor_create_with_repo_version(
    pypi_yank_monitor_api, python_repo_factory, python_bindings, add_to_cleanup
):
    repo = python_repo_factory()
    version_href = python_bindings.RepositoriesPythonApi.read(
        python_python_repository_href=repo.pulp_href
    ).latest_version_href

    monitor = pypi_yank_monitor_api.create(
        ServicePyPIYankMonitor(name=str(uuid4()), repository_version=version_href)
    )
    add_to_cleanup(pypi_yank_monitor_api, monitor.pulp_href)
    assert monitor.repository_version == version_href
    assert monitor.repository is None


def test_pypi_yank_monitor_validation_neither(pypi_yank_monitor_api):
    with pytest.raises(ApiException) as exc_info:
        pypi_yank_monitor_api.create(ServicePyPIYankMonitor(name=str(uuid4())))
    assert exc_info.value.status == 400


def test_pypi_yank_monitor_validation_non_python_repo(
    pypi_yank_monitor_api, file_bindings, gen_object_with_cleanup
):
    file_repo = gen_object_with_cleanup(
        file_bindings.RepositoriesFileApi, {"name": str(uuid4())}
    )
    with pytest.raises(ApiException) as exc_info:
        pypi_yank_monitor_api.create(
            ServicePyPIYankMonitor(name=str(uuid4()), repository=file_repo.pulp_href)
        )
    assert exc_info.value.status == 400


def test_pypi_yank_monitor_report_not_found(
    pypi_yank_monitor_api, python_repo_factory, add_to_cleanup
):
    repo = python_repo_factory()
    monitor = pypi_yank_monitor_api.create(
        ServicePyPIYankMonitor(name=str(uuid4()), repository=repo.pulp_href)
    )
    add_to_cleanup(pypi_yank_monitor_api, monitor.pulp_href)

    with pytest.raises(ApiException) as exc_info:
        pypi_yank_monitor_api.report(
            service_py_p_i_yank_monitor_href=monitor.pulp_href
        )
    assert exc_info.value.status == 404


_CREATE_YANKED_PKG_CODE = "\n".join([
    "import hashlib",
    "from pulp_python.app.models import PythonPackageContent",
    "sha = hashlib.sha256(b'attrs-21.1.0-py2.py3-none-any.whl').hexdigest()",
    "pkg, _ = PythonPackageContent.objects.get_or_create(",
    "    sha256=sha,",
    "    defaults=dict(name='attrs', version='21.1.0',",
    "        filename='attrs-21.1.0-py2.py3-none-any.whl', packagetype='bdist_wheel'))",
    "print(f'/pulp/api/v3/content/python/packages/{pkg.pk}/')",
])

_DELETE_YANKED_PKG_CODE = (
    "import hashlib\n"
    "from pulp_python.app.models import PythonPackageContent\n"
    "sha = hashlib.sha256(b'attrs-21.1.0-py2.py3-none-any.whl').hexdigest()\n"
    "PythonPackageContent.objects.filter(sha256=sha).delete()"
)


def test_pypi_yank_monitor_scoped_check(
    pypi_yank_monitor_api, python_repo_factory, python_bindings, monitor_task, add_to_cleanup
):
    """
    Verify that check_packages_for_monitor runs a yank check scoped to a
    specific repository and stores the result with the correct repository_name.

    Package content is created via django-admin shell because yanked packages
    cannot be uploaded through the Pulp API. The repository version is created
    via the REST API modify endpoint.
    """
    from pulpcore.client.pulp_python import RepositoryAddRemoveContent

    repo = python_repo_factory()

    result = subprocess.run(
        ["django-admin", "shell", "-c", _CREATE_YANKED_PKG_CODE],
        check=True, capture_output=True, text=True,
    )
    pkg_href = result.stdout.strip()
    try:
        # Add the package to the repo via the REST API (creates a new repo version)
        modify_response = python_bindings.RepositoriesPythonApi.modify(
            python_python_repository_href=repo.pulp_href,
            repository_add_remove_content=RepositoryAddRemoveContent(
                add_content_units=[pkg_href]
            ),
        )
        monitor_task(modify_response.task)

        monitor = pypi_yank_monitor_api.create(
            ServicePyPIYankMonitor(name=str(uuid4()), repository=repo.pulp_href)
        )
        add_to_cleanup(pypi_yank_monitor_api, monitor.pulp_href)

        check_response = pypi_yank_monitor_api.check(
            service_py_p_i_yank_monitor_href=monitor.pulp_href
        )
        monitor_task(check_response.task)

        report = pypi_yank_monitor_api.report(
            service_py_p_i_yank_monitor_href=monitor.pulp_href
        )
        assert report.repository_name == repo.name
        assert "attrs==21.1.0" in report.report
        assert "attrs==23.1.0" not in report.report

        updated_monitor = pypi_yank_monitor_api.read(
            service_py_p_i_yank_monitor_href=monitor.pulp_href
        )
        assert updated_monitor.last_checked is not None
    finally:
        # Delete the repo via REST API first so RepositoryContent associations are removed,
        # then delete the package content (RepositoryContent has a PROTECTED FK to content).
        delete_response = python_bindings.RepositoriesPythonApi.delete(
            python_python_repository_href=repo.pulp_href
        )
        monitor_task(delete_response.task)
        _django_admin_shell(_DELETE_YANKED_PKG_CODE)


def test_pypi_yank_monitor_scoped_check_repo_version(
    pypi_yank_monitor_api, python_repo_factory, python_bindings, monitor_task, add_to_cleanup
):
    """
    Verify that check_packages_for_monitor works when the monitor is pinned to a
    specific repository version rather than the repository itself.
    """
    from pulpcore.client.pulp_python import RepositoryAddRemoveContent

    repo = python_repo_factory()

    result = subprocess.run(
        ["django-admin", "shell", "-c", _CREATE_YANKED_PKG_CODE],
        check=True, capture_output=True, text=True,
    )
    pkg_href = result.stdout.strip()
    try:
        modify_response = python_bindings.RepositoriesPythonApi.modify(
            python_python_repository_href=repo.pulp_href,
            repository_add_remove_content=RepositoryAddRemoveContent(
                add_content_units=[pkg_href]
            ),
        )
        monitor_task(modify_response.task)

        version_href = python_bindings.RepositoriesPythonApi.read(
            python_python_repository_href=repo.pulp_href
        ).latest_version_href

        monitor = pypi_yank_monitor_api.create(
            ServicePyPIYankMonitor(name=str(uuid4()), repository_version=version_href)
        )
        add_to_cleanup(pypi_yank_monitor_api, monitor.pulp_href)

        check_response = pypi_yank_monitor_api.check(
            service_py_p_i_yank_monitor_href=monitor.pulp_href
        )
        monitor_task(check_response.task)

        report = pypi_yank_monitor_api.report(
            service_py_p_i_yank_monitor_href=monitor.pulp_href
        )
        assert report.repository_name == repo.name
        assert "attrs==21.1.0" in report.report

        updated_monitor = pypi_yank_monitor_api.read(
            service_py_p_i_yank_monitor_href=monitor.pulp_href
        )
        assert updated_monitor.last_checked is not None
    finally:
        delete_response = python_bindings.RepositoriesPythonApi.delete(
            python_python_repository_href=repo.pulp_href
        )
        monitor_task(delete_response.task)
        _django_admin_shell(_DELETE_YANKED_PKG_CODE)
