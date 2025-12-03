# pulp_benchmark/plugins/task_analysis.py
import asyncio
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import click
import numpy as np
import pandas as pd
import requests

from pulp_benchmark.client_async import create_session as create_async_session

@click.command()
@click.option('--since', type=click.DateTime(), default=datetime.now(timezone.utc).isoformat(), help='Analyze tasks created since this ISO 8601 timestamp.')
@click.option('--until', type=click.DateTime(), default=None, help='Analyze tasks created until this ISO 8601 timestamp.')
@click.option('--task-name', type=str, default=None, help='Filter tasks by name (e.g., "pulp_ansible.app.tasks.collections.sync").')
@click.pass_context
def task_analysis(ctx, since: datetime, until: Optional[datetime], task_name: Optional[str]):
    """Perform a wait-time analysis of completed tasks."""
    client_type = ctx.obj['client_type']
    api_root = ctx.obj['api_root']
    user = ctx.obj['user']
    password = ctx.obj['password']
    cert = ctx.obj['cert']
    key = ctx.obj['key']
    verify_ssl = ctx.obj['verify_ssl']

    # --- Client-specific logic ---
    if client_type == 'async':
        asyncio.run(run_analysis_async(api_root, user, password, cert, key, verify_ssl, since, until, task_name))
    else:
        run_analysis_sync(api_root, user, password, cert, key, verify_ssl, since, until, task_name)

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


async def run_analysis_async(api_root, user, password, cert, key, verify_ssl, since, until, task_name):
    """Fetches all tasks using aiohttp."""
    logging.info("\nStarting task analysis using 'async' client...")
    logging.info(f"Authentication: {'Basic Auth' if user and password else 'Cert Auth' if cert else 'None'}")
    logging.info(f"SSL Verification: {verify_ssl}")
    if cert:
        logging.info(f"Client cert: {cert}")
        logging.info(f"Client key: {key or 'None (using cert file)'}")

    # Extract base URL (scheme + netloc) to build absolute path
    parsed = urlparse(api_root)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"

    base_url = f"{base_domain}/api/pulp/admin/tasks/"
    logging.info(f"Fetching tasks from: {base_url}")
    params = {"pulp_created__gte": since.isoformat()}
    if until:
        params["pulp_created__lte"] = until.isoformat()
    if task_name:
        params["name"] = task_name
        logging.info(f"Filtering by task name: {task_name}")

    all_tasks = []
    async with create_async_session(user, password, cert, key, verify_ssl) as session:
        tasks_url: Optional[str] = base_url
        first_request = True
        while tasks_url:
            request_params = params if first_request else None
            logging.info(f"GET {tasks_url}")
            if request_params:
                logging.info(f"Query params: {request_params}")

            async with session.get(tasks_url, params=request_params) as response:
                logging.info(f"Response status: {response.status}")
                if response.status >= 400:
                    error_text = await response.text()
                    logging.error(f"Error response body: {error_text}")
                response.raise_for_status()
                data = await response.json()
                all_tasks.extend(data.get("results", []))

                # Get next URL and check if it's on the same domain
                next_url = data.get("next")
                if next_url:
                    next_parsed = urlparse(next_url)
                    # If pagination URL is on a different domain, reconstruct it on our domain
                    if next_parsed.netloc != parsed.netloc:
                        logging.info(f"Pagination URL points to different domain ({next_parsed.netloc}), reconstructing on correct domain")
                        # Reconstruct the URL with our base domain
                        tasks_url = f"{base_domain}{next_parsed.path}"
                        if next_parsed.query:
                            tasks_url += f"?{next_parsed.query}"
                    else:
                        tasks_url = next_url
                else:
                    tasks_url = None

                first_request = False
    process_and_display_results(all_tasks)



def run_analysis_sync(api_root, user, password, cert, key, verify_ssl, since, until, task_name):
    """Fetches all tasks using requests."""
    logging.info("\nStarting task analysis using 'sync' client...")
    logging.info(f"Authentication: {'Basic Auth' if user and password else 'Cert Auth' if cert else 'None'}")
    logging.info(f"SSL Verification: {verify_ssl}")
    if cert:
        logging.info(f"Client cert: {cert}")
        logging.info(f"Client key: {key or 'None (using cert file)'}")

    # Extract base URL (scheme + netloc) to build absolute path
    parsed = urlparse(api_root)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"

    tasks_url: Optional[str] = f"{base_domain}/api/pulp/admin/tasks/"
    logging.info(f"Fetching tasks from: {tasks_url}")
    params = {"pulp_created__gte": since.isoformat()}
    if until:
        params["pulp_created__lte"] = until.isoformat()
    if task_name:
        params["name"] = task_name
        logging.info(f"Filtering by task name: {task_name}")

    all_tasks = []
    with requests.Session() as session:
        # Configure authentication
        if user and password:
            session.auth = (user, password)

        # Configure client certificate for mTLS
        if cert:
            session.cert = (cert, key) if key else cert

        # Configure SSL verification
        session.verify = verify_ssl

        while tasks_url:
            logging.info(f"GET {tasks_url}")
            if params:
                logging.info(f"Query params: {params}")

            response = session.get(tasks_url, params=params)
            logging.info(f"Response status: {response.status_code}")
            if response.status_code >= 400:
                logging.error(f"Error response body: {response.text}")
            response.raise_for_status()
            data = response.json()
            all_tasks.extend(data.get("results", []))

            # Get next URL and check if it's on the same domain
            next_url = data.get("next")
            if next_url:
                next_parsed = urlparse(next_url)
                # If pagination URL is on a different domain, reconstruct it on our domain
                if next_parsed.netloc != parsed.netloc:
                    logging.info(f"Pagination URL points to different domain ({next_parsed.netloc}), reconstructing on correct domain")
                    # Reconstruct the URL with our base domain
                    tasks_url = f"{base_domain}{next_parsed.path}"
                    if next_parsed.query:
                        tasks_url += f"?{next_parsed.query}"
                else:
                    tasks_url = next_url
                params = None  # Subsequent requests use the full URL from 'next'
            else:
                tasks_url = None

    process_and_display_results(all_tasks)

