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

@click.command()
@click.option('--since', type=click.DateTime(), default=datetime.now(timezone.utc).isoformat(), help='Analyze tasks created since this ISO 8601 timestamp.')
@click.option('--until', type=click.DateTime(), default=None, help='Analyze tasks created until this ISO 8601 timestamp.')
@click.pass_context
def task_analysis(ctx, since: datetime, until: Optional[datetime]):
    """Perform an async wait-time analysis of completed tasks."""
    api_root = ctx.obj['api_root']
    user = ctx.obj['user']
    password = ctx.obj['password']

    async def run_logic():
        logging.info("\nStarting task analysis...")
        auth = aiohttp.BasicAuth(user, password)
        
        base_url = f"{api_root}/pulp/default/api/v3/tasks/"
        params = {
            "pulp_created__gte": since.isoformat()
        }
        if until:
            params["pulp_created__lte"] = until.isoformat()
            logging.info(f"Filtering tasks created between {since.date()} and {until.date()}")
        else:
            logging.info(f"Filtering tasks created since {since.date()}")

        all_tasks: List[Dict[str, Any]] = []
        
        # FIX: Create the session outside the loop for efficiency
        async with aiohttp.ClientSession(auth=auth) as session:
            tasks_url: Optional[str] = base_url
            first_request = True
            while tasks_url:
                try:
                    # Pass params only on the first request, then use the 'next' URL
                    request_params = params if first_request else None
                    async with session.get(tasks_url, params=request_params) as response:
                        response.raise_for_status()
                        data = await response.json()
                        all_tasks.extend(data.get("results", []))
                        tasks_url = data.get("next")
                        first_request = False
                        if tasks_url:
                            logging.info(f"Fetching next page of results...")
                except aiohttp.ClientError as e:
                    logging.error(f"Failed to fetch tasks: {e}")
                    return
        
        if not all_tasks:
            logging.warning("No tasks found for the specified date range.")
            return

        click.echo("\n--- Task Summary ---")
        click.echo(f"Total tasks fetched: {len(all_tasks)}")
        state_counts = Counter(t.get("state") for t in all_tasks)
        for state, count in state_counts.most_common():
            click.echo(f"- {state.capitalize()}: {count}")

        completed_tasks = [
            t for t in all_tasks 
            if t.get("state") == "completed" and t.get("started_at") and t.get("finished_at")
        ]

        if not completed_tasks:
            logging.warning("No completed tasks with start/finish times found for further analysis.")
            return

        logging.info(f"Analyzing {len(completed_tasks)} completed tasks for performance metrics...")
        
        # --- FIX: All analysis and print logic is now present ---
        start_times = [datetime.fromisoformat(t["started_at"]) for t in completed_tasks]
        finish_times = [datetime.fromisoformat(t["finished_at"]) for t in completed_tasks]

        if start_times and finish_times:
            first_task_started = min(start_times)
            last_task_finished = max(finish_times)
            total_duration = last_task_finished - first_task_started
            total_seconds = total_duration.total_seconds()

            click.echo("\n--- Throughput Analysis ---")
            if total_seconds > 0:
                throughput = len(completed_tasks) / total_seconds
                click.echo(f"Processed {len(completed_tasks)} completed tasks over a {total_seconds:.2f} second window.")
                click.echo(f"Average throughput: {throughput:.2f} tasks/sec.")
            else:
                click.echo("Not enough task duration to calculate throughput.")

        wait_times = []
        for task in completed_tasks:
            try:
                created = datetime.fromisoformat(task["pulp_created"])
                started = datetime.fromisoformat(task["started_at"])
                wait_times.append((started - created).total_seconds())
            except (KeyError, TypeError, ValueError) as e:
                logging.warning(f"Could not process task {task.get('pulp_href')}: {e}")

        if not wait_times:
            logging.warning("No valid task timings found for wait-time analysis.")
            return

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
        category_counts = wait_categories.value_counts().sort_index()

        click.echo("\n--- Wait Time Distribution ---")
        click.echo(category_counts.to_string())
        click.echo("---------------------------------")
    
    asyncio.run(run_logic())
