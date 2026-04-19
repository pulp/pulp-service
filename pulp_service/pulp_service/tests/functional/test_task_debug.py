"""Tests for the task debug and task queue API endpoints."""

import pytest
import requests

from urllib.parse import urljoin


@pytest.fixture
def debug_api_url(bindings_cfg):
    """Base URL for debug endpoints."""
    return urljoin(bindings_cfg.host, "/api/pulp/debug/")


@pytest.fixture
def admin_auth(bindings_cfg):
    """Basic auth tuple for admin requests."""
    return (bindings_cfg.username, bindings_cfg.password)


@pytest.fixture
def waiting_task(pulpcore_bindings, file_bindings, gen_object_with_cleanup):
    """Create a file remote so we can trigger a sync task that stays in waiting."""
    remote = gen_object_with_cleanup(
        file_bindings.RemotesFileApi,
        {"name": "test-debug-remote", "url": "https://fixtures.pulpproject.org/file/PULP_MANIFEST"},
    )
    repo = gen_object_with_cleanup(
        file_bindings.RepositoriesFileApi,
        {"name": "test-debug-repo", "remote": remote.pulp_href},
    )
    task = file_bindings.RepositoriesFileApi.sync(repo.pulp_href, {})
    return task


class TestTaskDebugView:
    """Tests for GET /api/pulp/debug/task-debug/"""

    def test_missing_task_id(self, debug_api_url, admin_auth):
        """Returns 400 when task_id is not provided."""
        resp = requests.get(f"{debug_api_url}task-debug/", auth=admin_auth)
        assert resp.status_code == 400
        assert "task_id" in resp.json()["error"]

    def test_nonexistent_task(self, debug_api_url, admin_auth):
        """Returns 404 for a task that doesn't exist."""
        resp = requests.get(
            f"{debug_api_url}task-debug/",
            params={"task_id": "00000000-0000-0000-0000-000000000000"},
            auth=admin_auth,
        )
        assert resp.status_code == 404

    def test_completed_task(self, debug_api_url, admin_auth, pulpcore_bindings, monitor_task):
        """Returns debug info for a completed task."""
        # Dispatch a trivial task and wait for completion
        from pulpcore.client.pulpcore import ApiException

        tasks = pulpcore_bindings.TasksApi.list(limit=1, state="completed")
        assert tasks.count >= 1
        task = tasks.results[0]
        task_id = task.pulp_href.split("/")[-2]

        resp = requests.get(
            f"{debug_api_url}task-debug/",
            params={"task_id": task_id},
            auth=admin_auth,
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["task_id"] == task_id
        assert "task" in data
        assert data["task"]["state"] == "completed"
        assert "name" in data["task"]
        assert "logging_cid" in data["task"]
        assert "pulp_created" in data["task"]
        assert "reserved_resources_record" in data["task"]
        assert "versions" in data["task"]
        assert "domain" in data["task"]

        assert "app_lock" in data
        assert "redis_locks" in data
        assert "task_lock" in data["redis_locks"]
        assert "exclusive_resources" in data["redis_locks"]
        assert "shared_resources" in data["redis_locks"]

        assert "queue_position" in data
        assert "older_waiting_tasks" in data["queue_position"]
        assert "fetch_task_limit" in data["queue_position"]
        assert "within_fetch_window" in data["queue_position"]

        assert "blocking_tasks" in data
        assert isinstance(data["blocking_tasks"], list)


class TestTaskQueueView:
    """Tests for GET /api/pulp/debug/task-queue/"""

    def test_default_limit(self, debug_api_url, admin_auth):
        """Returns task queue with default limit of 10."""
        resp = requests.get(f"{debug_api_url}task-queue/", auth=admin_auth)
        assert resp.status_code == 200
        data = resp.json()

        assert "total_waiting" in data
        assert "total_running" in data
        assert data["limit"] == 10
        assert "tasks" in data
        assert isinstance(data["tasks"], list)
        assert len(data["tasks"]) <= 10

    def test_custom_limit(self, debug_api_url, admin_auth):
        """Respects the limit query parameter."""
        resp = requests.get(
            f"{debug_api_url}task-queue/",
            params={"limit": 3},
            auth=admin_auth,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 3
        assert len(data["tasks"]) <= 3

    def test_limit_clamped_to_max(self, debug_api_url, admin_auth):
        """Limit is clamped to 100 maximum."""
        resp = requests.get(
            f"{debug_api_url}task-queue/",
            params={"limit": 500},
            auth=admin_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["limit"] == 100

    def test_limit_clamped_to_min(self, debug_api_url, admin_auth):
        """Limit is clamped to 1 minimum."""
        resp = requests.get(
            f"{debug_api_url}task-queue/",
            params={"limit": 0},
            auth=admin_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["limit"] == 1

    def test_invalid_limit(self, debug_api_url, admin_auth):
        """Invalid limit falls back to default."""
        resp = requests.get(
            f"{debug_api_url}task-queue/",
            params={"limit": "abc"},
            auth=admin_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["limit"] == 10

    def test_task_structure(self, debug_api_url, admin_auth):
        """Each task in the queue has the expected fields."""
        resp = requests.get(
            f"{debug_api_url}task-queue/",
            params={"limit": 1},
            auth=admin_auth,
        )
        assert resp.status_code == 200
        data = resp.json()

        if data["tasks"]:
            task = data["tasks"][0]
            assert "task_id" in task
            assert "state" in task
            assert task["state"] in ("waiting", "running")
            assert "name" in task
            assert "logging_cid" in task
            assert "pulp_created" in task
            assert "domain" in task
            assert "versions" in task
            assert "reserved_resources_record" in task
            assert "app_lock" in task
            assert "redis_locks" in task
            assert "task_lock_held" in task["redis_locks"]
            assert "exclusive_resources_locked" in task["redis_locks"]
            assert "shared_resources_locked" in task["redis_locks"]
            assert "blocked_by" in task["redis_locks"]
