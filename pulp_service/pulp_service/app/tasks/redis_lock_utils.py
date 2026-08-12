"""
Shared utilities for scanning and inspecting Redis locks used by Pulp's
task system.

Functions in this module are consumed by both the ``StaleLockScanView``
(paginated, interactive) and the ``cleanup_stale_locks`` background task
(full scan, automated).
"""

import logging
from datetime import timedelta

from django.utils import timezone

_logger = logging.getLogger(__name__)

TASK_TERMINAL_STATES = {"completed", "failed", "canceled", "skipped"}


def scan_resource_locks(redis_conn, cursor=0, max_keys=None):
    """
    Scan Redis for resource lock keys (``pulp:resource_lock:*``).

    Uses ``SCAN`` to iterate without blocking Redis.  For each key the
    lock type (``string`` = exclusive, ``set`` = shared) and holder(s) are
    extracted.

    Args:
        redis_conn: An active Redis connection.
        cursor: Redis SCAN cursor to resume from (default ``0`` = start).
        max_keys: Maximum number of keys to return before yielding control.
            When ``None`` the full keyspace is scanned (suitable for
            background tasks).

    Returns:
        A ``(locks, next_cursor)`` tuple.  ``next_cursor`` is ``0`` when the
        scan is complete; otherwise callers should pass it back to resume.
    """
    from pulpcore.tasking.redis_locks import REDIS_LOCK_PREFIX

    locks = []
    pattern = f"{REDIS_LOCK_PREFIX}*"

    while True:
        cursor, keys = redis_conn.scan(cursor=cursor, match=pattern, count=200)
        for key in keys:
            key_str = key.decode("utf-8") if isinstance(key, bytes) else key
            resource_name = key_str[len(REDIS_LOCK_PREFIX) :]

            lock_type = redis_conn.type(key)
            if isinstance(lock_type, bytes):
                lock_type = lock_type.decode("utf-8")

            ttl = redis_conn.ttl(key)
            holders = []

            if lock_type == "string":
                val = redis_conn.get(key)
                if val:
                    holders = [val.decode("utf-8")]
            elif lock_type == "set":
                members = redis_conn.smembers(key)
                holders = sorted(m.decode("utf-8") for m in members)

            locks.append(
                {
                    "lock_key": key_str,
                    "resource": resource_name,
                    "lock_type": lock_type,
                    "holders": holders,
                    "ttl": ttl,
                }
            )

        if max_keys is not None and len(locks) >= max_keys:
            # Return early with the current cursor so the caller can resume.
            return locks[:max_keys], cursor

        if cursor == 0:
            break

    return locks, 0


def scan_task_locks(redis_conn, cursor=0, max_keys=None):
    """
    Scan Redis for task lock keys (``task:*``).

    Args:
        redis_conn: An active Redis connection.
        cursor: Redis SCAN cursor to resume from (default ``0`` = start).
        max_keys: Maximum number of keys to return before yielding control.
            When ``None`` the full keyspace is scanned.

    Returns:
        A ``(locks, next_cursor)`` tuple.
    """
    locks = []
    pattern = "task:*"

    while True:
        cursor, keys = redis_conn.scan(cursor=cursor, match=pattern, count=200)
        for key in keys:
            key_str = key.decode("utf-8") if isinstance(key, bytes) else key
            task_id = key_str[5:]  # Strip "task:" prefix

            lock_type = redis_conn.type(key)
            if isinstance(lock_type, bytes):
                lock_type = lock_type.decode("utf-8")

            ttl = redis_conn.ttl(key)
            holder = None

            if lock_type == "string":
                val = redis_conn.get(key)
                if val:
                    holder = val.decode("utf-8")

            locks.append(
                {
                    "lock_key": key_str,
                    "task_id": task_id,
                    "lock_type": lock_type,
                    "holder": holder,
                    "ttl": ttl,
                }
            )

        if max_keys is not None and len(locks) >= max_keys:
            return locks[:max_keys], cursor

        if cursor == 0:
            break

    return locks, 0


def check_lock_holder_liveness(lock_holders):
    """
    Check whether each lock holder (worker / API process) is still alive.

    Detects orphaned locks from dead processes that were never cleaned up
    (Bug 2 in the stuck-task taxonomy).

    Args:
        lock_holders: An iterable of holder name strings.

    Returns:
        A dict mapping each holder name to a liveness info dict containing
        ``exists_in_db``, ``online``, ``app_type``, ``last_heartbeat``, and
        ``verdict`` keys.
    """
    from pulpcore.app.models import AppStatus

    if not lock_holders:
        return {}

    app_statuses = {app.name: app for app in AppStatus.objects.filter(name__in=lock_holders)}

    result = {}
    for holder_name in lock_holders:
        app = app_statuses.get(holder_name)
        if app is None:
            result[holder_name] = {
                "exists_in_db": False,
                "online": False,
                "app_type": None,
                "last_heartbeat": None,
                "verdict": "DEAD: no AppStatus record exists, lock is orphaned",
            }
        else:
            result[holder_name] = {
                "exists_in_db": True,
                "online": app.online,
                "app_type": app.app_type,
                "last_heartbeat": (app.last_heartbeat.isoformat() if app.last_heartbeat else None),
                "verdict": ("alive" if app.online else "DEAD: AppStatus exists but is not online, lock is orphaned"),
            }
    return result


