# pulp_benchmark/plugins/rds_connection_tests.py
"""
Plugin to trigger RDS Proxy connection timeout tests remotely.

This plugin dispatches RDS connection tests to a Pulp instance and monitors their progress.
Tests run for approximately 50 minutes each to identify RDS Proxy timeout issues.

Related to PULP-955: Pulp workers disconnect from RDS Proxy after ~40-46 minutes
"""
import asyncio
import logging
import time
from typing import List, Optional

import aiohttp
import click

# Import shared connection utilities
from pulp_benchmark.client_async import create_session

logger = logging.getLogger(__name__)


AVAILABLE_TESTS = {
    "test_1_idle_connection": "Idle connection test (50 min) - baseline timeout test",
    "test_2_active_heartbeat": "Active heartbeat test (50 min) - periodic queries",
    "test_3_long_transaction": "Long transaction test (50 min) - idle transaction",
    "test_4_transaction_with_work": "Transaction with work test (50 min) - active transaction",
    "test_5_session_variable": "Session variable test (50 min) - connection pinning via SET",
    "test_6_listen_notify": "LISTEN/NOTIFY test (50 min) - CRITICAL: real worker behavior",
    "test_7_listen_with_activity": "LISTEN with activity test (50 min) - periodic notifications",
}


async def dispatch_tests(
    api_root: str,
    tests: List[str],
    user: Optional[str] = None,
    password: Optional[str] = None,
    cert: Optional[str] = None,
    key: Optional[str] = None,
    verify_ssl: bool = True,
) -> dict:
    """
    Dispatch RDS connection tests to the Pulp instance.

    Args:
        api_root: Pulp API root URL
        tests: List of test names to run
        user: Username for authentication
        password: Password for authentication
        cert: Path to client certificate
        key: Path to client private key
        verify_ssl: Whether to verify SSL certificates

    Returns:
        dict: Response from the API with dispatched task information
    """
    # Extract base URL from api_root (protocol + domain)
    # /api/pulp/... endpoints are absolute paths, not relative to api_root
    from urllib.parse import urlparse
    parsed = urlparse(api_root)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    endpoint = f"{base_url}/api/pulp/rds-connection-tests/"

    payload = {
        "tests": tests,
        "run_sequentially": False  # All tests run in parallel by default
    }

    logger.info(f"Dispatching to endpoint: {endpoint}")
    logger.info(f"Authentication: {'Basic Auth' if user and password else 'Cert Auth' if cert else 'None'}")
    logger.info(f"SSL Verification: {verify_ssl}")

    try:
        async with create_session(user, password, cert, key, verify_ssl) as session:
            async with session.post(endpoint, json=payload) as response:
                logger.info(f"Response status: {response.status}")
                logger.info(f"Response headers: {dict(response.headers)}")
                response.raise_for_status()
                data = await response.json()
                return data
    except aiohttp.ClientResponseError as e:
        logger.error(f"HTTP {e.status} error: {e.message}")
        logger.error(f"Response headers: {e.headers}")
        if e.status == 403:
            logger.error("403 Forbidden - Check authentication credentials and permissions")
        raise
    except aiohttp.ClientError as e:
        logger.error(f"Failed to dispatch tests: {e}")
        raise


async def check_task_status(
    api_root: str,
    task_href: str,
    user: Optional[str] = None,
    password: Optional[str] = None,
    cert: Optional[str] = None,
    key: Optional[str] = None,
    verify_ssl: bool = True,
) -> dict:
    """
    Check the status of a Pulp task.

    Args:
        api_root: Pulp API root URL
        task_href: Task href path (e.g., /pulp/api/v3/tasks/{id}/ or /pulp/default/api/v3/tasks/{id}/)
        user: Username for authentication
        password: Password for authentication
        cert: Path to client certificate
        key: Path to client private key
        verify_ssl: Whether to verify SSL certificates

    Returns:
        dict: Task status information
    """
    # For normal Pulp endpoints, use api_root + task_href
    # api_root might be something like https://example.com/api
    endpoint = f"{api_root}{task_href}"

    try:
        async with create_session(user, password, cert, key, verify_ssl) as session:
            async with session.get(endpoint) as response:
                response.raise_for_status()
                data = await response.json()
                return data
    except aiohttp.ClientError as e:
        logger.error(f"Failed to check task status: {e}")
        raise


