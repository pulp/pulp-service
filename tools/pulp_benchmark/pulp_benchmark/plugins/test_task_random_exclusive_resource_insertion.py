# pulp_benchmark/plugins/test_task_insertion.py
import asyncio
import logging
import time
from typing import Optional

import click

# Import both async and sync logic
from ..client_async import run_concurrent_requests as run_concurrent_async
from ..client_sync import run_concurrent_requests_sync

@click.command()
@click.option("--timeout", type=int, default=30, show_default=True, help="Timeout for each task-creation request.")
@click.option("--max-workers", type=int, default=50, show_default=True, help="Number of concurrent requests/threads.")
@click.option("--run-until", type=int, help="Keep creating tasks until this total is reached.")
@click.pass_context
def test_task_random_exclusive_resource_insertion(ctx, timeout: int, max_workers: int, run_until: Optional[int]):
    """Run the task insertion load test."""
    client_type = ctx.obj['client_type']
    api_root = ctx.obj['api_root']
    test_url = f"{api_root}/pulp/test/random_lock_tasks/"
    
    logging.info(f"Starting task insertion test on {test_url} using '{client_type}' client with {max_workers} workers.")
    
    start_time = time.monotonic()
    tasks_processed = 0
    
    # --- Logic to choose client ---
    if client_type == 'async':
        async def run_logic():
            nonlocal tasks_processed
            if run_until:
                while tasks_processed < run_until:
                    tasks_processed += await run_concurrent_async(test_url, timeout, max_workers)
                    logging.info(f"Total tasks processed so far: {tasks_processed}")
            else:
                tasks_processed = await run_concurrent_async(test_url, timeout, max_workers)
        asyncio.run(run_logic())
    else: # 'sync' client
        if run_until:
            while tasks_processed < run_until:
                tasks_processed += run_concurrent_requests_sync(test_url, timeout, max_workers)
                logging.info(f"Total tasks processed so far: {tasks_processed}")
        else:
            tasks_processed = run_concurrent_requests_sync(test_url, timeout, max_workers)

    elapsed_time = time.monotonic() - start_time
    
    if tasks_processed > 0 and elapsed_time > 0:
        rate = tasks_processed / elapsed_time
        logging.info(f"Finished insertion test in {elapsed_time:.2f} seconds.")
        logging.info(f"Total tasks processed: {tasks_processed}. Average rate: {rate:.2f} tasks/s.")
    else:
        logging.warning("No tasks were processed during the test.")
