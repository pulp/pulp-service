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
        """Returns debug info for a completed task with all expected fields."""
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

        # --- task section ---
        assert "task" in data
        assert data["task"]["state"] == "completed"
        assert "name" in data["task"]
        assert "logging_cid" in data["task"]
        assert "pulp_created" in data["task"]
        assert "reserved_resources_record" in data["task"]
        assert "versions" in data["task"]
        assert "domain" in data["task"]

        # --- diagnosis section ---
        assert "diagnosis" in data
        assert isinstance(data["diagnosis"], list)
        assert len(data["diagnosis"]) >= 1
        diag = data["diagnosis"][0]
        assert "bug" in diag
        assert "severity" in diag
        assert "summary" in diag
        # Completed tasks should get the "not stuck" diagnosis
        assert diag["severity"] == "info"

        # --- app_lock section ---
        assert "app_lock" in data

        # --- redis_locks section ---
        assert "redis_locks" in data
        assert "task_lock" in data["redis_locks"]
        assert "ttl" in data["redis_locks"]["task_lock"]
        assert "exclusive_resources" in data["redis_locks"]
        assert "shared_resources" in data["redis_locks"]

        # --- lock_holder_liveness section ---
        assert "lock_holder_liveness" in data
        assert isinstance(data["lock_holder_liveness"], dict)

        # --- queue_position section ---
        assert "queue_position" in data
        assert "older_waiting_tasks" in data["queue_position"]
        assert "fetch_task_limit" in data["queue_position"]
        assert "within_fetch_window" in data["queue_position"]
        assert "stuck_in_window" in data["queue_position"]

        # --- blocking_tasks section ---
        assert "blocking_tasks" in data
        assert isinstance(data["blocking_tasks"], list)

        # --- fifo_analysis section ---
        assert "fifo_analysis" in data
        assert "is_fifo_blocked" in data["fifo_analysis"]

        # --- version_compatibility section ---
        assert "version_compatibility" in data
        assert "has_version_requirements" in data["version_compatibility"]

        # --- worker_summary section ---
        assert "worker_summary" in data
        assert "online_workers" in data["worker_summary"]
        assert "online_api_processes" in data["worker_summary"]

    def test_redis_lock_ttl_fields(self, debug_api_url, admin_auth, pulpcore_bindings):
        """Verify TTL fields are present on lock info for tasks with resources."""
        tasks = pulpcore_bindings.TasksApi.list(limit=5, state="completed")
        # Find a task with reserved resources to test TTL fields
        for t in tasks.results:
            task_id = t.pulp_href.split("/")[-2]
            resp = requests.get(
                f"{debug_api_url}task-debug/",
                params={"task_id": task_id},
                auth=admin_auth,
            )
            assert resp.status_code == 200
            data = resp.json()
            # TTL field on task_lock should always be present
            assert "ttl" in data["redis_locks"]["task_lock"]
            # Exclusive resources should have ttl field
            for res in data["redis_locks"]["exclusive_resources"]:
                assert "ttl" in res
            # Shared resources should have ttl field
            for res in data["redis_locks"]["shared_resources"]:
                assert "ttl" in res
            break

    def test_blocking_tasks_include_app_lock(self, debug_api_url, admin_auth, pulpcore_bindings):
        """Blocking tasks should include app_lock info for each blocker."""
        # We can only verify the structure -- in most test environments
        # there won't be actual blocking tasks, so we just verify the
        # endpoint returns successfully and blocking_tasks is a list
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
        assert isinstance(data["blocking_tasks"], list)
        # If there happen to be blocking tasks, verify the structure
        for bt in data["blocking_tasks"]:
            assert "task_id" in bt
            assert "state" in bt
            assert "name" in bt
            assert "overlapping_resources" in bt
            assert "app_lock" in bt


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

        # New top-level fields
        assert "waiting_with_applock" in data
        assert isinstance(data["waiting_with_applock"], int)
        assert "lock_holder_liveness" in data
        assert isinstance(data["lock_holder_liveness"], dict)
        assert "queue_health" in data
        assert "waiting_with_applock" in data["queue_health"]
        assert "waiting_with_applock_is_bug1" in data["queue_health"]
        assert "worker_summary" in data
        assert "online_workers" in data["worker_summary"]
        assert "online_api_processes" in data["worker_summary"]

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
        """Each task in the queue has the expected fields including new diagnostic fields."""
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
            assert "task_lock_holder" in task["redis_locks"]
            assert "task_lock_ttl" in task["redis_locks"]
            assert "exclusive_resources_locked" in task["redis_locks"]
            assert "shared_resources_locked" in task["redis_locks"]
            assert "blocked_by" in task["redis_locks"]

            # New per-task diagnosis field
            assert "diagnoses" in task
            assert isinstance(task["diagnoses"], list)

    def test_queue_health_indicators(self, debug_api_url, admin_auth):
        """Queue health section provides actionable bug indicators."""
        resp = requests.get(f"{debug_api_url}task-queue/", auth=admin_auth)
        assert resp.status_code == 200
        data = resp.json()

        health = data["queue_health"]
        assert "waiting_with_applock" in health
        assert "waiting_with_applock_is_bug1" in health
        assert isinstance(health["waiting_with_applock"], int)
        assert isinstance(health["waiting_with_applock_is_bug1"], bool)

    def test_app_lock_includes_versions(self, debug_api_url, admin_auth):
        """App lock info includes the worker's plugin versions for version mismatch diagnosis."""
        resp = requests.get(f"{debug_api_url}task-queue/", auth=admin_auth)
        assert resp.status_code == 200
        data = resp.json()

        for task in data["tasks"]:
            al = task["app_lock"]
            if al["locked"]:
                # When a lock holder exists, versions should be present
                assert "versions" in al