async def monitor_tasks(
    api_root: str,
    tasks: List[dict],
    user: Optional[str] = None,
    password: Optional[str] = None,
    cert: Optional[str] = None,
    key: Optional[str] = None,
    poll_interval: int = 60,
    verify_ssl: bool = True,
):
    """
    Monitor the progress of dispatched tasks.

    Args:
        api_root: Pulp API root URL
        tasks: List of task dictionaries with task_href
        user: Username for authentication
        password: Password for authentication
        cert: Path to client certificate
        key: Path to client private key
        poll_interval: How often to poll for status (seconds)
        verify_ssl: Whether to verify SSL certificates
    """
    logger.info(f"Monitoring {len(tasks)} task(s)...")
    logger.info("Note: Each test runs for approximately 50 minutes")

    pending_tasks = {task['task_id']: task for task in tasks}
    completed_tasks = {}  # Track completed tasks with their results
    start_time = time.time()

    while pending_tasks:
        await asyncio.sleep(poll_interval)

        elapsed = time.time() - start_time
        elapsed_mins = int(elapsed / 60)

        logger.info(f"--- Status Check (T+{elapsed_mins} minutes) ---")

        completed = []

        for task_id, task_info in pending_tasks.items():
            try:
                status = await check_task_status(
                    api_root, task_info['task_href'], user, password, cert, key, verify_ssl
                )

                state = status.get('state', 'unknown')
                test_name = task_info['test_name']

                if state in ['completed', 'failed', 'canceled']:
                    logger.info(f"  {test_name}: {state.upper()}")

                    # Store completion info
                    completed_tasks[task_id] = {
                        'test_name': test_name,
                        'state': state,
                        'status': status
                    }

                    if state == 'completed':
                        # Try to extract results
                        if 'result' in status:
                            result = status['result']
                            logger.info(f"    Status: {result.get('status', 'UNKNOWN')}")
                            logger.info(f"    Duration: {result.get('duration_minutes', 0)} minutes")
                            logger.info(f"    Connection Alive: {result.get('connection_alive', 'unknown')}")
                            if 'backend_pid' in result:
                                logger.info(f"    Backend PID: {result.get('backend_pid')}")
                    completed.append(task_id)
                else:
                    logger.info(f"  {test_name}: {state}")

            except Exception as e:
                logger.error(f"  Error checking {task_info['test_name']}: {e}")

        # Remove completed tasks
        for task_id in completed:
            del pending_tasks[task_id]

        if pending_tasks:
            logger.info(f"{len(pending_tasks)} task(s) still running...")

    # Return completed tasks for summary
    return completed_tasks


