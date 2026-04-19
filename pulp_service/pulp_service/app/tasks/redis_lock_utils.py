"""
Shared utilities for scanning and inspecting Redis locks used by Pulp's
task system.

Functions in this module are consumed by both the ``StaleLockScanView``
(paginated, interactive) and the ``cleanup_stale_locks`` background task
(full scan, automated).
"""

import logging

_logger = logging.getLogger(__name__)


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
            resource_name = key_str[len(REDIS_LOCK_PREFIX):]

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

            locks.append({
                "lock_key": key_str,
                "resource": resource_name,
                "lock_type": lock_type,
                "holders": holders,
                "ttl": ttl,
            })

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

            locks.append({
                "lock_key": key_str,
                "task_id": task_id,
                "lock_type": lock_type,
                "holder": holder,
                "ttl": ttl,
            })

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

    app_statuses = {
        app.name: app
        for app in AppStatus.objects.filter(name__in=lock_holders)
    }

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
                "last_heartbeat": (
                    app.last_heartbeat.isoformat() if app.last_heartbeat else None
                ),
                "verdict": (
                    "alive"
                    if app.online
                    else "DEAD: AppStatus exists but is not online, lock is orphaned"
                ),
            }
    return result
