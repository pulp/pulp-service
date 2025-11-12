"""
RDS Proxy Connection Timeout Test Tasks

These tasks test different connection patterns to identify RDS Proxy timeout causes.
Related to PULP-955: Pulp workers disconnect from RDS Proxy after ~40-46 minutes

Test Infrastructure: Django ORM (production-like behavior)
"""

import logging
import multiprocessing
import time
import traceback
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
            # Outer try/except to catch ANY exception including BaseException
            try:
                log(f"=== {test_name} ===")

                started_at = datetime.now()
                log(f"Test started at: {started_at.isoformat()}")

                # Get and log backend PID
                backend_pid = None
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid()")
                        backend_pid = cursor.fetchone()[0]
                        log(f"PostgreSQL backend PID: {backend_pid}")
                except Exception as e:
                    log(f"Warning: Could not retrieve backend PID: {e}")

                alive = True
                extra_data = {}
                if backend_pid:
                    extra_data['backend_pid'] = backend_pid

                try:
                    # Run the actual test logic
                    log(f"Starting test execution...")
                    test_result = test_func(*args, **kwargs)
                    log(f"Test execution completed")

                    # Test function can return (alive, extra_data) or just alive
                    if isinstance(test_result, tuple):
                        alive, extra_data = test_result
                    else:
                        alive = test_result

                except Exception as e:
                    log(f"TEST FAILED with exception: {type(e).__name__}: {e}")
                    log(f"Exception traceback: {traceback.format_exc()}")
                    alive = False
                    extra_data['error'] = {
                        'type': type(e).__name__,
                        'message': str(e),
                        'traceback': traceback.format_exc()
                    }

                finished_at = datetime.now()
                duration = (finished_at - started_at).total_seconds() / 60

                log(f"Test finished at: {finished_at.isoformat()}")
                log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")
                log(f"Duration: {round(duration, 2)} minutes")

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

                log(f"Returning result: {result.get('status')}")

                # Explicitly close connection if test showed it as dead
                # This prevents worker crashes from trying to use a dead connection
                if not alive:
                    try:
                        log("Connection was dead, explicitly closing Django connection...")
                        connection.close()
                        log("Django connection closed successfully")
                    except Exception as close_err:
                        log(f"Warning: Error closing connection: {close_err}")

                return result

            except BaseException as fatal_error:
                # Catch absolutely everything including SystemExit, KeyboardInterrupt
                log(f"FATAL ERROR in test wrapper: {type(fatal_error).__name__}: {fatal_error}")
                log(f"Fatal error traceback: {traceback.format_exc()}")

                # Try to close connection on fatal error
                try:
                    log("Fatal error occurred, attempting to close connection...")
                    connection.close()
                    log("Connection closed after fatal error")
                except Exception as close_err:
                    log(f"Could not close connection after fatal error: {close_err}")

                # Try to return a minimal result even for fatal errors
                try:
                    return {
                        'test': test_name,
                        'started_at': started_at.isoformat() if 'started_at' in locals() else datetime.now().isoformat(),
                        'finished_at': datetime.now().isoformat(),
                        'duration_minutes': 0,
                        'connection_alive': False,
                        'success': False,
                        'status': 'FATAL_ERROR',
                        'error': {
                            'type': type(fatal_error).__name__,
                            'message': str(fatal_error),
                            'traceback': traceback.format_exc()
                        }
                    }
                except:
                    # If even the error result construction fails, return bare minimum
                    return {
                        'test': test_name,
                        'status': 'FATAL_ERROR',
                        'error': 'Could not construct error result'
                    }

        return wrapper
    return decorator


def test_connection_alive_django():
    """Test if Django connection is still alive"""
    try:
        # Log connection state before attempting query
        log(f"Testing connection alive - current connection state: {connection.connection}")

        # Try to execute a simple query
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
        log(f"Django connection alive: SELECT 1 returned {result}")
        return True
    except Exception as e:
        log(f"Django connection dead: {type(e).__name__}: {e}")

        # Try to close the dead connection gracefully
        try:
            log("Attempting to close dead connection...")
            connection.close()
            log("Dead connection closed successfully")
        except Exception as close_error:
            log(f"Failed to close connection: {close_error}")

        return False


def get_django_connection_info():
    """Get info about current Django database connection"""
    log(f"Database: {connection.settings_dict['NAME']}")
    log(f"Host: {connection.settings_dict['HOST']}")
    log(f"Connection: {connection.connection}")
    return connection


