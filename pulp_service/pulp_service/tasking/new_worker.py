"""
New worker implementation using Redis distributed lock-based task fetching.

This implementation uses a fundamentally different algorithm where workers compete
directly for task resources using Redis distributed locks, eliminating the need
for the unblocking mechanism and all task cancellation support.
"""

from gettext import gettext as _
import functools
import hashlib
import logging
import os
import random
import select
import signal
import time
import uuid
from datetime import timedelta
from multiprocessing import Process
from tempfile import TemporaryDirectory

from django.conf import settings
from django.db import connection, transaction, DatabaseError, IntegrityError
from django.utils import timezone

from pulpcore.constants import (
    TASK_STATES,
    TASK_INCOMPLETE_STATES,
    TASK_SCHEDULING_LOCK,
    WORKER_CLEANUP_LOCK,
)
from pulpcore.app.apps import pulp_plugin_configs
from pulpcore.app.util import get_worker_name
from pulpcore.app.models import Task, AppStatus
from pulpcore.app.redis_connection import get_redis_connection
from pulpcore.tasking.storage import WorkerDirectory
from pulpcore.tasking._util import (
    dispatch_scheduled_tasks,
    perform_task,
    startup_hook,
    REDIS_LOCK_PREFIX,
    resource_to_lock_key,
    release_resource_locks,
)
from pulpcore.tasking.tasks import using_workdir, execute_task


_logger = logging.getLogger(__name__)
random.seed()

# Seconds for a task to finish on semi graceful worker shutdown (approx)
TASK_GRACE_INTERVAL = settings.TASK_GRACE_INTERVAL
# Seconds between attempts to kill the subprocess (approx)
TASK_KILL_INTERVAL = 1
# Number of heartbeats between cleaning up worker processes
WORKER_CLEANUP_INTERVAL = 100
# Number of heartbeats between rechecking ignored tasks
IGNORED_TASKS_CLEANUP_INTERVAL = 100
# Number of tasks to fetch in each query
FETCH_TASK_LIMIT = 20


def exclusive(lock):
    """
    Runs function in a transaction holding the specified lock.
    Returns None if the lock could not be acquired.
    It should be used for actions that only need to be performed by a single worker.
    """

    def _decorator(f):
        @functools.wraps(f)
        def _f(self, *args, **kwargs):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_xact_lock(%s, %s)", [0, lock])
                    acquired = cursor.fetchone()[0]
                if acquired:
                    return f(self, *args, **kwargs)
                else:
                    return None

        return _f

    return _decorator


