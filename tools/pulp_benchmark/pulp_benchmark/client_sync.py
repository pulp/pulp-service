# pulp_benchmark/client_sync.py
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests

def send_request_sync(session: requests.Session, url: str, timeout: int) -> int:
    """Sends a single sync request and returns the number of tasks processed."""
    try:
        response = session.get(url, params={"timeout": timeout}, timeout=timeout + 10)
        response.raise_for_status()
        tasks = response.json().get('tasks_executed', 0)
        logging.info(f"Successfully processed {tasks} tasks.")
        return tasks
    except requests.exceptions.Timeout:
        logging.error(f"Request timed out after {timeout + 10} seconds.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed: {e}")
    return 0

def run_concurrent_requests_sync(url: str, timeout: int, max_workers: int) -> int:
    """Runs concurrent requests using a ThreadPoolExecutor."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        with requests.Session() as session:
            futures = [executor.submit(send_request_sync, session, url, timeout) for _ in range(max_workers)]
            total_tasks = sum(f.result() for f in as_completed(futures))
            return total_tasks

def get_system_status_sync(api_root: str):
    """Fetches and prints the system's worker status synchronously."""
    logging.info("Fetching system status...")
    status_endpoint = f"{api_root}/pulp/api/v3/status/"
    try:
        response = requests.get(status_endpoint, timeout=15)
        response.raise_for_status()
        status = response.json()
        logging.info(f"Online API workers: {len(status.get('online_api_apps', []))}")
        logging.info(f"Online Content workers: {len(status.get('online_content_apps', []))}")
        logging.info(f"Online Task workers: {len(status.get('online_workers', []))}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Could not fetch system status: {e}")
