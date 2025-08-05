# pulp_benchmark/client.py
import asyncio
import logging
import ssl
from typing import Any, Dict, List, Optional

import aiohttp

async def send_request(session: aiohttp.ClientSession, url: str, timeout: int) -> int:
    """Sends a single async request and returns the number of tasks processed."""
    try:
        timeout_config = aiohttp.ClientTimeout(total=timeout + 5)
        async with session.get(url, params={"timeout": timeout}, timeout=timeout_config) as response:
            response.raise_for_status()
            data = await response.json()
            tasks = data.get('tasks_executed', 0)
            logging.info(f"Successfully processed {tasks} tasks.")
            return tasks
    except asyncio.TimeoutError:
        logging.error(f"Request timed out after {timeout + 5} seconds.")
    except aiohttp.ClientError as e:
        logging.error(f"Request failed: {e}")
    return 0

async def run_concurrent_requests(
    url: str,
    timeout: int,
    max_workers: int,
    user: Optional[str] = None,
    password: Optional[str] = None,
    cert: Optional[str] = None,
    key: Optional[str] = None,
) -> int:
    """Runs concurrent requests using asyncio.gather."""
    auth = aiohttp.BasicAuth(user, password) if user and password else None
    ssl_context = None
    if cert:
        ssl_context = ssl.create_default_context(cafile=cert)
        if key:
            ssl_context.load_cert_chain(cert, key)
    
    async with aiohttp.ClientSession(auth=auth, connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
        tasks = [send_request(session, url, timeout) for _ in range(max_workers)]
        results = await asyncio.gather(*tasks)
        return sum(results)

async def get_system_status(
    api_root: str,
    user: Optional[str] = None,
    password: Optional[str] = None,
    cert: Optional[str] = None,
    key: Optional[str] = None,
):
    """Fetches and prints the system's worker status asynchronously."""
    logging.info("Fetching system status...")
    status_endpoint = f"{api_root}/pulp/api/v3/status/"
    auth = aiohttp.BasicAuth(user, password) if user and password else None
    ssl_context = None
    if cert:
        ssl_context = ssl.create_default_context(cafile=cert)
        if key:
            ssl_context.load_cert_chain(cert, key)

    try:
        async with aiohttp.ClientSession(auth=auth, connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
            async with session.get(status_endpoint) as response:
                response.raise_for_status()
                status = await response.json()
                logging.info(f"Online API workers: {len(status.get('online_api_apps', []))}")
                logging.info(f"Online Content workers: {len(status.get('online_content_apps', []))}")
                logging.info(f"Online Task workers: {len(status.get('online_workers', []))}")
    except aiohttp.ClientError as e:
        logging.error(f"Could not fetch system status: {e}")
