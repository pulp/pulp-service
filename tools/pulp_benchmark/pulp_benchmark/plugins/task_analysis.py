# pulp_benchmark/plugins/task_analysis.py
import asyncio
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp
import click
import numpy as np
import pandas as pd
import requests

@click.command()
@click.option('--since', type=click.DateTime(), default=datetime.now(timezone.utc).isoformat(), help='Analyze tasks created since this ISO 8601 timestamp.')
@click.option('--until', type=click.DateTime(), default=None, help='Analyze tasks created until this ISO 8601 timestamp.')
@click.pass_context
def task_analysis(ctx, since: datetime, until: Optional[datetime]):
    """Perform a wait-time analysis of completed tasks."""
    client_type = ctx.obj['client_type']
    api_root = ctx.obj['api_root']
    user = ctx.obj['user']
    password = ctx.obj['password']

    # --- Client-specific logic ---
    if client_type == 'async':
        asyncio.run(run_analysis_async(api_root, user, password, since, until))
    else:
        run_analysis_sync(api_root, user, password, since, until)

def process_and_display_results(all_tasks: List[Dict[str, Any]]):
    """Shared function to process and print analysis results."""
    # (This function contains the numpy/pandas logic, unchanged from before)
    if not all_tasks:
        logging.warning("No tasks found for the specified date range.")
        return

    click.echo("\n--- Task Summary ---")
    click.echo(f"Total tasks fetched: {len(all_tasks)}")
    state_counts = Counter(t.get("state") for t in all_tasks)
    for state, count in state_counts.most_common():
        click.echo(f"- {state.capitalize()}: {count}")

    completed_tasks = [t for t in all_tasks if t.get("state") == "completed" and t.get("started_at") and t.get("finished_at")]

    if not completed_tasks:
        logging.warning("No completed tasks with start/finish times found for further analysis.")
        return

    logging.info(f"Analyzing {len(completed_tasks)} completed tasks for performance metrics...")
    
    # Throughput Analysis
    start_times = [datetime.fromisoformat(t["started_at"]) for t in completed_tasks]
    finish_times = [datetime.fromisoformat(t["finished_at"]) for t in completed_tasks]

    if start_times and finish_times:
        first_task_started, last_task_finished = min(start_times), max(finish_times)
        total_seconds = (last_task_finished - first_task_started).total_seconds()

        click.echo("\n--- Throughput Analysis ---")
        if total_seconds > 0:
            throughput = len(completed_tasks) / total_seconds
            click.echo(f"Processed {len(completed_tasks)} completed tasks over a {total_seconds:.2f} second window.")
            click.echo(f"Average throughput: {throughput:.2f} tasks/sec.")
        else:
            click.echo("Not enough task duration to calculate throughput.")

    # Queue Wait Time Analysis
    wait_times = [(datetime.fromisoformat(t["started_at"]) - datetime.fromisoformat(t["pulp_created"])).total_seconds() for t in completed_tasks]

    click.echo("\n--- Queue Wait Time Analysis ---")
    click.echo(f"Total tasks analyzed: {len(wait_times)}")
    click.echo(f"Tasks that waited (>0s): {sum(t > 0 for t in wait_times)}")
    click.echo(f"Average wait time: {np.mean(wait_times):.2f}s")
    click.echo(f"Median wait time: {np.median(wait_times):.2f}s")
    click.echo(f"Min | Max wait time: {min(wait_times):.2f}s | {max(wait_times):.2f}s")
    click.echo(f"95th Percentile: {np.percentile(wait_times, 95):.2f}s")

    bins = [0, 3600, 7200, 10800, 14400, 21600, 28800, float('inf')]
    labels = ["<1h", "1-2h", "2-3h", "3-4h", "4-6h", "6-8h", ">8h"]
    wait_categories = pd.cut(wait_times, bins=bins, labels=labels, right=False)
    
    click.echo("\n--- Wait Time Distribution ---")
    click.echo(wait_categories.value_counts().sort_index().to_string())
    click.echo("---------------------------------")


async def run_analysis_async(api_root, user, password, since, until):
    """Fetches all tasks using aiohttp."""
    logging.info("\nStarting task analysis using 'async' client...")
    auth = aiohttp.BasicAuth(user, password)
    base_url = f"{api_root}/pulp/default/api/v3/tasks/"
    params = {"pulp_created__gte": since.isoformat()}
    if until:
        params["pulp_created__lte"] = until.isoformat()

    all_tasks = []
    async with aiohttp.ClientSession(auth=auth) as session:
        tasks_url: Optional[str] = base_url
        first_request = True
        while tasks_url:
            request_params = params if first_request else None
            async with session.get(tasks_url, params=request_params) as response:
                response.raise_for_status()
                data = await response.json()
                all_tasks.extend(data.get("results", []))
                tasks_url = data.get("next")
                first_request = False
    process_and_display_results(all_tasks)


def run_analysis_sync(api_root, user, password, since, until):
    """Fetches all tasks using requests."""
    logging.info("\nStarting task analysis using 'sync' client...")
    auth = (user, password)
    tasks_url: Optional[str] = f"{api_root}/pulp/default/api/v3/tasks/"
    params = {"pulp_created__gte": since.isoformat()}
    if until:
        params["pulp_created__lte"] = until.isoformat()

    all_tasks = []
    with requests.Session() as session:
        session.auth = auth
        while tasks_url:
            response = session.get(tasks_durl, params=params)
            response.raise_for_status()
            data = response.json()
            all_tasks.extend(data.get("results", []))
            tasks_url = data.get("next")
            params = None # Subsequent requests use the full URL from 'next'
    process_and_display_results(all_tasks)
