# pulp_benchmark/plugins/test_task_insertion.py
import asyncio
import logging
import time
from typing import Optional

import click

# Note the relative import '..' to access the client module from the parent package
from ..client import run_concurrent_requests

@click.command()
@click.option("--timeout", type=int, default=30, show_default=True, help="Timeout for each task-creation request.")
@click.option("--max-workers", type=int, default=50, show_default=True, help="Number of concurrent async requests.")
@click.option("--run-until", type=int, help="Keep creating tasks until this total is reached.")
@click.pass_context
def test_task_insertion(ctx, timeout: int, max_workers: int, run_until: Optional[int]):
    """Run the async task insertion load test."""
    api_root = ctx.obj['api_root']
    test_url = f"{api_root}/pulp/test/tasks/"
    
    logging.info(f"Starting async task insertion test on {test_url} with {max_workers} concurrent requests.")
    
    start_time = time.monotonic()
    tasks_processed = 0
    
    async def run_logic():
        nonlocal tasks_processed
        if run_until:
            while tasks_processed < run_until:
                tasks_processed += await run_concurrent_requests(test_url, timeout, max_workers)
                logging.info(f"Total tasks processed so far: {tasks_processed}")
        else:
            tasks_processed = await run_concurrent_requests(test_url, timeout, max_workers)

    asyncio.run(run_logic())

    elapsed_time = time.monotonic() - start_time
    
    if tasks_processed > 0 and elapsed_time > 0:
        rate = tasks_processed / elapsed_time
        logging.info(f"Finished insertion test in {elapsed_time:.2f} seconds.")
        logging.info(f"Total tasks processed: {tasks_processed}. Average rate: {rate:.2f} tasks/s.")
    else:
        logging.warning("No tasks were processed during the test.")
