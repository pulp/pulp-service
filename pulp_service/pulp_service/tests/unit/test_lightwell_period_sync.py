"""
Unit tests for the Lightwell scheduled sync task.

These tests mock the ORM and pulpcore's dispatch/with_domain so they run without
a live Pulp stack. Their main purpose is to pin the domain-awareness fix: the
scheduled task looks up the repository/remote in the "lightwell" domain, but it
is fired by the scheduler with the *ambient* (default) domain active. Unless the
task dispatches the sync within the repository's domain context, the sync -- and
every content unit and artifact it creates -- is stamped with the default domain
and written to the default domain's storage backend instead of the repository's.
That is invisible locally (shared filesystem storage) but breaks in production,
where each domain has its own S3 backend.
"""

from unittest.mock import MagicMock, patch

import pytest

MODULE = "pulp_service.app.tasks.lightwell_period_sync"


class _ModelDoesNotExistError(Exception):
    """Stand-in for Model.DoesNotExist so `except`/`raises` works with a mocked model."""


def _make_repository(domain):
    repository = MagicMock(name="PythonRepository")
    repository.pk = "repo-pk-123"
    repository.pulp_domain = domain
    return repository


def _wire_lookups(mock_repo_model, mock_remote_model, repository, remote_pk="remote-pk-456"):
    mock_repo_model.objects.get.return_value = repository
    mock_remote_model.objects.values_list.return_value.get.return_value = remote_pk


@patch(f"{MODULE}.dispatch")
@patch(f"{MODULE}.with_domain")
@patch(f"{MODULE}.PythonRemote")
@patch(f"{MODULE}.PythonRepository")
def test_sync_dispatched_in_repository_domain(mock_repo_model, mock_remote_model, mock_with_domain, mock_dispatch):
    """The fix: the sync must be dispatched within the repository's domain context.

    This is the regression guard. It asserts the domain context is entered with
    the repository's own domain, that ``dispatch`` runs *inside* that context,
    and that the context is exited afterwards -- i.e. it does not leak past
    ``dispatch()``.
    """
    from pulp_service.app.tasks import lightwell_period_sync

    lightwell_domain = MagicMock(name="lightwell-domain")
    repository = _make_repository(lightwell_domain)
    _wire_lookups(mock_repo_model, mock_remote_model, repository)

    # Record the order of context enter/exit relative to the dispatch call.
    order = []
    mock_with_domain.return_value.__enter__.side_effect = lambda: order.append("enter_domain")
    mock_with_domain.return_value.__exit__.side_effect = lambda *_args: order.append("exit_domain")
    mock_dispatch.side_effect = lambda *_args, **_kwargs: order.append("dispatch")

    lightwell_period_sync.python_repository_sync()

    # Domain context scoped to the repository's own domain...
    mock_with_domain.assert_called_once_with(lightwell_domain)
    # ...dispatch runs inside it, and the context is exited afterwards (no leak).
    assert order == ["enter_domain", "dispatch", "exit_domain"]


@patch(f"{MODULE}.dispatch")
@patch(f"{MODULE}.with_domain")
@patch(f"{MODULE}.PythonRemote")
@patch(f"{MODULE}.PythonRepository")
def test_dispatches_additive_python_sync(mock_repo_model, mock_remote_model, _mock_with_domain, mock_dispatch):
    """Dispatch targets the pulp_python sync for the repo/remote, additive (mirror=False)."""
    from pulp_service.app.tasks import lightwell_period_sync

    repository = _make_repository(MagicMock(name="lightwell-domain"))
    _wire_lookups(mock_repo_model, mock_remote_model, repository)

    lightwell_period_sync.python_repository_sync()

    mock_dispatch.assert_called_once()
    args, kwargs = mock_dispatch.call_args
    assert args[0] == "pulp_python.app.tasks.sync.sync"
    assert kwargs["exclusive_resources"] == [repository]
    assert kwargs["kwargs"] == {
        "remote_pk": "remote-pk-456",
        "repository_pk": "repo-pk-123",
        "mirror": False,
    }


@patch(f"{MODULE}.dispatch")
@patch(f"{MODULE}.with_domain")
@patch(f"{MODULE}.PythonRemote")
@patch(f"{MODULE}.PythonRepository")
def test_looks_up_configured_resource_names(mock_repo_model, mock_remote_model, _mock_with_domain, _mock_dispatch):
    """The repo/remote are looked up by their exact configured names and domain.

    The lookups are mocked, so without this a typo in any of the hardcoded names
    would still pass here while silently no-op'ing (or failing) in production.
    """
    from pulp_service.app.tasks import lightwell_period_sync

    repository = _make_repository(MagicMock(name="lightwell-domain"))
    _wire_lookups(mock_repo_model, mock_remote_model, repository)

    lightwell_period_sync.python_repository_sync()

    mock_repo_model.objects.get.assert_called_once_with(
        name="network-python-validated-landing",
        pulp_domain__name="lightwell",
    )
    mock_remote_model.objects.values_list.return_value.get.assert_called_once_with(
        name="trusted-libraries",
        pulp_domain__name="lightwell",
    )


@patch(f"{MODULE}.dispatch")
@patch(f"{MODULE}.with_domain")
@patch(f"{MODULE}.PythonRemote")
@patch(f"{MODULE}.PythonRepository")
def test_noop_when_repository_missing(mock_repo_model, _mock_remote_model, mock_with_domain, mock_dispatch):
    """If the repository does not exist: no domain switch and no dispatch."""
    from pulp_service.app.tasks import lightwell_period_sync

    mock_repo_model.DoesNotExist = _ModelDoesNotExistError
    mock_repo_model.objects.get.side_effect = _ModelDoesNotExistError

    lightwell_period_sync.python_repository_sync()

    mock_dispatch.assert_not_called()
    mock_with_domain.assert_not_called()


@patch(f"{MODULE}.dispatch")
@patch(f"{MODULE}.with_domain")
@patch(f"{MODULE}.PythonRemote")
@patch(f"{MODULE}.PythonRepository")
def test_raises_when_remote_missing(mock_repo_model, mock_remote_model, mock_with_domain, mock_dispatch):
    """Documents current behavior: unlike the repo, a missing remote is not guarded.

    The remote lookup is not wrapped in try/except, so a missing/misnamed remote
    raises and fails the task rather than skipping. Nothing is dispatched.
    """
    from pulp_service.app.tasks import lightwell_period_sync

    mock_repo_model.objects.get.return_value = _make_repository(MagicMock(name="lightwell-domain"))
    mock_remote_model.objects.values_list.return_value.get.side_effect = _ModelDoesNotExistError

    with pytest.raises(_ModelDoesNotExistError):
        lightwell_period_sync.python_repository_sync()

    mock_dispatch.assert_not_called()
    mock_with_domain.assert_not_called()
