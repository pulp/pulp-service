# RDS Connection Tests - Remote Trigger Guide

**Related Ticket**: [PULP-955](https://issues.redhat.com/browse/PULP-955)

This guide explains how to trigger RDS Proxy connection timeout tests remotely using either direct API calls or the pulp_benchmark CLI tool.

## Overview

Two methods are available to trigger RDS connection tests remotely:

1. **Direct API calls** - Using curl, httpie, or any HTTP client
2. **pulp_benchmark CLI** - Automated tool with monitoring capabilities

## Method 1: Direct API Calls

### API Endpoint

```
POST /api/pulp/rds-connection-tests/
GET  /api/pulp/rds-connection-tests/
```

### Get Available Tests (GET)

```bash
curl -X GET https://your-pulp-instance.com/api/pulp/rds-connection-tests/
```

Response:
```json
{
  "available_tests": [
    "test_1_idle_connection",
    "test_2_active_heartbeat",
    "test_3_long_transaction",
    "test_4_transaction_with_work",
    "test_5_session_variable",
    "test_6_listen_notify",
    "test_7_listen_with_activity"
  ],
  "descriptions": {
    "test_1_idle_connection": "Idle connection test (50 min) - baseline timeout test",
    "test_2_active_heartbeat": "Active heartbeat test (50 min) - periodic queries",
    ...
  },
  "usage": {
    "endpoint": "/api/pulp/rds-connection-tests/",
    "method": "POST",
    "body": {
      "tests": ["test_1_idle_connection", "test_2_active_heartbeat"],
      "run_sequentially": false
    }
  }
}
```

### Dispatch Tests (POST)

#### Request Format

```json
{
  "tests": [
    "test_1_idle_connection",
    "test_2_active_heartbeat"
  ],
  "run_sequentially": false
}
```

#### Example: Dispatch Single Test

```bash
curl -X POST https://your-pulp-instance.com/api/pulp/rds-connection-tests/ \
  -H "Content-Type: application/json" \
  -d '{
    "tests": ["test_1_idle_connection"]
  }'
```

#### Example: Dispatch Multiple Tests

```bash
curl -X POST https://your-pulp-instance.com/api/pulp/rds-connection-tests/ \
  -H "Content-Type: application/json" \
  -d '{
    "tests": [
      "test_1_idle_connection",
      "test_2_active_heartbeat",
      "test_6_listen_notify"
    ]
  }'
```

#### Example: Using httpie

```bash
http POST https://your-pulp-instance.com/api/pulp/rds-connection-tests/ \
  tests:='["test_1_idle_connection", "test_2_active_heartbeat"]'
```

#### Response

```json
{
  "message": "Dispatched 2 test(s)",
  "tasks": [
    {
      "test_name": "test_1_idle_connection",
      "task_id": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
      "task_href": "/pulp/api/v3/tasks/a1b2c3d4-5678-90ab-cdef-1234567890ab/"
    },
    {
      "test_name": "test_2_active_heartbeat",
      "task_id": "e5f6g7h8-9012-34ij-klmn-5678901234op",
      "task_href": "/pulp/api/v3/tasks/e5f6g7h8-9012-34ij-klmn-5678901234op/"
    }
  ],
  "run_sequentially": false,
  "note": "Each test runs for approximately 50 minutes. Monitor task status via task_href."
}
```

### Check Task Status

After dispatching, monitor task progress using the Pulp Tasks API:

```bash
curl https://your-pulp-instance.com/pulp/api/v3/tasks/{task_id}/
```

Example:
```bash
curl https://your-pulp-instance.com/pulp/api/v3/tasks/a1b2c3d4-5678-90ab-cdef-1234567890ab/
```

Response includes:
- `state`: "waiting", "running", "completed", "failed"
- `started_at`: When the task started
- `finished_at`: When the task completed
- `result`: Test results (if completed)

## Method 2: pulp_benchmark CLI (Recommended)

The pulp_benchmark tool provides an automated way to dispatch and monitor RDS tests.

### Installation

```bash
cd /path/to/pulp-service/tools/pulp_benchmark

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Usage

#### List Available Tests

```bash
python -m pulp_benchmark.main \
  --api-root https://your-pulp-instance.com \
  rds_connection_tests --list
```

Output:
```
=== Available RDS Connection Tests ===

  test_1_idle_connection
    Idle connection test (50 min) - baseline timeout test

  test_2_active_heartbeat
    Active heartbeat test (50 min) - periodic queries

  test_3_long_transaction
    Long transaction test (50 min) - idle transaction

  test_4_transaction_with_work
    Transaction with work test (50 min) - active transaction

  test_5_session_variable
    Session variable test (50 min) - connection pinning via SET

  test_6_listen_notify
    LISTEN/NOTIFY test (50 min) - CRITICAL: real worker behavior

  test_7_listen_with_activity
    LISTEN with activity test (50 min) - periodic notifications
```

#### Run Single Test

```bash
python -m pulp_benchmark.main \
  --api-root https://your-pulp-instance.com \
  --user admin \
  --password YOUR_PASSWORD \
  rds_connection_tests -t test_1_idle_connection
```

#### Run Multiple Tests

```bash
python -m pulp_benchmark.main \
  --api-root https://your-pulp-instance.com \
  --user admin \
  --password YOUR_PASSWORD \
  rds_connection_tests \
    -t test_1_idle_connection \
    -t test_2_active_heartbeat \
    -t test_6_listen_notify
```

#### Run All Tests

```bash
python -m pulp_benchmark.main \
  --api-root https://your-pulp-instance.com \
  --user admin \
  --password YOUR_PASSWORD \
  rds_connection_tests --all
```

#### Dispatch Without Monitoring

```bash
python -m pulp_benchmark.main \
  --api-root https://your-pulp-instance.com \
  --user admin \
  --password YOUR_PASSWORD \
  rds_connection_tests \
    -t test_6_listen_notify \
    --no-monitor
```

#### Using Environment Variables

```bash
export PULP_API_ROOT=https://your-pulp-instance.com
export PULP_USER=admin
export PULP_PASSWORD=your_password

python -m pulp_benchmark.main rds_connection_tests -t test_1_idle_connection
```

#### Using Client Certificates

```bash
python -m pulp_benchmark.main \
  --api-root https://your-pulp-instance.com \
  --cert /path/to/client.crt \
  --key /path/to/client.key \
  rds_connection_tests -t test_1_idle_connection
```

### Monitoring Output

When monitoring is enabled (default), you'll see progress updates:

```
Dispatching 1 test(s) to https://your-pulp-instance.com
Tests to run:
  - test_1_idle_connection: Idle connection test (50 min) - baseline timeout test

✓ Successfully dispatched 1 test(s)

Dispatched Tasks:
  - test_1_idle_connection
    Task ID: a1b2c3d4-5678-90ab-cdef-1234567890ab
    Task URL: https://your-pulp-instance.com/pulp/api/v3/tasks/a1b2c3d4-5678-90ab-cdef-1234567890ab/

Starting task monitoring...
Polling every 60 seconds
Press Ctrl+C to stop monitoring (tasks will continue running)

--- Status Check (T+0 minutes) ---
  test_1_idle_connection: running
1 task(s) still running...

--- Status Check (T+1 minutes) ---
  test_1_idle_connection: running
1 task(s) still running...

...

--- Status Check (T+50 minutes) ---
  test_1_idle_connection: COMPLETED
    Result: {'test': 'Test 1 - Django Idle Connection', 'connection_alive': False, ...}

✓ All tasks completed!
```

### Advanced Options

```bash
python -m pulp_benchmark.main \
  --api-root https://your-pulp-instance.com \
  rds_connection_tests \
    -t test_6_listen_notify \
    --poll-interval 120  # Check status every 2 minutes instead of 60 seconds
```

## Test Descriptions

### Test 1: Idle Connection
- **Duration**: 50 minutes
- **Purpose**: Baseline test for idle connection timeout
- **Expected**: Connection dies at ~40 minutes (IdleClientTimeout)

### Test 2: Active Heartbeat
- **Duration**: 50 minutes
- **Purpose**: Test if periodic queries prevent timeout
- **Expected**: Connection stays alive for full duration

### Test 3: Long Transaction
- **Duration**: 50 minutes
- **Purpose**: Test long-running idle transaction behavior
- **Expected**: May timeout due to idle_in_transaction_timeout

### Test 4: Transaction with Active Work
- **Duration**: 50 minutes
- **Purpose**: Test transaction with periodic queries
- **Expected**: Active work should prevent idle_in_transaction_timeout

### Test 5: Session Variable
- **Duration**: 50 minutes
- **Purpose**: Test connection pinning via SET TIMEZONE
- **Expected**: Connection pinned, may show different timeout behavior

### Test 6: LISTEN/NOTIFY ⚠️ CRITICAL
- **Duration**: 50 minutes
- **Purpose**: Test how Pulp workers actually work - LISTEN connection
- **Expected**: Connection pinned, critical to understand timeout
- **Importance**: This is the MOST IMPORTANT test - simulates real Pulp worker behavior

### Test 7: LISTEN with Activity
- **Duration**: 50 minutes
- **Purpose**: Test if LISTEN + periodic notifications keeps connection alive
- **Note**: Requires manual NOTIFY commands from another session

## Recommended Test Sequence

Run tests in this order to build understanding progressively:

1. `test_1_idle_connection` - Baseline
2. `test_2_active_heartbeat` - Does activity help?
3. `test_3_long_transaction` - Transaction behavior
4. `test_4_transaction_with_work` - Transaction + activity
5. `test_5_session_variable` - Connection pinning via SET
6. `test_6_listen_notify` - **CRITICAL** - How Pulp workers actually work
7. `test_7_listen_with_activity` - Pinned connection + activity

## Example Workflow

### Quick Test (Single Test)

```bash
# 1. Dispatch test
python -m pulp_benchmark.main \
  --api-root https://pulp-prod.example.com \
  rds_connection_tests -t test_1_idle_connection

# Monitor automatically until completion (50 minutes)
```

### Complete Test Suite

```bash
# Run all 7 tests (will take ~6 hours total, but runs in parallel)
python -m pulp_benchmark.main \
  --api-root https://pulp-prod.example.com \
  rds_connection_tests --all
```

### Critical Production Test

```bash
# Test the most important scenario - how workers actually behave
python -m pulp_benchmark.main \
  --api-root https://pulp-prod.example.com \
  rds_connection_tests -t test_6_listen_notify
```

## Troubleshooting

### Error: "RDS connection tests are not enabled"

The view requires either DEBUG mode or `RDS_CONNECTION_TESTS_ENABLED` setting:

```python
# In Django settings
DEBUG = True
# OR
RDS_CONNECTION_TESTS_ENABLED = True
```

### Authentication Errors

Ensure you're using valid credentials or certificates:

```bash
# With username/password
python -m pulp_benchmark.main \
  --api-root https://pulp.example.com \
  --user admin \
  --password your_password \
  rds_connection_tests --list

# With client certificates
python -m pulp_benchmark.main \
  --api-root https://pulp.example.com \
  --cert /path/to/cert.pem \
  --key /path/to/key.pem \
  rds_connection_tests --list
```

### Connection Timeout

Tests take 50 minutes to run. The monitoring will poll every 60 seconds by default. You can:

1. Increase poll interval to reduce API calls
2. Disable monitoring and check manually later
3. Press Ctrl+C to stop monitoring (tasks continue running)

## Data Collection

After tests complete, collect the following data:

1. **Task Results**: Check task result field for connection status
2. **CloudWatch Metrics**: Monitor RDS Proxy metrics during test
3. **Database Logs**: Check PostgreSQL logs for connection errors
4. **Task Timing**: Record exact disconnect times

See `rds-proxy-disconnection-test-plan.md` for complete data collection guidance.

## Next Steps

1. Run tests according to recommended sequence
2. Document results in PULP-955
3. Analyze CloudWatch metrics for connection pinning
4. Compare results across different test types
5. Determine root cause and propose solution

## Related Documentation

- `rds-proxy-disconnection-test-plan.md` - Detailed test plan
- `RDS_CONNECTION_TESTS.md` - Local execution guide
- `pulp_service/app/tasks/rds_connection_tests.py` - Test implementations
- `pulp_service/app/viewsets.py` - API endpoint implementation
