# pulp_benchmark/client.py
import asyncio
import logging
import ssl
from typing import Any, Dict, List, Optional

import aiohttp

# User-Agent version constants
PULP_BENCHMARK_VERSION = "1.0"
CURL_COMPATIBLE_VERSION = "8.0.0"


def log_worker_status(status: dict) -> None:
    """Log worker status information from Pulp API response."""
    logging.info(f"Online API workers: {len(status.get('online_api_apps', []))}")
    logging.info(f"Online Content workers: {len(status.get('online_content_apps', []))}")
    logging.info(f"Online Task workers: {len(status.get('online_workers', []))}")


def create_ssl_context(
    cert: Optional[str] = None,
    key: Optional[str] = None,
    verify_ssl: bool = True
) -> Optional[ssl.SSLContext]:
    """
    Create an SSL context for aiohttp connections.

    Args:
        cert: Path to client certificate
        key: Path to client certificate key
        verify_ssl: Whether to verify SSL certificates

    Returns:
        SSL context or None (for default behavior)
    """
    ssl_context = None

    if not verify_ssl:
        # Disable SSL verification
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        # Load client certificate if provided
        if cert:
            ssl_context.load_cert_chain(cert, key) if key else ssl_context.load_cert_chain(cert)
    elif cert:
        # Use client certificate with normal SSL verification
        ssl_context = ssl.create_default_context()
        ssl_context.load_cert_chain(cert, key) if key else ssl_context.load_cert_chain(cert)

    return ssl_context


def create_session(
    user: Optional[str] = None,
    password: Optional[str] = None,
    cert: Optional[str] = None,
    key: Optional[str] = None,
    verify_ssl: bool = True
) -> aiohttp.ClientSession:
    """
    Create an aiohttp ClientSession with authentication and SSL configuration.

    Args:
        user: Username for basic auth
        password: Password for basic auth
        cert: Path to client certificate
        key: Path to client certificate key
        verify_ssl: Whether to verify SSL certificates

    Returns:
        Configured aiohttp.ClientSession

    Note:
        Automatically uses HTTP_PROXY, HTTPS_PROXY, and NO_PROXY environment variables
    """
    auth = aiohttp.BasicAuth(user, password) if user and password else None
    ssl_context = create_ssl_context(cert, key, verify_ssl)

    # Set headers to avoid CDN/WAF blocks (e.g., Akamai)
    headers = {
        'User-Agent': f'pulp-benchmark/{PULP_BENCHMARK_VERSION} (compatible; curl/{CURL_COMPATIBLE_VERSION})',
    }

    return aiohttp.ClientSession(
        auth=auth,
        connector=aiohttp.TCPConnector(ssl=ssl_context),
        headers=headers,
        trust_env=True  # Use HTTP_PROXY, HTTPS_PROXY, NO_PROXY from environment
    )

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
    verify_ssl: bool = True,
) -> int:
    """Runs concurrent requests using asyncio.gather."""
    async with create_session(user, password, cert, key, verify_ssl) as session:
        tasks = [send_request(session, url, timeout) for _ in range(max_workers)]
        results = await asyncio.gather(*tasks)
        return sum(results)

async def get_system_status(
    api_root: str,
    user: Optional[str] = None,
    password: Optional[str] = None,
    cert: Optional[str] = None,
    key: Optional[str] = None,
    verify_ssl: bool = True,
):
    """Fetches and prints the system's worker status asynchronously."""
    logging.info("Fetching system status...")
    status_endpoint = f"{api_root}/pulp/api/v3/status/"

    try:
        async with create_session(user, password, cert, key, verify_ssl) as session:
            async with session.get(status_endpoint) as response:
                response.raise_for_status()
                status = await response.json()
                log_worker_status(status)
    except aiohttp.ClientError as e:
        logging.error(f"Could not fetch system status: {e}")
