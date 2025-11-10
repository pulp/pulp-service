"""
RDS Proxy Connection Timeout Test Tasks

These tasks test different connection patterns to identify RDS Proxy timeout causes.
Related to PULP-955: Pulp workers disconnect from RDS Proxy after ~40-46 minutes

Test Infrastructure: Django ORM (production-like behavior)
"""

import logging
import time
from datetime import datetime
from functools import wraps

from django.db import connection, transaction

from pulpcore.app.models import AppStatus
from pulpcore.plugin.models import Task


logger = logging.getLogger(__name__)


def log(message):
    """Log with timestamp"""
    timestamp = datetime.now().isoformat()
    logger.info(f"[{timestamp}] {message}")


def rds_test_wrapper(test_name, connection_pinned=False):
    """
    Decorator to handle common test setup, timing, and result formatting.

    Reduces code duplication across test functions by centralizing:
    - Timing logic
    - Logging setup/teardown
    - Result formatting
    - Error handling

    Args:
        test_name: Human-readable test name
        connection_pinned: Whether this test pins the connection

    Returns:
        Decorated function that returns standardized test results
    """
    def decorator(test_func):
        @wraps(test_func)
        def wrapper(*args, **kwargs):
            log(f"=== {test_name} ===")

            started_at = datetime.now()
            log(f"Test started at: {started_at.isoformat()}")

            alive = True
            extra_data = {}

            try:
                # Run the actual test logic
                test_result = test_func(*args, **kwargs)

                # Test function can return (alive, extra_data) or just alive
                if isinstance(test_result, tuple):
                    alive, extra_data = test_result
                else:
                    alive = test_result

            except Exception as e:
                log(f"TEST FAILED with exception: {e}")
                alive = False
                extra_data['error'] = str(e)

            finished_at = datetime.now()
            duration = (finished_at - started_at).total_seconds() / 60

            log(f"Test finished at: {finished_at.isoformat()}")
            log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

            # Build result dictionary
            result = {
                'test': test_name,
                'started_at': started_at.isoformat(),
                'finished_at': finished_at.isoformat(),
                'duration_minutes': round(duration, 2),
                'connection_alive': alive,
                'success': alive,
                'status': 'PASSED' if alive else 'FAILED'
            }

            # Add connection pinning info if applicable
            if connection_pinned:
                result['connection_pinned'] = True

            # Merge any extra data from the test
            result.update(extra_data)

            return result

        return wrapper
    return decorator


def test_connection_alive_django():
    """Test if Django connection is still alive"""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
        log(f"Django connection alive: SELECT 1 returned {result}")
        return True
    except Exception as e:
        log(f"Django connection dead: {e}")
        return False


def get_django_connection_info():
    """Get info about current Django database connection"""
    log(f"Database: {connection.settings_dict['NAME']}")
    log(f"Host: {connection.settings_dict['HOST']}")
    log(f"Connection: {connection.connection}")
    return connection


@rds_test_wrapper("TEST 1: IDLE CONNECTION (DJANGO ORM)")
def test_1_idle_connection():
    """
    Test 1: Keep connection open but completely idle (Django ORM)

    Hypothesis: Idle connections timeout at ~40 minutes due to IdleClientTimeout
    Duration: 50 minutes
    """
    # Force Django to establish connection
    test_connection_alive_django()

    # Get the underlying connection
    get_django_connection_info()

    # Wait 50 minutes
    wait_minutes = 50
    log(f"Waiting {wait_minutes} minutes with Django idle connection...")
    time.sleep(wait_minutes * 60)

    # Test if connection still works and return result
    return test_connection_alive_django()


def test_2_active_heartbeat():
    """
    Test 2: Keep connection alive with periodic heartbeat queries (Django ORM)

    Hypothesis: Periodic queries keep connection alive beyond 40 minutes
    Duration: 50 minutes
    """
    log("=== TEST 2: ACTIVE HEARTBEAT (DJANGO ORM) ===")

    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    # Send heartbeat every 60 seconds for 50 minutes
    test_duration_minutes = 50
    heartbeat_interval_seconds = 60
    iterations = test_duration_minutes

    log(f"Running heartbeat test: {iterations} iterations, 1 query per minute")

    alive = True
    for i in range(iterations):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()

            elapsed_minutes = i + 1
            log(f"Heartbeat {elapsed_minutes}/{iterations}: OK")

            if i < iterations - 1:  # Don't sleep after last iteration
                time.sleep(heartbeat_interval_seconds)
        except Exception as e:
            alive = False
            finished_at = datetime.now()
            duration = (finished_at - started_at).total_seconds() / 60
            log(f"HEARTBEAT FAILED at iteration {i+1} ({duration:.2f} minutes)")
            log(f"Error: {e}")
            break

    if alive:
        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds() / 60
        log("All heartbeats successful!")

    log(f"Test finished at: {finished_at.isoformat()}")
    log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

    return {
        'test': 'Test 2 - Active Heartbeat',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': round(duration, 2),
        'connection_alive': alive,
        'iterations_completed': i + 1 if not alive else iterations,
        'success': alive,
        'status': 'PASSED' if alive else 'FAILED'
    }