def detect_abandoned_task_locks(task_locks, min_age_seconds=60):
    """
    Identify task locks that are abandoned despite the holder being alive.

    A task lock is abandoned if:
    - The task is ``waiting`` with no ``app_lock`` (worker acquired the lock
      but failed to claim the task via DB, then moved on).
    - The task is in a terminal state (lock was not released after completion).
    - The task no longer exists in the database (purged).

    A minimum age threshold avoids a race with the normal acquire-to-claim
    window in ``fetch_task()``.

    Args:
        task_locks: List of task lock dicts from ``scan_task_locks()``.
        min_age_seconds: Ignore locks for tasks created less than this many
            seconds ago (default 60).

    Returns:
        A list of abandoned task lock dicts, each augmented with
        ``task_state`` and ``reason`` keys.
    """
    from pulpcore.app.models import Task

    if not task_locks:
        return []

    task_ids = [lock["task_id"] for lock in task_locks]
    tasks_by_id = {str(t.pk): t for t in Task.objects.filter(pk__in=task_ids).select_related("app_lock")}

    age_cutoff = timezone.now() - timedelta(seconds=min_age_seconds)
    abandoned = []

    for lock_info in task_locks:
        task_id = lock_info["task_id"]
        task = tasks_by_id.get(task_id)

        if task is None:
            entry = dict(lock_info)
            entry["task_state"] = "NOT_FOUND"
            entry["reason"] = "Task does not exist in database; lock is orphaned"
            abandoned.append(entry)
            continue

        if task.pulp_created > age_cutoff:
            continue

        if task.state == "waiting" and task.app_lock is None:
            entry = dict(lock_info)
            entry["task_state"] = task.state
            entry["reason"] = (
                "Task is waiting with no app_lock; worker acquired the Redis lock but did not claim the task"
            )
            abandoned.append(entry)
        elif task.state in TASK_TERMINAL_STATES:
            entry = dict(lock_info)
            entry["task_state"] = task.state
            entry["reason"] = f"Task is in terminal state '{task.state}' but Redis lock was not released"
            abandoned.append(entry)

    return abandoned


def detect_abandoned_resource_locks(abandoned_task_locks, resource_locks, redis_conn):
    """
    Identify resource locks that correspond to abandoned tasks.

    Starts from abandoned task locks, looks up each task's
    ``reserved_resources_record``, and checks whether the matching Redis
    resource lock is still held by the same worker.

    Args:
        abandoned_task_locks: List of abandoned task lock dicts (output of
            ``detect_abandoned_task_locks``).
        resource_locks: List of resource lock dicts from
            ``scan_resource_locks()``.
        redis_conn: Active Redis connection (unused but kept for API
            consistency; resource lock state comes from the pre-scanned list).

    Returns:
        A list of abandoned resource lock dicts, each augmented with
        ``abandoned_by_task`` and ``reason`` keys.
    """
    from pulpcore.app.models import Task

    if not abandoned_task_locks:
        return []

    resource_locks_by_key = {}
    for lock_info in resource_locks:
        resource_locks_by_key[lock_info["lock_key"]] = lock_info

    task_ids = [lock["task_id"] for lock in abandoned_task_locks if lock.get("task_state") != "NOT_FOUND"]
    tasks_by_id = {str(t.pk): t for t in Task.objects.filter(pk__in=task_ids)} if task_ids else {}

    from pulpcore.tasking.redis_locks import REDIS_LOCK_PREFIX

    abandoned = []
    seen_keys = set()

    for task_lock in abandoned_task_locks:
        task_id = task_lock["task_id"]
        holder = task_lock.get("holder")
        task = tasks_by_id.get(task_id)

        if task is None:
            continue

        for resource in task.reserved_resources_record:
            clean_resource = resource.removeprefix("shared:")
            lock_key = f"{REDIS_LOCK_PREFIX}{clean_resource}"

            if lock_key in seen_keys:
                continue

            resource_lock = resource_locks_by_key.get(lock_key)
            if resource_lock is None:
                continue

            if holder and holder in resource_lock.get("holders", []):
                entry = dict(resource_lock)
                entry["abandoned_by_task"] = task_id
                entry["abandoned_holder"] = holder
                entry["reason"] = f"Resource lock held by worker that abandoned task {task_id}"
                abandoned.append(entry)
                seen_keys.add(lock_key)

    return abandoned