@click.command()
@click.option(
    "--tests",
    "-t",
    multiple=True,
    type=click.Choice(list(AVAILABLE_TESTS.keys())),
    help="Test(s) to run. Can be specified multiple times. Use 'all' to run all tests."
)
@click.option(
    "--all",
    "run_all",
    is_flag=True,
    help="Run all available tests"
)
@click.option(
    "--list",
    "list_tests",
    is_flag=True,
    help="List all available tests and exit"
)
@click.option(
    "--monitor/--no-monitor",
    default=True,
    show_default=True,
    help="Monitor task progress until completion"
)
@click.option(
    "--poll-interval",
    type=int,
    default=60,
    show_default=True,
    help="Interval in seconds to poll for task status when monitoring"
)
@click.pass_context
def rds_connection_tests(
    ctx,
    tests: tuple,
    run_all: bool,
    list_tests: bool,
    monitor: bool,
    poll_interval: int
):
    """
    Trigger RDS Proxy connection timeout tests remotely.

    This command dispatches RDS connection tests to identify timeout issues
    with RDS Proxy. Each test runs for approximately 50 minutes.

    Examples:

      # List all available tests
      pulp-benchmark --api-root https://pulp.example.com rds_connection_tests --list

      # Run a single test
      pulp-benchmark --api-root https://pulp.example.com rds_connection_tests -t test_1_idle_connection

      # Run multiple specific tests
      pulp-benchmark --api-root https://pulp.example.com rds_connection_tests -t test_1_idle_connection -t test_2_active_heartbeat

      # Run all tests
      pulp-benchmark --api-root https://pulp.example.com rds_connection_tests --all

      # Dispatch without monitoring
      pulp-benchmark --api-root https://pulp.example.com rds_connection_tests -t test_6_listen_notify --no-monitor
    """

    # List tests and exit
    if list_tests:
        click.echo("\n=== Available RDS Connection Tests ===\n")
        for test_name, description in AVAILABLE_TESTS.items():
            click.echo(f"  {test_name}")
            click.echo(f"    {description}\n")
        return

    # Determine which tests to run
    if run_all:
        selected_tests = list(AVAILABLE_TESTS.keys())
        click.echo(f"Running all {len(selected_tests)} tests")
    elif tests:
        selected_tests = list(tests)
    else:
        click.echo("Error: Must specify --tests, --all, or --list")
        click.echo("Use --help for usage information")
        return

    # Get context
    api_root = ctx.obj['api_root']
    user = ctx.obj['user']
    password = ctx.obj['password']
    cert = ctx.obj['cert']
    key = ctx.obj['key']
    verify_ssl = ctx.obj['verify_ssl']

    click.echo(f"\nDispatching {len(selected_tests)} test(s) to {api_root}")
    click.echo("Tests to run:")
    for test in selected_tests:
        click.echo(f"  - {test}: {AVAILABLE_TESTS[test]}")
    click.echo("")

    # Dispatch tests
    async def run_dispatch():
        try:
            result = await dispatch_tests(
                api_root, selected_tests, user, password, cert, key, verify_ssl
            )

            click.echo(f"\n✓ Successfully dispatched {len(result['tasks'])} test(s)\n")

            # Display task information
            click.echo("Dispatched Tasks:")
            for task in result['tasks']:
                click.echo(f"  - {task['test_name']}")
                click.echo(f"    Task ID: {task['task_id']}")
                # Task URLs use api_root (normal Pulp endpoints)
                click.echo(f"    Task URL: {api_root}{task['task_href']}\n")

            # Monitor if requested
            if monitor:
                click.echo("\nStarting task monitoring...")
                click.echo(f"Polling every {poll_interval} seconds")
                click.echo("Press Ctrl+C to stop monitoring (tasks will continue running)\n")

                try:
                    completed_tasks = await monitor_tasks(
                        api_root, result['tasks'], user, password, cert, key, poll_interval, verify_ssl
                    )
                    click.echo("\n✓ All tasks completed!")

                    # Display summary
                    click.echo("\n" + "=" * 60)
                    click.echo("TEST SUMMARY")
                    click.echo("=" * 60)

                    passed_tests = []
                    failed_tests = []

                    for task_id, task_data in completed_tasks.items():
                        test_name = task_data['test_name']
                        state = task_data['state']
                        status_obj = task_data['status']

                        if state == 'completed':
                            result_data = status_obj.get('result', {})
                            test_status = result_data.get('status', 'UNKNOWN')
                            connection_alive = result_data.get('connection_alive', 'unknown')
                            duration = result_data.get('duration_minutes', 0)
                            backend_pid = result_data.get('backend_pid', 'N/A')

                            if test_status == 'PASSED':
                                passed_tests.append({
                                    'name': test_name,
                                    'duration': duration,
                                    'backend_pid': backend_pid,
                                    'connection_alive': connection_alive
                                })
                            else:
                                failed_tests.append({
                                    'name': test_name,
                                    'duration': duration,
                                    'backend_pid': backend_pid,
                                    'connection_alive': connection_alive,
                                    'error': result_data.get('error', 'Unknown error')
                                })
                        else:
                            # Task failed or was canceled
                            failed_tests.append({
                                'name': test_name,
                                'duration': 0,
                                'backend_pid': 'N/A',
                                'connection_alive': False,
                                'error': f"Task {state}"
                            })

                    # Display passed tests
                    if passed_tests:
                        click.echo(f"\n✓ PASSED ({len(passed_tests)}):")
                        for test in passed_tests:
                            click.echo(f"  • {test['name']}")
                            click.echo(f"    Duration: {test['duration']} min | Backend PID: {test['backend_pid']} | Connection: {'ALIVE' if test['connection_alive'] else 'DEAD'}")

                    # Display failed tests
                    if failed_tests:
                        click.echo(f"\n✗ FAILED ({len(failed_tests)}):")
                        for test in failed_tests:
                            click.echo(f"  • {test['name']}")
                            click.echo(f"    Duration: {test['duration']} min | Backend PID: {test['backend_pid']} | Connection: {'ALIVE' if test['connection_alive'] else 'DEAD'}")
                            if isinstance(test['error'], dict):
                                click.echo(f"    Error: {test['error'].get('type', 'Unknown')}: {test['error'].get('message', 'No message')}")
                            else:
                                click.echo(f"    Error: {test['error']}")

                    # Final stats
                    total = len(passed_tests) + len(failed_tests)
                    click.echo(f"\n{'-' * 60}")
                    click.echo(f"Total: {total} | Passed: {len(passed_tests)} | Failed: {len(failed_tests)}")
                    click.echo("=" * 60)

                except KeyboardInterrupt:
                    click.echo("\n\nMonitoring stopped. Tasks are still running on the server.")
                    click.echo("Check task status manually using the task URLs above.")
            else:
                click.echo("Note: Tasks are running on the server (not monitoring)")
                click.echo("Use the task URLs above to check status manually")

        except Exception as e:
            click.echo(f"\n✗ Error: {e}", err=True)
            raise click.Abort()

    # Run async function
    asyncio.run(run_dispatch())