@rds_test_wrapper("TEST 1: IDLE CONNECTION (DJANGO ORM)")
def test_1_idle_connection(duration_minutes=50):
    """
    Test 1: Keep connection open but completely idle (Django ORM)

    Hypothesis: Idle connections timeout at ~40 minutes due to IdleClientTimeout
    Duration: Configurable (default 50 minutes)
    """
    # Force Django to establish connection
    test_connection_alive_django()

    # Get the underlying connection
    get_django_connection_info()

    # Wait for specified duration
    log(f"Waiting {duration_minutes} minutes with Django idle connection...")
    time.sleep(duration_minutes * 60)

    # Test if connection still works and return result
    return test_connection_alive_django()


@rds_test_wrapper("TEST 2: ACTIVE HEARTBEAT (DJANGO ORM)")
def test_2_active_heartbeat(duration_minutes=50):
    """
    Test 2: Keep connection alive with periodic heartbeat queries (Django ORM)

    Hypothesis: Periodic queries keep connection alive beyond 40 minutes
    Duration: Configurable (default 50 minutes)
    """
    # Send heartbeat every 60 seconds
    heartbeat_interval_seconds = 60
    iterations = duration_minutes

    log(f"Running heartbeat test: {iterations} iterations, 1 query per minute")

    for i in range(iterations):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

        elapsed_minutes = i + 1
        log(f"Heartbeat {elapsed_minutes}/{iterations}: OK")

        if i < iterations - 1:  # Don't sleep after last iteration
            time.sleep(heartbeat_interval_seconds)

    log("All heartbeats successful!")
    return True, {'iterations_completed': iterations}


@rds_test_wrapper("TEST 3: LONG-RUNNING TRANSACTION (DJANGO ORM)")
def test_3_long_transaction(duration_minutes=50):
    """
    Test 3: Start transaction and hold it for extended period (Django ORM)

    Hypothesis: Long-running transactions timeout differently than idle connections
    Duration: Configurable (default 50 minutes)
    """
    with transaction.atomic():
        # Execute a query within transaction
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM core_task LIMIT 1 FOR UPDATE")
            result = cursor.fetchone()
            log(f"Query executed: selected 1 task for update")

        # Hold transaction for specified duration
        log(f"Holding transaction for {duration_minutes} minutes...")
        time.sleep(duration_minutes * 60)

        log("Transaction will commit on context exit")
    # Transaction commits here automatically

    log("Transaction committed successfully")
    return True


@rds_test_wrapper("TEST 4: TRANSACTION WITH ACTIVE WORK (DJANGO ORM)")
def test_4_transaction_with_work(duration_minutes=50):
    """
    Test 4: Long transaction with periodic queries (Django ORM)

    Hypothesis: Transaction with periodic queries behaves differently than idle transaction
    Duration: Configurable (default 50 minutes)
    """
    # Start Django atomic transaction
    with transaction.atomic():
        log("Django transaction started")

        # Do periodic work - every 2 minutes
        iterations = duration_minutes // 2

        for i in range(iterations):
            # Query 1: Count waiting tasks (Django ORM)
            waiting_count = Task.objects.filter(state='waiting').count()

            # Query 2: Get a task
            task = Task.objects.first()

            # Query 3: Count online workers
            worker_count = AppStatus.objects.filter(app_type="worker").count()

            elapsed_minutes = (i + 1) * 2
            log(f"Iteration {i+1}/{iterations} ({elapsed_minutes} min): "
                f"{waiting_count} waiting tasks, {worker_count} workers")

            if i < iterations - 1:
                time.sleep(120)  # 2 minutes

        log("Transaction will commit on context exit")
    # Transaction commits here

    log("Django transaction committed successfully")
    return True


@rds_test_wrapper("TEST 5: SESSION VARIABLE (SET TIMEZONE, DJANGO ORM)", connection_pinned=True)
def test_5_session_variable(duration_minutes=50):
    """
    Test 5: Set session variable (TIMEZONE) and hold connection (Django ORM)

    Hypothesis: Setting session variables causes connection pinning, affecting timeout behavior
    Duration: Configurable (default 50 minutes)
    """
    # Set session variable (causes connection pinning)
    with connection.cursor() as cursor:
        cursor.execute("SET TIMEZONE TO 'UTC'")
        log("Session variable set: SET TIMEZONE TO 'UTC'")

        # Verify it was set
        cursor.execute("SHOW TIMEZONE")
        tz = cursor.fetchone()[0]
        log(f"Confirmed TIMEZONE = {tz}")

    # Wait for specified duration
    log(f"Waiting {duration_minutes} minutes with pinned connection...")
    time.sleep(duration_minutes * 60)

    # Try to query
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        log(f"Query successful: {result}")

    return True


