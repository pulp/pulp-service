# pulp_benchmark/client_sync.py
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import requests


def log_worker_status(status: dict) -> None:
    """Log worker status information from Pulp API response."""
    logging.info(f"Online API workers: {len(status.get('online_api_apps', []))}")
    logging.info(f"Online Content workers: {len(status.get('online_content_apps', []))}")
    logging.info(f"Online Task workers: {len(status.get('online_workers', []))}")

def send_request_sync(session: requests.Session, url: str, timeout: int, worker_id: int, debug_requests: bool = False) -> int:
    """Sends a single sync request and returns the number of tasks processed."""
    logging.info(f"[Sync Worker {worker_id}] Attempting to send request...")
    params = {"timeout": timeout}

    if debug_requests:
        logging.info(f"[DEBUG REQUEST] Worker {worker_id}: GET {url}")
        logging.info(f"[DEBUG REQUEST] Worker {worker_id}: Params: {params}")
        logging.info(f"[DEBUG REQUEST] Worker {worker_id}: Headers: {dict(session.headers)}")

    try:
        response = session.get(url, params=params, timeout=timeout + 10)

        if debug_requests:
            logging.info(f"[DEBUG RESPONSE] Worker {worker_id}: Status: {response.status_code}")
            logging.info(f"[DEBUG RESPONSE] Worker {worker_id}: Headers: {dict(response.headers)}")

        response.raise_for_status()
        data = response.json()

        if debug_requests:
            logging.info(f"[DEBUG RESPONSE] Worker {worker_id}: Body: {data}")

        tasks = data.get('tasks_executed', 0)
        logging.info(f"[Sync Worker {worker_id}] Successfully processed {tasks} tasks.")
        return tasks
    except requests.exceptions.Timeout:
        logging.error(f"[Sync Worker {worker_id}] Request timed out after {timeout + 10} seconds.")
    except requests.exceptions.RequestException as e:
        logging.error(f"[Sync Worker {worker_id}] Request failed: {e}")
    return 0

def run_concurrent_requests_sync(
    url: str,
    timeout: int,
    max_workers: int,
    user: Optional[str] = None,
    password: Optional[str] = None,
    cert: Optional[str] = None,
    key: Optional[str] = None,
    verify_ssl: bool = True,
    debug_requests: bool = False,
) -> int:
    """Runs concurrent requests using a ThreadPoolExecutor."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        with requests.Session() as session:
            if user and password:
                session.auth = (user, password)
            if cert:
                session.cert = (cert, key) if key else cert
            session.verify = verify_ssl
            # Pass a worker_id for better logging
            futures = [executor.submit(send_request_sync, session, url, timeout, i+1, debug_requests) for i in range(max_workers)]
            # Correctly wait for all futures to complete
            results = [f.result() for f in futures]
            return sum(results)

def get_system_status_sync(
    api_root: str,
    user: Optional[str] = None,
    password: Optional[str] = None,
    cert: Optional[str] = None,
    key: Optional[str] = None,
    verify_ssl: bool = True,
    debug_requests: bool = False,
):
    """Fetches and prints the system's worker status synchronously."""
    logging.info("Fetching system status...")
    status_endpoint = f"{api_root}/pulp/api/v3/status/"
    auth = (user, password) if user and password else None
    cert_param = (cert, key) if cert and key else cert

    if debug_requests:
        logging.info(f"[DEBUG REQUEST] GET {status_endpoint}")
        logging.info(f"[DEBUG REQUEST] Auth: {'Basic Auth' if auth else 'Cert Auth' if cert_param else 'None'}")
        logging.info(f"[DEBUG REQUEST] SSL Verify: {verify_ssl}")

    try:
        response = requests.get(status_endpoint, timeout=15, auth=auth, cert=cert_param, verify=verify_ssl)

        if debug_requests:
            logging.info(f"[DEBUG RESPONSE] Status: {response.status_code}")
            logging.info(f"[DEBUG RESPONSE] Headers: {dict(response.headers)}")

        response.raise_for_status()
        status = response.json()

        if debug_requests:
            logging.info(f"[DEBUG RESPONSE] Body: {status}")

        log_worker_status(status)
    except requests.exceptions.RequestException as e:
        logging.error(f"Could not fetch system status: {e}")