def test_3_long_transaction():
    """
    Test 3: Start transaction and hold it for extended period (Django ORM)

    Hypothesis: Long-running transactions timeout differently than idle connections
    Duration: 50 minutes
    """
    log("=== TEST 3: LONG-RUNNING TRANSACTION (DJANGO ORM) ===")

    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    try:
        # Disable autocommit for manual transaction control
        transaction.set_autocommit(False)

        # Execute a query within transaction
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM core_task LIMIT 1 FOR UPDATE")
            result = cursor.fetchone()
            log(f"Query executed: selected 1 task for update")

        # Hold transaction for 50 minutes
        wait_minutes = 50
        log(f"Holding transaction for {wait_minutes} minutes...")
        time.sleep(wait_minutes * 60)

        # Try to commit
        transaction.commit()
        log("Transaction committed successfully")
        alive = True

    except Exception as e:
        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds() / 60
        log(f"TRANSACTION FAILED after {duration:.2f} minutes")
        log(f"Error: {e}")
        alive = False
        try:
            transaction.rollback()
        except:
            pass
    finally:
        # Re-enable autocommit
        transaction.set_autocommit(True)

    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds() / 60

    log(f"Test finished at: {finished_at.isoformat()}")
    log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

    return {
        'test': 'Test 3 - Long Transaction',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': round(duration, 2),
        'connection_alive': alive,
        'success': alive,
        'status': 'PASSED' if alive else 'FAILED'
    }


def test_4_transaction_with_work():
    """
    Test 4: Long transaction with periodic queries (Django ORM)

    Hypothesis: Transaction with periodic queries behaves differently than idle transaction
    Duration: 50 minutes
    """
    log("=== TEST 4: TRANSACTION WITH ACTIVE WORK (DJANGO ORM) ===")

    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    try:
        # Start Django atomic transaction
        with transaction.atomic():
            log("Django transaction started")

            # Do periodic work for 50 minutes
            iterations = 25  # Every 2 minutes for 50 min

            for i in range(iterations):
                # Query 1: Count waiting tasks (Django ORM)
                waiting_count = Task.objects.filter(state='waiting').count()

                # Query 2: Get a task
                task = Task.objects.first()

                # Query 3: Count online workers
                worker_count = AppStatus.objects.filter(app_type="worker", online=True).count()

                elapsed_minutes = (i + 1) * 2
                log(f"Iteration {i+1}/{iterations} ({elapsed_minutes} min): "
                    f"{waiting_count} waiting tasks, {worker_count} workers")

                if i < iterations - 1:
                    time.sleep(120)  # 2 minutes

            log("Transaction will commit on context exit")
        # Transaction commits here

        alive = True
        log("Django transaction committed successfully")

    except Exception as e:
        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds() / 60
        log(f"TRANSACTION FAILED after {duration:.2f} minutes")
        log(f"Error: {e}")
        alive = False

    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds() / 60

    log(f"Test finished at: {finished_at.isoformat()}")
    log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

    return {
        'test': 'Test 4 - Django ORM Transaction with Work',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': round(duration, 2),
        'connection_alive': alive,
        'success': alive,
        'status': 'PASSED' if alive else 'FAILED'
    }


def test_5_session_variable():
    """
    Test 5: Set session variable (TIMEZONE) and hold connection (Django ORM)

    Hypothesis: Setting session variables causes connection pinning, affecting timeout behavior
    Duration: 50 minutes
    """
    log("=== TEST 5: SESSION VARIABLE (SET TIMEZONE, DJANGO ORM) ===")

    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    try:
        # Set session variable (causes connection pinning)
        with connection.cursor() as cursor:
            cursor.execute("SET TIMEZONE TO 'UTC'")
            log("Session variable set: SET TIMEZONE TO 'UTC'")

            # Verify it was set
            cursor.execute("SHOW TIMEZONE")
            tz = cursor.fetchone()[0]
            log(f"Confirmed TIMEZONE = {tz}")

        # Wait 50 minutes
        wait_minutes = 50
        log(f"Waiting {wait_minutes} minutes with pinned connection...")
        time.sleep(wait_minutes * 60)

        # Try to query
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            log(f"Query successful: {result}")
        alive = True

    except Exception as e:
        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds() / 60
        log(f"CONNECTION FAILED after {duration:.2f} minutes")
        log(f"Error: {e}")
        alive = False

    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds() / 60

    log(f"Test finished at: {finished_at.isoformat()}")
    log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

    return {
        'test': 'Test 5 - Session Variable',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': round(duration, 2),
        'connection_alive': alive,
        'success': alive,
        'status': 'PASSED' if alive else 'FAILED'
    }


