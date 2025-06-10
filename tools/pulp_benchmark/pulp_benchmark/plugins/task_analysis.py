# pulp_benchmark/plugins/task_analysis.py
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp
import click
import numpy as np
import pandas as pd

@click.command()
@click.option('--since', type=click.DateTime(), default=datetime.now(timezone.utc).isoformat(), help='Analyze tasks created since this ISO 8601 timestamp.')
@click.pass_context
def task_analysis(ctx, since: datetime):
    """Perform an async wait-time analysis of completed tasks."""
    api_root = ctx.obj['api_root']
    user = ctx.obj['user']
    password = ctx.obj['password']

    async def run_logic():
        logging.info("\nStarting task analysis...")
        auth = aiohttp.BasicAuth(user, password)
        tasks_url: Optional[str] = f"{api_root}/pulp/default/api/v3/tasks/?pulp_created__gte={since.isoformat()}"
        
        all_tasks: List[Dict[str, Any]] = []
        async with aiohttp.ClientSession(auth=auth) as session:
            while tasks_url:
                try:
                    async with session.get(tasks_url) as response:
                        response.raise_for_status()
                        data = await response.json()
                        all_tasks.extend(data.get("results", []))
                        tasks_url = data.get("next")
                        if tasks_url:
                            logging.info("Fetching next page of results...")
                except aiohttp.ClientError as e:
                    logging.error(f"Failed to fetch tasks: {e}")
                    return
        
        if not all_tasks:
            logging.warning("No tasks found for analysis.")
            return

        logging.info(f"Analyzing {len(all_tasks)} tasks...")
        wait_times = []
        for task in all_tasks:
            try:
                created = datetime.fromisoformat(task["pulp_created"])
                started = datetime.fromisoformat(task["started_at"])
                wait_times.append((started - created).total_seconds())
            except (KeyError, TypeError, ValueError) as e:
                logging.warning(f"Could not process task {task.get('pulp_href')}: {e}")

        if not wait_times:
            logging.warning("No valid task timings found to analyze.")
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