class TestStaleLockScanView:
    """Tests for GET /api/pulp/debug/stale-locks/"""

    def test_basic_response_structure(self, debug_api_url, admin_auth):
        """Returns expected top-level structure with summary counts."""
        resp = requests.get(f"{debug_api_url}stale-locks/", auth=admin_auth)
        assert resp.status_code == 200
        data = resp.json()

        # --- summary section ---
        assert "summary" in data
        summary = data["summary"]
        assert "total_resource_locks" in summary
        assert "orphaned_resource_locks" in summary
        assert "healthy_resource_locks" in summary
        assert "total_task_locks" in summary
        assert "orphaned_task_locks" in summary
        assert "healthy_task_locks" in summary
        assert "unique_lock_holders" in summary
        assert "dead_lock_holders" in summary

        # Counts should be non-negative integers
        for key, value in summary.items():
            assert isinstance(value, int), f"summary[{key}] should be int, got {type(value)}"
            assert value >= 0, f"summary[{key}] should be >= 0, got {value}"

        # Total should equal orphaned + healthy
        assert summary["total_resource_locks"] == (
            summary["orphaned_resource_locks"] + summary["healthy_resource_locks"]
        )
        assert summary["total_task_locks"] == (
            summary["orphaned_task_locks"] + summary["healthy_task_locks"]
        )

        # --- orphaned locks sections ---
        assert "orphaned_resource_locks" in data
        assert isinstance(data["orphaned_resource_locks"], list)

        assert "orphaned_task_locks" in data
        assert isinstance(data["orphaned_task_locks"], list)

        # --- liveness section ---
        assert "lock_holder_liveness" in data
        assert isinstance(data["lock_holder_liveness"], dict)

        # --- task correlations ---
        assert "task_correlations" in data
        assert isinstance(data["task_correlations"], dict)

        # --- worker summary ---
        assert "worker_summary" in data
        assert "online_workers" in data["worker_summary"]
        assert "online_api_processes" in data["worker_summary"]

    def test_healthy_locks_excluded_by_default(self, debug_api_url, admin_auth):
        """Healthy locks are not included unless include_healthy=true."""
        resp = requests.get(f"{debug_api_url}stale-locks/", auth=admin_auth)
        assert resp.status_code == 200
        data = resp.json()

        # Healthy sections should NOT be present by default
        assert "healthy_resource_locks" not in data
        assert "healthy_task_locks" not in data

    def test_include_healthy_locks(self, debug_api_url, admin_auth):
        """When include_healthy=true, healthy lock lists are included."""
        resp = requests.get(
            f"{debug_api_url}stale-locks/",
            params={"include_healthy": "true"},
            auth=admin_auth,
        )
        assert resp.status_code == 200
        data = resp.json()

        # Healthy sections SHOULD be present
        assert "healthy_resource_locks" in data
        assert isinstance(data["healthy_resource_locks"], list)
        assert "healthy_task_locks" in data
        assert isinstance(data["healthy_task_locks"], list)

    def test_resource_lock_structure(self, debug_api_url, admin_auth):
        """Each resource lock entry has the expected fields."""
        resp = requests.get(
            f"{debug_api_url}stale-locks/",
            params={"include_healthy": "true"},
            auth=admin_auth,
        )
        assert resp.status_code == 200
        data = resp.json()

        all_resource_locks = (
            data["orphaned_resource_locks"]
            + data.get("healthy_resource_locks", [])
        )
        for lock_info in all_resource_locks:
            assert "lock_key" in lock_info
            assert "resource" in lock_info
            assert "lock_type" in lock_info
            assert lock_info["lock_type"] in ("string", "set", "none")
            assert "holders" in lock_info
            assert isinstance(lock_info["holders"], list)
            assert "ttl" in lock_info

    def test_task_lock_structure(self, debug_api_url, admin_auth):
        """Each task lock entry has the expected fields."""
        resp = requests.get(
            f"{debug_api_url}stale-locks/",
            params={"include_healthy": "true"},
            auth=admin_auth,
        )
        assert resp.status_code == 200
        data = resp.json()

        all_task_locks = (
            data["orphaned_task_locks"]
            + data.get("healthy_task_locks", [])
        )
        for lock_info in all_task_locks:
            assert "lock_key" in lock_info
            assert "task_id" in lock_info
            assert "lock_type" in lock_info
            assert "holder" in lock_info
            assert "ttl" in lock_info

    def test_orphaned_resource_lock_has_orphaned_holders(self, debug_api_url, admin_auth):
        """Orphaned resource locks include the orphaned_holders and healthy_holders breakdown."""
        resp = requests.get(f"{debug_api_url}stale-locks/", auth=admin_auth)
        assert resp.status_code == 200
        data = resp.json()

        for lock_info in data["orphaned_resource_locks"]:
            assert "orphaned_holders" in lock_info
            assert isinstance(lock_info["orphaned_holders"], list)
            assert len(lock_info["orphaned_holders"]) > 0
            assert "healthy_holders" in lock_info
            assert isinstance(lock_info["healthy_holders"], list)

    def test_orphaned_task_lock_has_task_state(self, debug_api_url, admin_auth):
        """Orphaned task locks include task_state from the database."""
        resp = requests.get(f"{debug_api_url}stale-locks/", auth=admin_auth)
        assert resp.status_code == 200
        data = resp.json()

        for lock_info in data["orphaned_task_locks"]:
            assert "task_state" in lock_info
            assert "task_name" in lock_info
            assert "task_app_lock" in lock_info

    def test_lock_holder_liveness_structure(self, debug_api_url, admin_auth):
        """Each entry in lock_holder_liveness has the expected fields."""
        resp = requests.get(
            f"{debug_api_url}stale-locks/",
            params={"include_healthy": "true"},
            auth=admin_auth,
        )
        assert resp.status_code == 200
        data = resp.json()

        for holder_name, info in data["lock_holder_liveness"].items():
            assert "exists_in_db" in info
            assert "online" in info
            assert "app_type" in info
            assert "last_heartbeat" in info
            assert "verdict" in info

    def test_task_correlations_structure(self, debug_api_url, admin_auth):
        """Task correlations map resource names to task summary lists."""
        resp = requests.get(f"{debug_api_url}stale-locks/", auth=admin_auth)
        assert resp.status_code == 200
        data = resp.json()

        for resource_name, task_list in data["task_correlations"].items():
            assert isinstance(resource_name, str)
            assert isinstance(task_list, list)
            for task_summary in task_list:
                assert "task_id" in task_summary
                assert "state" in task_summary
                assert "name" in task_summary
                assert "pulp_created" in task_summary
                assert "app_lock" in task_summary