def test_6_listen_notify():
    """
    Test 6: Use LISTEN/NOTIFY to pin connection like Pulp workers do (Django ORM)

    CRITICAL TEST: This mimics how Pulp workers actually use PostgreSQL.
    Workers establish a LISTEN connection to receive task dispatch notifications.

    Hypothesis: LISTEN causes persistent connection pinning
    Duration: 50 minutes
    """
    log("=== TEST 6: LISTEN/NOTIFY (CONNECTION PINNING, DJANGO ORM) ===")

    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    try:
        # Set up LISTEN (this pins the connection)
        with connection.cursor() as cursor:
            channel_name = "test_task_dispatch"
            cursor.execute(f"LISTEN {channel_name}")
            log(f"LISTEN started on channel: {channel_name}")
            log("Connection is now PINNED (cannot be pooled)")

        # Simulate worker behavior: wait for notifications
        wait_minutes = 50
        log(f"Listening for {wait_minutes} minutes (simulating worker lifetime)...")

        # Check for notifications periodically (like a worker would)
        iterations = wait_minutes
        alive = True

        for i in range(iterations):
            try:
                # Simple sleep - workers poll for notifications
                time.sleep(60)
                elapsed = i + 1
                log(f"Minute {elapsed}/{iterations}: Listening...")

            except Exception as e:
                finished_at = datetime.now()
                duration = (finished_at - started_at).total_seconds() / 60
                log(f"LISTEN FAILED at minute {i+1} ({duration:.2f} minutes)")
                log(f"Error: {e}")
                alive = False
                break

        if alive:
            finished_at = datetime.now()
            duration = (finished_at - started_at).total_seconds() / 60
            log(f"LISTEN completed successfully for {duration:.2f} minutes")

            # Clean up
            with connection.cursor() as cursor:
                cursor.execute(f"UNLISTEN {channel_name}")
                log(f"UNLISTEN {channel_name}")

    except Exception as e:
        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds() / 60
        log(f"CONNECTION FAILED after {duration:.2f} minutes")
        log(f"Error: {e}")
        alive = False

    log(f"Test finished at: {finished_at.isoformat()}")
    log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

    return {
        'test': 'Test 6 - LISTEN/NOTIFY',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': round(duration, 2),
        'connection_alive': alive,
        'connection_pinned': True,
        'success': alive,
        'status': 'PASSED' if alive else 'FAILED'
    }


def test_7_listen_with_activity():
    """
    Test 7: LISTEN with periodic NOTIFY to keep connection active (Django ORM)

    Hypothesis: Active LISTEN connection with periodic notifications stays alive longer
    Duration: 50 minutes

    Note: This test requires running a separate connection to send NOTIFY.
    Use send_test_notification() helper from another worker/shell.
    """
    log("=== TEST 7: LISTEN WITH PERIODIC ACTIVITY (DJANGO ORM) ===")

    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    try:
        # Set up LISTEN
        with connection.cursor() as cursor:
            channel_name = "test_task_dispatch"
            cursor.execute(f"LISTEN {channel_name}")
            log(f"LISTEN started on channel: {channel_name}")
            log("Note: Manually send NOTIFY commands every 2 minutes from another session")
            log(f"  Example: NOTIFY {channel_name}, 'heartbeat_1';")

        # Listen for notifications
        alive = True
        iterations = 50  # 50 minutes

        for i in range(iterations):
            try:
                # Wait 60 seconds
                time.sleep(60)
                log(f"Minute {i+1}/{iterations}: Waiting for notification...")

            except Exception as e:
                finished_at = datetime.now()
                duration = (finished_at - started_at).total_seconds() / 60
                log(f"LISTEN FAILED after {duration:.2f} minutes")
                log(f"Error: {e}")
                alive = False
                break

        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds() / 60

        log(f"Test finished at: {finished_at.isoformat()}")
        log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

        # Clean up
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"UNLISTEN {channel_name}")
        except:
            pass

    except Exception as e:
        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds() / 60
        log(f"CONNECTION FAILED after {duration:.2f} minutes")
        log(f"Error: {e}")
        alive = False

    return {
        'test': 'Test 7 - LISTEN with Activity',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': round(duration, 2),
        'connection_alive': alive,
        'connection_pinned': True,
        'success': alive,
        'status': 'PASSED' if alive else 'FAILED'
    }


def send_test_notification(channel_name="test_task_dispatch", payload="test_payload"):
    """
    Helper function to send NOTIFY from a separate connection

    Run this from a different Django shell or worker to send notifications
    to a listening connection in Test 7.

    Usage:
        from pulp_service.app.tasks.rds_connection_tests import send_test_notification
        send_test_notification()
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(f"NOTIFY {channel_name}, '{payload}'")
    log(f"Sent NOTIFY to {channel_name}: {payload}")
