"""
Background task that scans Redis for orphaned / stale locks and removes
them automatically.

Orphaned locks occur when a worker or API process dies without releasing
its Redis locks (Bug 2 in the stuck-task taxonomy).  Because Pulp's Redis
locks have no TTL, they persist forever and block subsequent tasks from
acquiring the same resources.

This task is registered on a 6-hour schedule via
``register_stale_lock_cleanup_schedule()`` in ``tasks/util.py``.
"""

import logging

from pulpcore.app.redis_connection import get_redis_connection

from pulp_service.app.tasks.redis_lock_utils import (
    check_lock_holder_liveness,
    scan_resource_locks,
    scan_task_locks,
)

_logger = logging.getLogger(__name__)


def cleanup_stale_locks():
    """
    Scan all Redis locks, identify orphaned ones, and remove them.

    For **shared** resource locks (Redis sets with multiple members) only the
    dead holder members are removed via ``SREM``.  The key itself is deleted
    only when *all* members are dead.

    For **exclusive** resource locks (Redis strings) the key is deleted if its
    holder is dead.

    For **task locks** (``task:<uuid>``) the key is deleted if its holder is
    dead.

    Returns:
        A summary dict with counts of scanned, orphaned, and cleaned locks.
        Pulpcore stores the return value as ``task.result``.
    """
    redis_conn = get_redis_connection()
    if not redis_conn:
        _logger.error("Redis connection not available -- cannot clean up stale locks.")
        return {"error": "Redis connection not available"}

    # -- Phase 1: full scan (no pagination limit for background tasks) ------
    resource_locks, _ = scan_resource_locks(redis_conn)
    task_locks, _ = scan_task_locks(redis_conn)

    # -- Phase 2: collect all unique holders and check liveness -------------
    all_holders = set()
    for lock_info in resource_locks:
        all_holders.update(lock_info["holders"])
    for lock_info in task_locks:
        if lock_info["holder"]:
            all_holders.add(lock_info["holder"])

    liveness = check_lock_holder_liveness(all_holders)

    dead_holders = {
        name for name, info in liveness.items() if not info.get("online", False)
    }

    # -- Phase 3: clean up orphaned resource locks --------------------------
    resource_locks_scanned = len(resource_locks)
    resource_locks_orphaned = 0
    resource_locks_cleaned = 0

    for lock_info in resource_locks:
        orphaned_holders = [h for h in lock_info["holders"] if h in dead_holders]
        if not orphaned_holders:
            continue

        resource_locks_orphaned += 1
        lock_key = lock_info["lock_key"]
        lock_type = lock_info["lock_type"]

        if lock_type == "set":
            # Shared lock -- remove only dead members.
            healthy_holders = [h for h in lock_info["holders"] if h not in dead_holders]
            for holder in orphaned_holders:
                redis_conn.srem(lock_key, holder)
                _logger.info(
                    "Removed dead holder '%s' from shared lock '%s'",
                    holder,
                    lock_key,
                )

            # If no healthy holders remain, delete the key entirely.
            if not healthy_holders:
                redis_conn.delete(lock_key)
                _logger.info(
                    "Deleted empty shared lock key '%s' (all holders dead)",
                    lock_key,
                )

            resource_locks_cleaned += 1

        elif lock_type == "string":
            # Exclusive lock -- delete the key.
            redis_conn.delete(lock_key)
            _logger.info(
                "Deleted exclusive lock '%s' held by dead holder '%s'",
                lock_key,
                orphaned_holders[0],
            )
            resource_locks_cleaned += 1

    # -- Phase 4: clean up orphaned task locks ------------------------------
    task_locks_scanned = len(task_locks)
    task_locks_orphaned = 0
    task_locks_cleaned = 0

    for lock_info in task_locks:
        holder = lock_info["holder"]
        if not holder or holder not in dead_holders:
            continue

        task_locks_orphaned += 1
        lock_key = lock_info["lock_key"]
        redis_conn.delete(lock_key)
        _logger.info(
            "Deleted orphaned task lock '%s' held by dead holder '%s'",
            lock_key,
            holder,
        )
        task_locks_cleaned += 1

    # -- Phase 5: return summary --------------------------------------------
    summary = {
        "resource_locks_scanned": resource_locks_scanned,
        "resource_locks_orphaned": resource_locks_orphaned,
        "resource_locks_cleaned": resource_locks_cleaned,
        "task_locks_scanned": task_locks_scanned,
        "task_locks_orphaned": task_locks_orphaned,
        "task_locks_cleaned": task_locks_cleaned,
        "dead_holders": sorted(dead_holders),
    }

    _logger.info("Stale lock cleanup complete: %s", summary)
    return summary