@rds_test_wrapper("TEST 6: LISTEN/NOTIFY (CONNECTION PINNING, DJANGO ORM)", connection_pinned=True)
def test_6_listen_notify(duration_minutes=50):
    """
    Test 6: Use LISTEN/NOTIFY to pin connection like Pulp workers do (Django ORM)

    CRITICAL TEST: This mimics how Pulp workers actually use PostgreSQL.
    Workers establish a LISTEN connection to receive task dispatch notifications.

    Hypothesis: LISTEN causes persistent connection pinning
    Duration: Configurable (default 50 minutes)
    """
    # Set up LISTEN (this pins the connection)
    with connection.cursor() as cursor:
        channel_name = "test_task_dispatch"
        cursor.execute(f"LISTEN {channel_name}")
        log(f"LISTEN started on channel: {channel_name}")
        log("Connection is now PINNED (cannot be pooled)")

    # Simulate worker behavior: wait for notifications
    log(f"Listening for {duration_minutes} minutes (simulating worker lifetime)...")

    # Check for notifications periodically (like a worker would)
    iterations = duration_minutes

    for i in range(iterations):
        # Simple sleep - workers poll for notifications
        time.sleep(60)
        elapsed = i + 1
        log(f"Minute {elapsed}/{iterations}: Listening...")

    log(f"LISTEN completed successfully")

    # Clean up
    with connection.cursor() as cursor:
        cursor.execute(f"UNLISTEN {channel_name}")
        log(f"UNLISTEN {channel_name}")

    return True


def _notification_sender_worker(channel_name, interval_seconds, duration_minutes, db_settings):
    """
    Worker function to send periodic NOTIFY commands from a separate process.

    Args:
        channel_name: PostgreSQL channel to send notifications on
        interval_seconds: How often to send notifications
        duration_minutes: How long to run
        db_settings: Database connection settings from Django
    """
    import logging
    import time
    from datetime import datetime
    import psycopg

    logger = logging.getLogger(__name__)

    def worker_log(message):
        timestamp = datetime.now().isoformat()
        logger.info(f"[NOTIFY-WORKER][{timestamp}] {message}")

    try:
        # Create a new database connection (can't share with parent process)
        conn = psycopg.connect(
            host=db_settings['HOST'],
            port=db_settings.get('PORT', 5432),
            dbname=db_settings['NAME'],
            user=db_settings['USER'],
            password=db_settings['PASSWORD'],
        )
        conn.autocommit = True

        worker_log(f"Started notification sender for channel '{channel_name}'")
        worker_log(f"Will send notifications every {interval_seconds}s for {duration_minutes} minutes")

        iterations = int((duration_minutes * 60) / interval_seconds)

        for i in range(iterations):
            time.sleep(interval_seconds)

            try:
                cursor = conn.cursor()
                payload = f"heartbeat_{i+1}"
                cursor.execute(f"NOTIFY {channel_name}, '{payload}'")
                cursor.close()
                worker_log(f"Sent NOTIFY #{i+1}/{iterations}: '{payload}'")
            except Exception as e:
                worker_log(f"Error sending NOTIFY: {e}")
                break

        conn.close()
        worker_log("Notification sender completed")

    except Exception as e:
        worker_log(f"Worker failed: {e}")


@rds_test_wrapper("TEST 7: LISTEN WITH PERIODIC ACTIVITY (DJANGO ORM)", connection_pinned=True)
def test_7_listen_with_activity(duration_minutes=50):
    """
    Test 7: LISTEN with periodic NOTIFY to keep connection active (Django ORM)

    Hypothesis: Active LISTEN connection with periodic notifications stays alive longer
    Duration: Configurable (default 50 minutes)

    This test automatically sends NOTIFY commands from a separate process every 2 minutes.
    """
    channel_name = "test_task_dispatch"

    # Set up LISTEN
    with connection.cursor() as cursor:
        cursor.execute(f"LISTEN {channel_name}")
        log(f"LISTEN started on channel: {channel_name}")

    # Start notification sender in separate process
    log("Starting automatic NOTIFY sender in separate process (every 2 minutes)...")

    # Get database settings to pass to worker
    db_settings = connection.settings_dict.copy()

    notify_process = multiprocessing.Process(
        target=_notification_sender_worker,
        args=(channel_name, 120, duration_minutes, db_settings)  # Send every 2 minutes for duration
    )
    notify_process.start()
    log(f"NOTIFY sender process started (PID: {notify_process.pid})")

    # Listen for notifications
    iterations = duration_minutes

    try:
        for i in range(iterations):
            # Wait 60 seconds
            time.sleep(60)
            log(f"Minute {i+1}/{iterations}: Listening for notifications...")

    finally:
        # Clean up LISTEN
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"UNLISTEN {channel_name}")
                log(f"UNLISTEN {channel_name}")
        except Exception:
            pass

        # Stop the notification sender process
        if notify_process.is_alive():
            log("Terminating NOTIFY sender process...")
            notify_process.terminate()
            notify_process.join(timeout=5)
            if notify_process.is_alive():
                log("Force killing NOTIFY sender process...")
                notify_process.kill()
                notify_process.join()
        log("NOTIFY sender process stopped")

    return True


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
