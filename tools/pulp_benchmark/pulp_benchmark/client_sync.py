# pulp_benchmark/client_sync.py
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import requests

def send_request_sync(session: requests.Session, url: str, timeout: int, worker_id: int) -> int:
    """Sends a single sync request and returns the number of tasks processed."""
    logging.info(f"[Sync Worker {worker_id}] Attempting to send request...") # <-- ADDED LOGGING
    try:
        response = session.get(url, params={"timeout": timeout}, timeout=timeout + 10)
        response.raise_for_status()
        tasks = response.json().get('tasks_executed', 0)
        logging.info(f"[Sync Worker {worker_id}] Successfully processed {tasks} tasks.")
        return tasks
    except requests.exceptions.Timeout:
        logging.error(f"[Sync Worker {worker_id}] Request timed out after {timeout + 10} seconds.")
    except requests.exceptions.RequestException as e:
        logging.error(f"[Sync Worker {worker_id}] Request failed: {e}")
    return 0

def run_concurrent_requests_sync(url: str, timeout: int, max_workers: int) -> int:
    """Runs concurrent requests using a ThreadPoolExecutor."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        with requests.Session() as session:
            # Pass a worker_id for better logging
            futures = [executor.submit(send_request_sync, session, url, timeout, i+1) for i in range(max_workers)]
            # Correctly wait for all futures to complete
            results = [f.result() for f in futures]
            return sum(results)

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
