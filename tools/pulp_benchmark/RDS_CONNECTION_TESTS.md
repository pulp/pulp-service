# RDS Proxy Connection Tests - Execution Guide

**Related Ticket**: [PULP-955](https://issues.redhat.com/browse/PULP-955)
**Test Plan**: See `rds-proxy-disconnection-test-plan.md`

## Overview

This document provides instructions for running RDS Proxy connection timeout tests using Pulp-style tasks implemented in Django ORM.

All test tasks are located in: `pulp_service/pulp_service/app/tasks/rds_connection_tests.py`

## Prerequisites

- Access to pulp-prod or pulp-perf environment with RDS Proxy enabled
- Ability to run Django management shell or dispatch Pulp tasks
- CloudWatch Logs access for error monitoring
- Ability to run tests for 50+ minutes each
- RDS Proxy endpoint configured in Django settings

## Test Execution Methods

### Method 1: Django Management Shell (Recommended for Quick Tests)

```bash
# SSH into the Pulp environment
# Access Django shell
pulpcore-manager shell

# Import the test tasks
from pulp_service.app.tasks.rds_connection_tests import (
    test_1_idle_connection,
    test_2_active_heartbeat,
    test_3_long_transaction,
    test_4_transaction_with_work,
    test_5_session_variable,
    test_6_listen_notify,
    test_7_listen_with_activity,
)

# Run a single test (this will block for 50 minutes)
result = test_1_idle_connection()
print(result)
```

### Method 2: Dispatch as Pulp Tasks (Recommended for Production-Like Testing)

```python
# In Django shell or Python script
from pulpcore.tasking.tasks import dispatch

# Dispatch Test 1
task = dispatch(
    func="pulp_service.app.tasks.rds_connection_tests.test_1_idle_connection",
    exclusive_resources=[],  # No resource locking needed
)
print(f"Task dispatched: {task.pulp_href}")

# Monitor task status
from pulpcore.app.models import Task
task = Task.objects.get(pk=task.pk)
print(f"State: {task.state}")
print(f"Progress: {task.progress_reports}")
```

### Method 3: Direct Python Script

Create a script that can be run directly:

```python
#!/usr/bin/env python3
import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pulpcore.app.settings')
django.setup()

# Import and run test
from pulp_service.app.tasks.rds_connection_tests import test_1_idle_connection

if __name__ == '__main__':
    result = test_1_idle_connection()
    print("\n=== FINAL RESULTS ===")
    print(result)
```

## Test Execution Order

Run tests in this sequence to build understanding progressively:

1. **Test 1**: Idle Connection (baseline) - `test_1_idle_connection()`
2. **Test 2**: Active Heartbeat - `test_2_active_heartbeat()`
3. **Test 3**: Long Transaction Idle - `test_3_long_transaction()`
4. **Test 4**: Transaction with Active Work - `test_4_transaction_with_work()`
5. **Test 5**: Session Variable - `test_5_session_variable()`
6. **Test 6**: LISTEN/NOTIFY Idle - `test_6_listen_notify()` ⚠️ **CRITICAL**
7. **Test 7**: LISTEN with Activity - `test_7_listen_with_activity()`

## Individual Test Descriptions

### Test 1: Idle Connection
```python
result = test_1_idle_connection()
```
- **Duration**: 50 minutes
- **Purpose**: Baseline test for idle connection timeout
- **Expected**: Connection dies at ~40 minutes (IdleClientTimeout)

### Test 2: Active Heartbeat
```python
result = test_2_active_heartbeat()
```
- **Duration**: 50 minutes
- **Purpose**: Test if periodic queries prevent timeout
- **Expected**: Connection stays alive for full duration

### Test 3: Long Transaction
```python
result = test_3_long_transaction()
```
- **Duration**: 50 minutes
- **Purpose**: Test long-running idle transaction behavior
- **Expected**: May timeout due to idle_in_transaction_timeout

### Test 4: Transaction with Active Work
```python
result = test_4_transaction_with_work()
```
- **Duration**: 50 minutes
- **Purpose**: Test transaction with periodic queries
- **Expected**: Active work should prevent idle_in_transaction_timeout

### Test 5: Session Variable
```python
result = test_5_session_variable()
```
- **Duration**: 50 minutes
- **Purpose**: Test connection pinning via SET TIMEZONE
- **Expected**: Connection pinned, may show different timeout behavior

### Test 6: LISTEN/NOTIFY (CRITICAL)
```python
result = test_6_listen_notify()
```
- **Duration**: 50 minutes
- **Purpose**: Test how Pulp workers actually work - LISTEN connection
- **Expected**: Connection pinned, critical to understand timeout
- **Importance**: ⚠️ This is the MOST IMPORTANT test - it simulates real Pulp worker behavior

### Test 7: LISTEN with Activity
```python
result = test_7_listen_with_activity()
```
- **Duration**: 50 minutes
- **Purpose**: Test if LISTEN + periodic notifications keeps connection alive
- **Note**: Requires manual NOTIFY commands from another session

To send test notifications (from separate shell):
```python
from pulp_service.app.tasks.rds_connection_tests import send_test_notification

# Send a notification every 2 minutes manually, or in a loop:
import time
for i in range(25):
    send_test_notification(payload=f"heartbeat_{i}")
    time.sleep(120)
```

## Monitoring and Data Collection

### Check Task Progress

```python
# Monitor running task
from pulpcore.app.models import Task
task = Task.objects.get(pk='<task-uuid>')
print(f"State: {task.state}")
print(f"Started: {task.started_at}")
print(f"Duration: {datetime.now() - task.started_at}")
```

### View Task Logs

```bash
# View task logs (adjust log location as needed)
tail -f /var/log/pulp/worker*.log | grep "TEST"
```

### CloudWatch Metrics

Monitor these RDS Proxy metrics during tests:

```bash
# Check pinned connections
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnectionsCurrentlySessionPinned \
  --dimensions Name=DBProxyName,Value=<your-proxy-name> \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average,Maximum

# Check client connections
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name ClientConnections \
  --dimensions Name=DBProxyName,Value=<your-proxy-name> \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average,Maximum
```

### Database Connection Monitoring

```sql
-- Monitor active connections during test
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

## Results Recording

Each test returns a dictionary with results:

```python
{
    'test': 'Test Name',
    'started_at': '2025-10-30T10:00:00.000000',
    'finished_at': '2025-10-30T10:50:00.000000',
    'duration_minutes': 50.0,
    'connection_alive': True/False,
    'connection_pinned': True/False,  # For Tests 5, 6, 7
    'iterations_completed': 50  # For Tests 2, 4
}
```

### Create Results Summary Table

| Test ID | Description | Duration | Disconnect? | Disconnect Time | Error Message | Pinned? |
|---------|-------------|----------|-------------|----------------|---------------|---------|
| 1 | Idle Connection | 50 min | Yes/No | mm:ss | | No |
| 2 | Active Heartbeat | 50 min | Yes/No | mm:ss | | No |
| 3 | Long Transaction (idle) | 50 min | Yes/No | mm:ss | | No |
| 4 | Transaction with Active Work | 50 min | Yes/No | mm:ss | | No |
| 5 | Session Variable (SET TIMEZONE) | 50 min | Yes/No | mm:ss | | Yes |
| 6 | LISTEN/NOTIFY (idle) | 50 min | Yes/No | mm:ss | | Yes |
| 7 | LISTEN with Activity | 50 min | Yes/No | mm:ss | | Yes |

## Troubleshooting

### Task Fails Immediately

Check Django database settings:
```python
from django.conf import settings
print(settings.DATABASES['default'])
```

Ensure RDS Proxy endpoint is configured correctly.

### Task Hangs

- This is expected - tests run for 50 minutes
- Monitor via logs or separate shell session
- Check `pg_stat_activity` to verify test is running

### Connection Already Dead

- Verify RDS Proxy is accessible
- Check current IdleClientTimeout setting
- Review recent CloudWatch logs for errors

### Test 7 (LISTEN with Activity) Notes

This test requires manual intervention to send NOTIFY commands. Options:

1. **Manual**: Run `send_test_notification()` from another shell every 2 minutes
2. **Scripted**: Create a background job to send notifications automatically
3. **Modified Test**: Modify test to spawn a thread/process to send notifications

## Expected Outcomes and Root Cause Analysis

See `rds-proxy-disconnection-test-plan.md` Section "Expected Root Cause Analysis" for interpretation of results.

**Key Decision Points:**

- **If Test 1 fails at ~40 min**: IdleClientTimeout is the cause
- **If Test 2 succeeds**: Heartbeat keeps connection alive
- **If Test 6 fails at ~40 min**: CRITICAL - LISTEN/NOTIFY incompatible with current RDS Proxy settings
- **If Test 7 succeeds but Test 6 fails**: Periodic activity on pinned connections helps

## Next Steps After Testing

1. Document all findings in PULP-955
2. Update RDS Proxy report with root cause analysis
3. Propose solution (configuration vs. application vs. architecture changes)
4. Create follow-up tickets for implementation
5. Share findings with Tasking Working Group (relates to PULP-674)

## Quick Reference Commands

```python
# Import all tests
from pulp_service.app.tasks.rds_connection_tests import *

# Run all tests sequentially (will take ~6 hours!)
tests = [
    test_1_idle_connection,
    test_2_active_heartbeat,
    test_3_long_transaction,
    test_4_transaction_with_work,
    test_5_session_variable,
    test_6_listen_notify,
    test_7_listen_with_activity,
]

results = []
for test_func in tests:
    print(f"\n{'='*60}")
    print(f"Running {test_func.__name__}")
    print(f"{'='*60}\n")
    result = test_func()
    results.append(result)
    print(f"\nResult: {result}")

# Print summary
print("\n\n=== ALL RESULTS ===")
for r in results:
    print(r)
```