class NewPulpcoreWorker:
    """
    New worker implementation using Redis distributed lock-based resource acquisition.

    This worker uses a simpler algorithm where:
    1. Query waiting tasks (sorted by creation time, limited)
    2. For each task, try to acquire Redis distributed locks for all resources
    3. If all locks acquired, claim the task
    4. Process resources in deterministic (sorted) order to prevent deadlocks
    5. Lock values contain worker names to enable cleanup of stale locks

    Note: This implementation does NOT support task cancellation.
    """

    def __init__(self):
        # Notification states from signal handlers
        self.shutdown_requested = False
        self.wakeup_handle = False

        self.ignored_task_ids = []
        self.ignored_task_countdown = IGNORED_TASKS_CLEANUP_INTERVAL

        self.task = None
        self.name = get_worker_name()
        self.heartbeat_period = timedelta(seconds=settings.WORKER_TTL / 3)
        self.versions = {app.label: app.version for app in pulp_plugin_configs()}
        self.app_status = AppStatus.objects.create(
            name=self.name, app_type="worker", versions=self.versions
        )

        # This defaults to immediate task cancellation.
        # It will be set into the future on moderately graceful worker shutdown,
        # and set to None for fully graceful shutdown.
        self.task_grace_timeout = timezone.now()

        self.worker_cleanup_countdown = random.randint(
            int(WORKER_CLEANUP_INTERVAL / 10), WORKER_CLEANUP_INTERVAL
        )

        # Cache worker count for sleep calculation (updated during beat)
        self.num_workers = 1

        # Redis connection for distributed locks
        self.redis_conn = get_redis_connection()

        # Add a file descriptor to trigger select on signals
        self.sentinel, sentinel_w = os.pipe()
        os.set_blocking(self.sentinel, False)
        os.set_blocking(sentinel_w, False)
        signal.set_wakeup_fd(sentinel_w)

        startup_hook()

        _logger.info("Initialized NewPulpcoreWorker with Redis lock-based algorithm")

    def _signal_handler(self, thesignal, frame):
        """Handle shutdown signals."""
        if thesignal in (signal.SIGHUP, signal.SIGTERM):
            _logger.info(_("Worker %s was requested to shut down gracefully."), self.name)
            # Wait forever...
            self.task_grace_timeout = None
        else:
            # Reset signal handlers to default
            # If you kill the process a second time it's not graceful anymore.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.signal(signal.SIGHUP, signal.SIG_DFL)

            _logger.info(_("Worker %s was requested to shut down."), self.name)
            self.task_grace_timeout = timezone.now() + timezone.timedelta(
                seconds=TASK_GRACE_INTERVAL
            )
        self.shutdown_requested = True

    def shutdown(self):
        """Cleanup worker on shutdown."""
        self.app_status.delete()
        _logger.info(_("Worker %s was shut down."), self.name)

    def handle_worker_heartbeat(self):
        """
        Update worker heartbeat records.

        If the update fails (the record was deleted, the database is unreachable, ...) the worker
        is shut down.
        """
        msg = "Worker heartbeat from '{name}' at time {timestamp}".format(
            timestamp=self.app_status.last_heartbeat, name=self.name
        )
        try:
            self.app_status.save_heartbeat()
            _logger.debug(msg)
        except (IntegrityError, DatabaseError):
            _logger.error(f"Updating the heartbeat of worker {self.name} failed.")
            self.shutdown_requested = True

    def cleanup_ignored_tasks(self):
        """Remove tasks from ignored list that are no longer incomplete."""
        for pk in (
            Task.objects.filter(pk__in=self.ignored_task_ids)
            .exclude(state__in=TASK_INCOMPLETE_STATES)
            .values_list("pk", flat=True)
        ):
            self.ignored_task_ids.remove(pk)

    def cleanup_redis_locks_for_worker(self, worker_name):
        """
        Clean up Redis locks held by a specific worker.

        This is called when a worker is detected as missing to release any
        locks it may have been holding.

        Args:
            worker_name (str): Name of the missing worker
        """
        if not self.redis_conn:
            return

        try:
            # Scan for all resource locks
            lock_pattern = f"{REDIS_LOCK_PREFIX}*"
            locks_cleaned = 0

            for key in self.redis_conn.scan_iter(match=lock_pattern, count=100):
                # Check if this lock is held by the missing worker
                lock_holder = self.redis_conn.get(key)
                if lock_holder and lock_holder.decode('utf-8') == worker_name:
                    # Delete the lock
                    self.redis_conn.delete(key)
                    locks_cleaned += 1
                    _logger.info(
                        "Cleaned up lock %s held by missing worker %s",
                        key.decode('utf-8'),
                        worker_name
                    )

            if locks_cleaned > 0:
                _logger.info(
                    "Cleaned up %d Redis locks for missing worker %s",
                    locks_cleaned,
                    worker_name
                )
        except Exception as e:
            _logger.error("Error cleaning up Redis locks for worker %s: %s", worker_name, e)

    @exclusive(WORKER_CLEANUP_LOCK)
    def app_worker_cleanup(self):
        """Cleanup records of missing worker processes and their Redis locks."""
        qs = AppStatus.objects.missing()
        for app_worker in qs:
            _logger.warning(
                "Cleanup record of missing %s process %s.", app_worker.app_type, app_worker.name
            )
            # Clean up any Redis locks held by this missing worker
            if app_worker.app_type == "worker":
                self.cleanup_redis_locks_for_worker(app_worker.name)
        qs.delete()

    @exclusive(TASK_SCHEDULING_LOCK)
    def dispatch_scheduled_tasks(self):
        """Dispatch scheduled tasks."""
        dispatch_scheduled_tasks()

    def beat(self):
        """Periodic worker maintenance tasks (heartbeat, cleanup, etc.)."""
        now = timezone.now()
        if self.app_status.last_heartbeat < now - self.heartbeat_period:
            self.handle_worker_heartbeat()
            if self.ignored_task_ids:
                self.ignored_task_countdown -= 1
                if self.ignored_task_countdown <= 0:
                    self.ignored_task_countdown = IGNORED_TASKS_CLEANUP_INTERVAL
                    self.cleanup_ignored_tasks()

            self.worker_cleanup_countdown -= 1
            if self.worker_cleanup_countdown <= 0:
                self.worker_cleanup_countdown = WORKER_CLEANUP_INTERVAL
                self.app_worker_cleanup()

            self.dispatch_scheduled_tasks()

            # Update cached worker count for sleep calculation
            self.num_workers = AppStatus.objects.online().filter(app_type='worker').count()

    def _try_acquire_resource_locks(self, resources):
        """
        Try to acquire Redis distributed locks for all resources in deterministic order.

        The lock value is set to the worker name, which allows the cleanup code to
        identify and remove locks from missing workers.

        Args:
            resources (list): List of resource names

        Returns:
            bool: True if all locks were acquired, False otherwise
        """
        if not resources:
            # No resources means no locks needed
            return True

        if not self.redis_conn:
            _logger.error("Redis connection not available for locking")
            return False

        # Sort resources deterministically to prevent deadlocks
        sorted_resources = sorted(resources)

        try:
            for resource in sorted_resources:
                lock_key = resource_to_lock_key(resource)

                # Try to acquire lock using SET with NX (only set if not exists)
                # The value is the worker name, so cleanup can identify stale locks
                acquired = self.redis_conn.set(lock_key, self.name, nx=True)

                if not acquired:
                    _logger.debug(
                        "Failed to acquire lock for resource: %s (key: %s)",
                        resource,
                        lock_key
                    )
                    # Release any locks we acquired so far
                    self._release_resource_locks(sorted_resources[:sorted_resources.index(resource)])
                    return False

                _logger.debug("Acquired lock for resource: %s", resource)

            # All locks acquired successfully
            _logger.debug("Successfully acquired all locks for %d resources", len(resources))
            return True

        except Exception as e:
            _logger.error("Error acquiring locks: %s", e)
            # Try to release any locks we may have acquired
            self._release_resource_locks(sorted_resources)
            return False

    def _release_resource_locks(self, resources):
        """
        Release Redis distributed locks for the given resources.

        Uses a Lua script to ensure we only release locks that we own.

        Args:
            resources (list): List of resource names to release locks for
        """
        release_resource_locks(self.redis_conn, self.name, resources)

    def is_compatible(self, task):
        """
        Check if this worker is compatible with the task's version requirements.

        Args:
            task: Task object

        Returns:
            bool: True if compatible, False otherwise
        """
        from packaging.version import parse as parse_version

        unmatched_versions = [
            f"task: {label}>={version} worker: {self.versions.get(label)}"
            for label, version in task.versions.items()
            if label not in self.versions
            or parse_version(self.versions[label]) < parse_version(version)
        ]
        if unmatched_versions:
            domain = task.pulp_domain
            _logger.info(
                _("Incompatible versions to execute task %s in domain: %s by worker %s: %s"),
                task.pk,
                domain.name,
                self.name,
                ",".join(unmatched_versions),
            )
            return False
        return True

    def fetch_task(self):
        """
        Fetch an available waiting task using Redis locks.

        This method:
        1. Queries waiting tasks (sorted by creation time, limited)
        2. For each task, attempts to acquire Redis distributed locks for exclusive resources
        3. If resource locks acquired, attempts to claim the task with a Redis task lock (24h expiration)
        4. Returns the first task for which both locks can be acquired

        Returns:
            Task: A task object if one was successfully locked, None otherwise
        """
        # Query waiting tasks, sorted by creation time, limited
        waiting_tasks = Task.objects.filter(
            state=TASK_STATES.WAITING
        ).exclude(
            pk__in=self.ignored_task_ids
        ).order_by('pulp_created').select_related('pulp_domain')[:FETCH_TASK_LIMIT]

        # Try to acquire locks for each task
        for task in waiting_tasks:
            try:
                reserved_resources_record = task.reserved_resources_record or []

                # Extract exclusive resources (non-shared)
                exclusive_resources = [
                    resource
                    for resource in reserved_resources_record
                    if not resource.startswith("shared:")
                ]

                # First, try to acquire Redis locks for resources
                if self._try_acquire_resource_locks(exclusive_resources):
                    # Successfully acquired resource locks!
                    # Now try to claim the task with a Redis lock (24 hour expiration)
                    task_lock_key = f"task:{task.pk}"
                    # Use SET with NX (only set if not exists) and EX (expiration in seconds)
                    # 24 hours = 86400 seconds
                    acquired = self.redis_conn.set(
                        task_lock_key,
                        self.name,
                        nx=True,
                        ex=86400
                    )

                    if acquired:
                        # We successfully claimed the task!
                        _logger.info(
                            "Worker %s acquired resource locks and task lock for task %s in domain: %s",
                            self.name,
                            task.pk,
                            task.pulp_domain.name
                        )
                        # Store resources so we can release them later
                        # Note: We don't store the task lock since it auto-expires in 24h
                        task._locked_resources = exclusive_resources
                        return task
                    else:
                        # Another worker claimed this task
                        self._release_resource_locks(exclusive_resources)

                        if exclusive_resources:
                            # This should NOT happen for tasks with exclusive resources!
                            # We hold the resource locks, so no other worker should be able to claim this task
                            raise RuntimeError(
                                f"Worker {self.name} acquired resource locks {exclusive_resources} "
                                f"for task {task.pk} but another worker claimed it with task lock. "
                                f"This indicates a serious locking protocol violation."
                            )
                        else:
                            # This is normal for tasks without exclusive resources where multiple workers
                            # can acquire the (empty) resource locks simultaneously
                            _logger.debug(
                                "Worker %s tried to claim task %s but another worker got task lock first",
                                self.name,
                                task.pk
                            )
                            continue

            except Exception as e:
                _logger.error("Error processing task %s: %s", task.pk, e)
                continue

        # No task could be locked
        return None

    def supervise_immediate_task(self, task):
        """Call and supervise the immediate async task process.

        This function must only be called while holding the lock for that task."""
        self.task = task
        _logger.info(
            "WORKER IMMEDIATE EXECUTION: Worker %s executing immediate task %s in domain: %s",
            self.name,
            task.pk,
            task.pulp_domain.name
        )
        with using_workdir():
            execute_task(task)
        self.task = None

    def supervise_task(self, task):
        """Call and supervise the task process while heart beating.

        This function must only be called while holding the lock for that task.
        Note: This version does not support task cancellation."""

        self.task = task
        domain = task.pulp_domain
        _logger.info(
            "WORKER DEFERRED EXECUTION: Worker %s executing deferred task %s in domain: %s",
            self.name,
            task.pk,
            domain.name
        )
        with TemporaryDirectory(dir=".") as task_working_dir_rel_path:
            task_process = Process(target=perform_task, args=(task.pk, task_working_dir_rel_path))
            task_process.start()

            # Heartbeat while waiting for task to complete
            while task_process.is_alive():
                # Wait for a short period or until process completes
                r, w, x = select.select(
                    [self.sentinel, task_process.sentinel],
                    [],
                    [],
                    self.heartbeat_period.seconds,
                )
                # Call beat to keep worker heartbeat alive and perform periodic tasks
                self.beat()

                if self.sentinel in r:
                    os.read(self.sentinel, 256)

                if task_process.sentinel in r:
                    if not task_process.is_alive():
                        break

                # If shutdown was requested, handle gracefully or abort
                if self.shutdown_requested:
                    if self.task_grace_timeout is None or self.task_grace_timeout > timezone.now():
                        msg = (
                            "Worker shutdown requested, waiting for task {pk} in domain: {name} "
                            "to finish.".format(pk=task.pk, name=domain.name)
                        )
                        _logger.info(msg)
                    else:
                        _logger.info(
                            "Aborting current task %s in domain: %s due to worker shutdown.",
                            task.pk,
                            domain.name,
                        )
                        # Send SIGUSR1 to task process to trigger graceful abort
                        os.kill(task_process.pid, signal.SIGUSR1)
                        self.task_grace_timeout = timezone.now() + timezone.timedelta(
                            seconds=TASK_KILL_INTERVAL
                        )

            task_process.join()
            if task_process.exitcode != 0:
                _logger.warning(
                    "Task process for %s exited with non zero exitcode %i.",
                    task.pk,
                    task_process.exitcode,
                )
        self.task = None

    def handle_tasks(self):
        """Pick and supervise tasks until there are no more available tasks."""
        while not self.shutdown_requested:
            # Clear pending wakeups
            self.wakeup_handle = False

            task = self.fetch_task()
            if task is None:
                # No task found
                break

            try:
                if self.is_compatible(task):
                    # Task is compatible, execute it
                    # Note: _execute_task() will handle resource lock release in its finally block
                    if task.immediate:
                        self.supervise_immediate_task(task)
                    else:
                        self.supervise_task(task)
                else:
                    # Incompatible task, add to ignored list
                    self.ignored_task_ids.append(task.pk)
                    # Release both resource locks and task lock since we're not executing this task
                    if hasattr(task, '_locked_resources'):
                        self._release_resource_locks(task._locked_resources)
                    # Release the task lock so other workers can attempt it
                    task_lock_key = f"task:{task.pk}"
                    self.redis_conn.delete(task_lock_key)
            except Exception:
                # Exception occurred - could be during is_compatible() check or before
                # task execution begins.
                # If _execute_task() ran, it will have released resource locks and deleted
                # the _locked_resources attribute. Only release if attribute still exists.
                if hasattr(task, '_locked_resources'):
                    self._release_resource_locks(task._locked_resources)
                    # Also delete task lock since task is still in WAITING state
                    task_lock_key = f"task:{task.pk}"
                    self.redis_conn.delete(task_lock_key)
                raise

    def sleep(self):
        """Sleep while calling beat() to maintain heartbeat and perform periodic tasks.

        Sleep time = (num_workers * 10ms) + random_jitter(0.5ms, 1.5ms)
        """
        # Calculate sleep time: (num_workers * 10ms) + jitter(0.5-1.5ms)
        base_sleep_ms = self.num_workers * 10.0
        jitter_ms = random.uniform(0.5, 1.5)
        sleep_time_seconds = (base_sleep_ms + jitter_ms) / 1000.0

        _logger.debug(
            _("Worker %s sleeping for %.4f seconds (workers=%d)"),
            self.name,
            sleep_time_seconds,
            self.num_workers
        )

        # Call beat before sleeping to maintain heartbeat and perform periodic tasks
        self.beat()

        time.sleep(sleep_time_seconds)

    def run(self, burst=False):
        """Main worker loop."""
        with WorkerDirectory(self.name):
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGHUP, self._signal_handler)

            if burst:
                # Burst mode: process tasks until none are available
                self.handle_tasks()
            else:
                # Normal mode: loop and sleep when no tasks available
                while not self.shutdown_requested:
                    if self.shutdown_requested:
                        break
                    self.handle_tasks()
                    if self.shutdown_requested:
                        break
                    # Sleep until work arrives or heartbeat needed
                    self.sleep()

            self.shutdown()
