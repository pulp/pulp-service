# RDS Proxy Disconnection Investigation - Test Plan

**Ticket**: [PULP-955](https://issues.redhat.com/browse/PULP-955)

**Issue**: Pulp workers disconnect from RDS Proxy after ~40-46 minutes

**Observed Pattern**:
- Test 1: Disconnected at 41:16
- Test 2: Disconnected at 40:49
- Test 3: Disconnected at 46:41

**Date**: October 30, 2025

**Test Infrastructure**: Django ORM (primary) and psycopg3 (debugging supplement)

---

## Step 4: Test with Different Workload Patterns

### Objective
Determine if workload type affects the ~40 minute timeout behavior.

### Test Execution Order
Run tests in the following sequence to build understanding progressively:
1. **Test 1**: Idle Connection (baseline)
2. **Test 2**: Active Heartbeat (does activity help?)
3. **Test 3**: Long Transaction idle (transaction behavior)
4. **Test 4**: Transaction with Active Work (transaction + activity)
5. **Test 5**: Session Variable (connection pinning via SET)
6. **Test 6**: LISTEN/NOTIFY idle (critical - how Pulp workers actually work)
7. **Test 7**: LISTEN with Activity (pinned connection + activity)

### Test Environment Setup

**Prerequisites**:
- Access to pulp-prod or pulp-perf with RDS Proxy enabled
- Python script capability to run isolated connection tests
- CloudWatch Logs access for error monitoring
- Ability to run tests for 50+ minutes each
- **psycopg3 installed**: `pip install psycopg[binary]`
- **Django environment configured**: Access to Pulp's Django settings

**Test Script Template**:
```python
#!/usr/bin/env python3
"""
RDS Proxy Connection Timeout Test
Test different connection patterns to identify timeout cause
Uses psycopg3 and Django ORM
"""

import psycopg
import time
from datetime import datetime

# Database connection parameters (for raw psycopg3 tests)
DB_CONFIG = {
    'host': 'rds-proxy-endpoint.region.rds.amazonaws.com',
    'dbname': 'pulp',
    'user': 'pulp',
    'password': 'xxx',
    'port': 5432
}

def log(message):
    """Log with timestamp"""
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] {message}")

def get_connection():
    """Establish database connection (psycopg3)"""
    log("Establishing database connection...")
    conn = psycopg.connect(**DB_CONFIG)
    log(f"Connection established: {conn}")
    return conn

def test_connection_alive(conn):
    """Test if connection is still alive (psycopg3)"""
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
        log(f"Connection alive: SELECT 1 returned {result}")
        return True
    except Exception as e:
        log(f"Connection dead: {e}")
        return False
```

---

### Django ORM Tests (Preferred for Pulp)

**Why use Django ORM**: Since Pulp uses Django, testing with Django ORM more accurately represents production behavior. **All production tests should use Django ORM**, with psycopg3 tests as a supplement for debugging.

**Setup**:
```python
#!/usr/bin/env python3
"""
RDS Proxy Connection Timeout Test - Django ORM Version
More realistic for Pulp production environment
"""

import os
import django
import time
from datetime import datetime

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pulpcore.app.settings')
django.setup()

# Now import Django models
from django.db import connection, transaction
from pulpcore.app.models import Task, Worker

def log(message):
    """Log with timestamp"""
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] {message}")

def test_connection_alive_django():
    """Test if Django connection is still alive"""
    try:
        from django.db import connection
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
    from django.db import connection
    log(f"Database: {connection.settings_dict['NAME']}")
    log(f"Host: {connection.settings_dict['HOST']}")
    log(f"Connection: {connection.connection}")
    return connection
```

**Django ORM Test Examples**:

```python
# Example: Test with Django ORM queries (like Test 4)
def test_4_django_orm():
    """Test 4 using Django ORM - Transaction with active work"""
    log("=== TEST 4: DJANGO ORM TRANSACTION WITH WORK ===")

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
                worker_count = Worker.objects.filter(online=True).count()

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
        log(f"DJANGO TRANSACTION FAILED after {duration:.2f} minutes")
        log(f"Error: {e}")
        alive = False

    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds() / 60

    log(f"Test finished at: {finished_at.isoformat()}")
    log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

    return {
        'test': 'Test 4 - Django ORM Transaction',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': duration,
        'connection_alive': alive
    }

# Example: Django connection check (like Test 1)
def test_1_django_orm():
    """Test 1 using Django - Idle connection"""
    log("=== TEST 1: DJANGO ORM IDLE CONNECTION ===")

    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    # Force Django to establish connection
    test_connection_alive_django()

    # Get the underlying connection
    conn_info = get_django_connection_info()

    # Wait 50 minutes
    wait_minutes = 50
    log(f"Waiting {wait_minutes} minutes with Django idle connection...")
    time.sleep(wait_minutes * 60)

    # Test if connection still works
    alive = test_connection_alive_django()

    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds() / 60

    log(f"Test finished at: {finished_at.isoformat()}")
    log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

    return {
        'test': 'Test 1 - Django Idle Connection',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': duration,
        'connection_alive': alive
    }
```

**Notes on Django ORM Testing**:
- Django's `CONN_MAX_AGE` setting affects connection behavior (check `settings.py`)
- Django may close/reopen connections automatically between requests
- `transaction.atomic()` creates savepoints, not full transactions by default
- Use `transaction.set_autocommit(False)` for manual transaction control if needed

**Recommendation**:
- **Primary approach**: Run all tests using **Django ORM** (most realistic for Pulp production)
- **Secondary approach**: Run tests with **raw psycopg3** for debugging and comparison
- This helps identify if Django's connection management affects timeout behavior differently

---

### Test 1: Idle Connection Test

**Hypothesis**: Idle connections timeout at ~40 minutes due to IdleClientTimeout

**Test Duration**: 50 minutes

**Procedure**:
```python
def test_1_idle_connection():
    """Test 1: Keep connection open but completely idle (psycopg3)"""
    log("=== TEST 1: IDLE CONNECTION (PSYCOPG3) ===")

    # Establish connection
    conn = get_connection()
    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    # Wait 50 minutes doing nothing
    wait_minutes = 50
    log(f"Waiting {wait_minutes} minutes with idle connection...")
    time.sleep(wait_minutes * 60)

    # Try to use connection
    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds() / 60
    log(f"Waited {duration:.2f} minutes")

    # Test if connection still works
    alive = test_connection_alive(conn)

    log(f"Test finished at: {finished_at.isoformat()}")
    log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

    conn.close()

    return {
        'test': 'Test 1 - Idle Connection',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': duration,
        'connection_alive': alive
    }

if __name__ == '__main__':
    result = test_1_idle_connection()
    print("\n=== RESULTS ===")
    print(result)
```

**Expected Outcome**: Connection dies at ~40 minutes

**Data to Record**:
- Exact disconnect time
- Error message from exception
- CloudWatch logs around disconnect time

---

### Test 2: Active Heartbeat Test

**Hypothesis**: Periodic queries keep connection alive beyond 40 minutes

**Test Duration**: 50 minutes

**Procedure**:
```python
def test_2_active_heartbeat():
    """Test 2: Keep connection alive with periodic heartbeat queries (psycopg3)"""
    log("=== TEST 2: ACTIVE HEARTBEAT (PSYCOPG3) ===")

    # Establish connection
    conn = get_connection()
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
            with conn.cursor() as cursor:
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
        log(f"All heartbeats successful!")

    log(f"Test finished at: {finished_at.isoformat()}")
    log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

    try:
        conn.close()
    except:
        pass

    return {
        'test': 'Test 2 - Active Heartbeat',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': duration,
        'connection_alive': alive,
        'iterations_completed': i + 1 if not alive else iterations
    }

if __name__ == '__main__':
    result = test_2_active_heartbeat()
    print("\n=== RESULTS ===")
    print(result)
```

**Expected Outcome**: Connection stays alive for full 50 minutes

**Data to Record**:
- Whether connection stayed alive
- If it died, at which iteration/minute
- Error message if disconnected

---

### Test 3: Long-Running Transaction Test

**Hypothesis**: Long-running transactions timeout differently than idle connections

**Test Duration**: 50 minutes

**Procedure**:
```python
def test_3_long_transaction():
    """Test 3: Start transaction and hold it for extended period (psycopg3)"""
    log("=== TEST 3: LONG-RUNNING TRANSACTION (PSYCOPG3) ===")

    # Establish connection
    conn = get_connection()
    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    try:
        # Start transaction
        with conn.cursor() as cursor:
            cursor.execute("BEGIN")
            log("Transaction started: BEGIN")

            # Execute a query within transaction
            cursor.execute("SELECT * FROM core_task LIMIT 1 FOR UPDATE")
            result = cursor.fetchone()
            log(f"Query executed: selected 1 task for update")

            # Hold transaction for 50 minutes
            wait_minutes = 50
            log(f"Holding transaction for {wait_minutes} minutes...")
            time.sleep(wait_minutes * 60)

            # Try to commit
            cursor.execute("COMMIT")
            log("Transaction committed successfully")
        alive = True

    except Exception as e:
        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds() / 60
        log(f"TRANSACTION FAILED after {duration:.2f} minutes")
        log(f"Error: {e}")
        alive = False
        try:
            conn.rollback()
        except:
            pass

    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds() / 60

    log(f"Test finished at: {finished_at.isoformat()}")
    log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

    try:
        conn.close()
    except:
        pass

    return {
        'test': 'Test 3 - Long Transaction',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': duration,
        'connection_alive': alive
    }

if __name__ == '__main__':
    result = test_3_long_transaction()
    print("\n=== RESULTS ===")
    print(result)
```

**Expected Outcome**: Transaction may timeout at PostgreSQL level or RDS Proxy level

**Data to Record**:
- Whether transaction completed
- Exact timeout if failed
- Error message (idle_in_transaction_timeout vs connection timeout)

---

### Test 4: Transaction with Active Work

**Hypothesis**: Transaction with periodic queries behaves differently than idle transaction

**Test Duration**: 50 minutes

**Procedure**:
```python
def test_4_transaction_with_work():
    """Test 4: Long transaction with periodic queries (not idle, psycopg3)"""
    log("=== TEST 4: TRANSACTION WITH ACTIVE WORK (PSYCOPG3) ===")

    # Establish connection
    conn = get_connection()
    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    try:
        # Start transaction
        with conn.cursor() as cursor:
            cursor.execute("BEGIN")
            log("Transaction started: BEGIN")

            # Do periodic work within the transaction for 50 minutes
            test_duration_minutes = 50
            work_interval_seconds = 120  # Query every 2 minutes

            log(f"Running transaction with work: queries every {work_interval_seconds}s for {test_duration_minutes} min")

            iterations = test_duration_minutes * 60 // work_interval_seconds  # ~25 iterations

            for i in range(iterations):
                try:
                    # Do some actual work within transaction
                    # Query 1: Read task count
                    cursor.execute("SELECT COUNT(*) FROM core_task WHERE state = 'waiting'")
                    count = cursor.fetchone()[0]

                    # Query 2: Read a task
                    cursor.execute("SELECT pulp_id, name FROM core_task LIMIT 1")
                    task = cursor.fetchone()

                    # Query 3: Check workers
                    cursor.execute("SELECT COUNT(*) FROM core_worker WHERE online = true")
                    workers = cursor.fetchone()[0]

                    elapsed_minutes = (i + 1) * work_interval_seconds / 60
                    log(f"Work iteration {i+1}/{iterations} ({elapsed_minutes:.1f} min): "
                        f"{count} waiting tasks, {workers} online workers")

                    if i < iterations - 1:  # Don't sleep after last iteration
                        time.sleep(work_interval_seconds)

                except Exception as e:
                    finished_at = datetime.now()
                    duration = (finished_at - started_at).total_seconds() / 60
                    log(f"WORK FAILED at iteration {i+1} ({duration:.2f} minutes)")
                    log(f"Error: {e}")
                    raise

            # Commit the transaction
            cursor.execute("COMMIT")
            log("Transaction committed successfully after periodic work")
        alive = True

    except Exception as e:
        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds() / 60
        log(f"TRANSACTION FAILED after {duration:.2f} minutes")
        log(f"Error: {e}")
        alive = False
        try:
            conn.rollback()
        except:
            pass

    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds() / 60

    log(f"Test finished at: {finished_at.isoformat()}")
    log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

    try:
        conn.close()
    except:
        pass

    return {
        'test': 'Test 4 - Transaction with Work',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': duration,
        'connection_alive': alive,
        'iterations_completed': i + 1 if not alive else iterations
    }

if __name__ == '__main__':
    result = test_4_transaction_with_work()
    print("\n=== RESULTS ===")
    print(result)
```

**Expected Outcome**: Active work within transaction should prevent idle_in_transaction_timeout

**Data to Record**:
- Whether transaction completed full 50 minutes
- Number of work iterations completed
- Whether active queries prevent timeout vs idle transaction (Test 3)

**Why This Test Matters**:
- Tests if PostgreSQL `idle_in_transaction_timeout` only applies to truly idle transactions
- Simulates real worker behavior: transaction with periodic queries
- Helps understand if "active" transactions behave differently than "idle" transactions
- Comparison with Test 3 shows if work within transaction extends timeout

---

### Test 5: Session Variable (SET TIMEZONE) Test

**Hypothesis**: Setting session variables causes connection pinning, affecting timeout behavior

**Test Duration**: 50 minutes

**Procedure**:
```python
def test_5_session_variable():
    """Test 5: Set session variable (TIMEZONE) and hold connection (psycopg3)"""
    log("=== TEST 5: SESSION VARIABLE (SET TIMEZONE, PSYCOPG3) ===")

    # Establish connection
    conn = get_connection()
    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    try:
        # Set session variable (causes connection pinning)
        with conn.cursor() as cursor:
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
        with conn.cursor() as cursor:
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

    try:
        conn.close()
    except:
        pass

    return {
        'test': 'Test 5 - Session Variable',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': duration,
        'connection_alive': alive
    }

if __name__ == '__main__':
    result = test_5_session_variable()
    print("\n=== RESULTS ===")
    print(result)
```

**Expected Outcome**: Connection pinned due to session variable, timeout behavior may differ

**Data to Record**:
- Whether connection survived 50 minutes
- Exact disconnect time if failed
- Check CloudWatch for pinning metrics

---

### Test 6: LISTEN/NOTIFY (Connection Pinning) Test

**Hypothesis**: LISTEN causes persistent connection pinning, simulating real Pulp worker behavior

**Test Duration**: 50 minutes

**Background**: This test mimics how Pulp workers actually use PostgreSQL. Workers establish a LISTEN connection to receive task dispatch notifications, which creates a long-lived, pinned connection that cannot be pooled by RDS Proxy.

**Procedure**:
```python
def test_6_listen_notify():
    """Test 6: Use LISTEN/NOTIFY to pin connection like Pulp workers do (psycopg3)"""
    log("=== TEST 6: LISTEN/NOTIFY (CONNECTION PINNING, PSYCOPG3) ===")

    # Establish connection
    conn = get_connection()
    conn.autocommit = True  # psycopg3 way to set autocommit
    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    try:
        # Set up LISTEN (this pins the connection)
        with conn.cursor() as cursor:
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
                # Wait for notification with 60 second timeout
                import select
                if select.select([conn], [], [], 60) == ([], [], []):
                    # No notification received (expected in test)
                    elapsed = i + 1
                    log(f"Minute {elapsed}/{iterations}: No notification (listening...)")
                else:
                    # Notification received (psycopg3 uses conn.notifies)
                    for notify in conn.notifies():
                        log(f"Received notification: {notify.payload}")

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
            with conn.cursor() as cursor:
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

    try:
        cursor.close()
        conn.close()
    except:
        pass

    return {
        'test': 'Test 6 - LISTEN/NOTIFY',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': duration,
        'connection_alive': alive,
        'connection_pinned': True
    }

# Additional helper: Send test notification (run from separate connection)
def send_test_notification():
    """Helper function to send NOTIFY from a separate connection (psycopg3)"""
    conn = get_connection()
    with conn.cursor() as cursor:
        cursor.execute("NOTIFY test_task_dispatch, 'test_payload'")
    conn.commit()
    conn.close()
    log("Test notification sent")

if __name__ == '__main__':
    import select  # Required for LISTEN polling
    result = test_6_listen_notify()
    print("\n=== RESULTS ===")
    print(result)
```

**Expected Outcome**: Connection pinned immediately, timeout behavior critical to understand

**Data to Record**:
- Whether LISTEN connection survived 50 minutes
- Exact disconnect time if failed
- CloudWatch pinning metrics should show **100% pinned** from start
- Compare disconnect time with Test 1 (idle) and Test 5 (SET TIMEZONE)

**Why This Test Matters**:
- **This is how Pulp workers actually behave** in production
- Each worker holds one LISTEN connection for its entire lifetime
- RDS Proxy cannot pool these connections (permanently pinned)
- If you have 64 workers, you have 64 permanently pinned connections
- This test reveals if LISTEN connections timeout differently than idle/SET TIMEZONE

---

### Test 7: LISTEN with Periodic Activity

**Hypothesis**: Active LISTEN connection with periodic notifications stays alive longer

**Test Duration**: 50 minutes

**Procedure**:
```python
def test_7_listen_with_activity():
    """Test 7: LISTEN with periodic NOTIFY to keep connection active (psycopg3)"""
    log("=== TEST 7: LISTEN WITH PERIODIC ACTIVITY (PSYCOPG3) ===")

    from multiprocessing import Process
    import select

    # Establish listening connection
    conn = get_connection()
    conn.autocommit = True  # psycopg3 way to set autocommit
    started_at = datetime.now()
    log(f"Test started at: {started_at.isoformat()}")

    # Set up LISTEN
    with conn.cursor() as cursor:
        channel_name = "test_task_dispatch"
        cursor.execute(f"LISTEN {channel_name}")
        log(f"LISTEN started on channel: {channel_name}")

    # Function to send periodic notifications
    def send_periodic_notifications():
        """Send NOTIFY every 2 minutes"""
        for i in range(25):  # 50 minutes / 2 minutes
            time.sleep(120)  # 2 minutes
            try:
                notify_conn = get_connection()
                with notify_conn.cursor() as notify_cursor:
                    payload = f"heartbeat_{i+1}"
                    notify_cursor.execute(f"NOTIFY {channel_name}, '{payload}'")
                notify_conn.commit()
                notify_conn.close()
                log(f"Sent notification {i+1}/25: {payload}")
            except Exception as e:
                log(f"Failed to send notification: {e}")

    # Start notification sender in background process
    notifier_process = Process(target=send_periodic_notifications, daemon=True)
    notifier_process.start()

    # Listen for notifications
    alive = True
    try:
        for i in range(50):  # 50 minutes
            if select.select([conn], [], [], 60) == ([], [], []):
                log(f"Minute {i+1}/50: Waiting for notification...")
            else:
                # psycopg3 uses conn.notifies() generator
                for notify in conn.notifies():
                    log(f"Minute {i+1}/50: Received notification: {notify.payload}")

    except Exception as e:
        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds() / 60
        log(f"LISTEN FAILED after {duration:.2f} minutes")
        log(f"Error: {e}")
        alive = False

    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds() / 60

    log(f"Test finished at: {finished_at.isoformat()}")
    log(f"Connection status: {'ALIVE' if alive else 'DEAD'}")

    try:
        with conn.cursor() as cursor:
            cursor.execute(f"UNLISTEN {channel_name}")
        conn.close()
    except:
        pass

    return {
        'test': 'Test 7 - LISTEN with Activity',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'duration_minutes': duration,
        'connection_alive': alive,
        'connection_pinned': True
    }

if __name__ == '__main__':
    import select  # Required for LISTEN polling
    result = test_7_listen_with_activity()
    print("\n=== RESULTS ===")
    print(result)
```

**Expected Outcome**: Periodic activity may keep pinned connection alive

**Data to Record**:
- Whether active LISTEN connection survived 50 minutes
- Comparison with idle LISTEN (Test 6)
- Number of notifications successfully received

**Why This Test Matters**:
- Tests if periodic task dispatch notifications keep connection alive
- Simulates production scenario where workers receive tasks intermittently
- Helps understand if "activity" on a pinned connection prevents timeout

---

## Step 5: Check for Connection Pinning

### Objective
Monitor RDS Proxy metrics to identify connection pinning correlation with timeouts.

### Procedure

**CloudWatch Metrics to Monitor**:

```bash
# Monitor these metrics during each test:
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnectionsCurrentlySessionPinned \
  --dimensions Name=DBProxyName,Value=your-proxy-name \
  --start-time 2025-10-30T00:00:00Z \
  --end-time 2025-10-30T23:59:59Z \
  --period 60 \
  --statistics Average,Maximum

aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name ClientConnections \
  --dimensions Name=DBProxyName,Value=your-proxy-name \
  --start-time 2025-10-30T00:00:00Z \
  --end-time 2025-10-30T23:59:59Z \
  --period 60 \
  --statistics Average,Maximum

aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnectionsBorrowLatency \
  --dimensions Name=DBProxyName,Value=your-proxy-name \
  --start-time 2025-10-30T00:00:00Z \
  --end-time 2025-10-30T23:59:59Z \
  --period 60 \
  --statistics Average,Maximum
```

### Analysis

Create a timeline correlation:

| Time | Test Running | Pinned Connections | Client Connections | Borrow Latency | Notes |
|------|--------------|-------------------|-------------------|----------------|-------|
| T+0 | Test 1 Start | 0 | 1 | <1ms | |
| T+40min | Test 1 Disconnect | ? | ? | ? | Check spike |
| T+0 | Test 2 Start | 0 | 1 | <1ms | |
| T+50min | Test 2 Complete | ? | ? | ? | Still alive? |

**Look for**:
- Pinned connection count during Test 5 (session variable)
- Correlation between pinning and disconnections
- Borrow latency spikes at disconnect time

---

## Step 6: Test with Modified IdleClientTimeout

### Objective
Confirm if IdleClientTimeout is the root cause by changing the value.

### Procedure

**Step 6.1: Check Current IdleClientTimeout**
```bash
aws rds describe-db-proxies --db-proxy-name your-proxy-name \
  | jq '.DBProxies[0].IdleClientTimeout'
```

**Step 6.2: Modify IdleClientTimeout to 3600 seconds (60 minutes)**
```bash
aws rds modify-db-proxy \
  --db-proxy-name your-proxy-name \
  --idle-client-timeout 3600
```

**Step 6.3: Wait for change to apply**
(May take a few minutes)

**Step 6.4: Rerun Test 1 (Idle Connection)**
```bash
python3 test_1_idle_connection.py
```

**Step 6.5: Record Results**

| Configuration | Timeout Value | Disconnect Time | Notes |
|--------------|---------------|----------------|-------|
| Original | ? seconds | ~40 minutes | Baseline |
| Modified | 3600 seconds (60 min) | ? minutes | Expected: ~60 min |

**Expected Outcome**:
- If disconnect moves to ~60 minutes, IdleClientTimeout confirmed as cause
- If disconnect still at ~40 minutes, look elsewhere (PostgreSQL settings, application behavior)

**Step 6.6: Restore Original Setting** (if needed)
```bash
aws rds modify-db-proxy \
  --db-proxy-name your-proxy-name \
  --idle-client-timeout <original-value>
```

---

## Step 7: Analyze Pulp Worker Behavior

### Objective
Understand what Pulp workers are actually doing during their ~40 minute lifetime.

### Procedure

**Step 7.1: Enable Django Query Logging**

Add to Django settings (temporarily):
```python
LOGGING = {
    'version': 1,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'DEBUG',
        },
    },
}
```

**Step 7.2: Monitor Active Queries**

Run this query periodically during worker lifetime:
```sql
SELECT
    pid,
    usename,
    application_name,
    client_addr,
    state,
    now() - query_start as query_duration,
    now() - state_change as state_duration,
    query
FROM pg_stat_activity
WHERE datname = 'pulp'
  AND usename = 'pulp'
ORDER BY query_start DESC;
```

**Step 7.3: Check for Session Variables**

Look for these in logs:
```sql
-- Look for session-level settings in logs
SELECT query
FROM pg_stat_statements
WHERE query ILIKE '%SET %'
ORDER BY calls DESC;
```

**Step 7.4: Analyze Worker Connection Pattern**

Track for one worker lifecycle:
- Connection established time
- First query time
- Pattern of queries (frequency, type)
- Any SET statements
- Transaction boundaries (BEGIN/COMMIT)
- Idle periods
- Final query before disconnect
- Disconnect time

**Template**:
```
Worker Lifecycle Analysis
-------------------------
Connection Start: 2025-10-30T10:00:00Z
First Query: 2025-10-30T10:00:05Z (SELECT FROM core_task)
Session Variable: SET TIMEZONE TO 'UTC' (10:00:06Z)
Activity Pattern:
  - 10:00:00 - 10:05:00: Task processing (5 queries)
  - 10:05:00 - 10:35:00: Idle (heartbeat only, every 5 min)
  - 10:35:00 - 10:40:00: Task processing (3 queries)
  - 10:40:00 - disconnect
Disconnect Time: 2025-10-30T10:40:16Z
Total Duration: 40 minutes 16 seconds
```

---

## Data Collection Summary Template

### Test Results Table

| Test ID | Description | Duration | Disconnect? | Disconnect Time | Error Message | Pinned? |
|---------|-------------|----------|-------------|----------------|---------------|---------|
| 1 | Idle Connection | 50 min | Yes/No | mm:ss | | No |
| 2 | Active Heartbeat | 50 min | Yes/No | mm:ss | | No |
| 3 | Long Transaction (idle) | 50 min | Yes/No | mm:ss | | No |
| 4 | Transaction with Active Work | 50 min | Yes/No | mm:ss | | No |
| 5 | Session Variable (SET TIMEZONE) | 50 min | Yes/No | mm:ss | | Yes |
| 6 | LISTEN/NOTIFY (idle) | 50 min | Yes/No | mm:ss | | Yes |
| 7 | LISTEN with Activity | 50 min | Yes/No | mm:ss | | Yes |
| 8 | Modified Timeout (Step 6) | 70 min | Yes/No | mm:ss | | |

### Configuration Data

```
RDS Proxy Configuration:
- Proxy Name:
- IdleClientTimeout: _____ seconds
- MaxConnectionsPercent: _____%
- MaxIdleConnectionsPercent: _____%
- RequireTLS: Yes/No

PostgreSQL Settings:
- idle_in_transaction_session_timeout: _____ ms
- statement_timeout: _____ ms
- tcp_keepalives_idle: _____ seconds
- tcp_keepalives_interval: _____ seconds

Pulp Worker Configuration:
- Worker heartbeat interval: _____ seconds
- Django CONN_MAX_AGE: _____ seconds
- Time zone setting: _____
```

---

## Expected Root Cause Analysis

Based on test results, we should be able to determine:

**If Test 1 fails at ~40 min AND Test 8 (Step 6) fails at new timeout**:
- **Root Cause**: RDS Proxy IdleClientTimeout
- **Solution**: Increase timeout or implement connection refresh

**If Test 2 succeeds**:
- **Root Cause**: Idle connection timeout (confirmed)
- **Solution**: Implement periodic heartbeat or connection refresh

**If Test 3 fails before Test 1**:
- **Root Cause**: PostgreSQL idle_in_transaction_timeout
- **Solution**: Adjust PostgreSQL settings or reduce transaction duration

**If Test 5 shows different behavior**:
- **Root Cause**: Connection pinning affecting timeout
- **Solution**: Avoid session variables or implement connection pooling strategy

**If Test 6 (LISTEN idle) fails at ~40 min**:
- **Root Cause**: Pinned connections have same IdleClientTimeout as unpinned
- **Critical Finding**: LISTEN/NOTIFY (how Pulp workers work) incompatible with RDS Proxy's timeout settings
- **Solution**: Either increase IdleClientTimeout globally OR redesign worker notification system
- **Impact**: This is the REAL production scenario - every Pulp worker uses LISTEN

**If Test 6 fails BUT Test 7 succeeds**:
- **Root Cause**: LISTEN connections need periodic activity to stay alive
- **Solution**: Workers could send/receive periodic NOTIFY "heartbeats" to keep connection active
- **Note**: This would be a workaround, not addressing root pinning issue

**If Tests 6 and 7 both fail at same time as Test 1**:
- **Root Cause**: IdleClientTimeout applies uniformly regardless of pinning
- **Critical Decision**: Either accept ~40 min worker lifetimes OR increase global timeout
- **Implication for PULP-674**: This confirms LISTEN/NOTIFY architecture is incompatible with RDS Proxy

**If Tests 6/7 succeed but Test 1 fails**:
- **Surprising Finding**: Pinned connections treated differently than unpinned
- **Need Investigation**: Why would RDS Proxy have different timeout behavior for pinned connections?

---

## Recommended Next Steps After Testing

1. **Document all findings** in PULP-955
2. **Update RDS Proxy report** with root cause analysis
3. **Propose solution**:
   - Configuration changes (increase timeout)
   - Application changes (connection refresh, avoid pinning)
   - Architecture changes (connection pooling strategy)
4. **Create follow-up ticket** for implementation
5. **Share findings** with Tasking Working Group (relates to PULP-674)

---

**Test Plan Owner**: Andre Brito (ddebrito@redhat.com)

**Created**: October 30, 2025

**Status**: Ready to Execute
