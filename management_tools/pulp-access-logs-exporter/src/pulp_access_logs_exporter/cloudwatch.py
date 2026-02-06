"""CloudWatch Logs Insights query execution."""

import time
from datetime import datetime, timedelta
from typing import List, Dict, Any
import boto3
import pyarrow as pa
from pulp_access_logs_exporter.writer import SCHEMA


def build_query(filter_paths: str = "/api/pypi/", exclude_paths: str = "/livez,/status") -> str:
    """
    Build CloudWatch Logs Insights query with parsing and filtering.

    All parsing and filtering is done server-side in CloudWatch Logs Insights.
    Returns structured results ready for conversion to PyArrow Table.

    Args:
        filter_paths: Include only these path prefixes (default: /api/pypi/)
        exclude_paths: Exclude these paths (default: /livez,/status)

    Returns:
        CloudWatch Logs Insights query string
    """
    # Build filter for include paths (currently only supports single path)
    # Escape forward slashes for CloudWatch Logs Insights regex
    filter_pattern = filter_paths.replace('/', '\\/')

    # Build exclude filters
    exclude_filters = []
    if exclude_paths:
        for path in exclude_paths.split(','):
            path = path.strip()
            if path:
                # Escape forward slashes
                escaped_path = path.replace('/', '\\/')
                exclude_filters.append(f"| filter @message not like /{escaped_path}/")

    # Always exclude django warnings
    exclude_filters.append("| filter @message not like /django.request:WARNING/")

    # Build complete query
    query_parts = [
        "fields @timestamp, @message",
        f"| filter @message like /{filter_pattern}/",
    ]
    query_parts.extend(exclude_filters)
    query_parts.extend([
        "| parse @message \"user:* org_id\" as parsed_user",
        "| parse @message \"org_id:* \" as parsed_org_id",
        "| parse @message '/api/pypi/*/*/simple/' as parsed_domain, parsed_distribution",
        "| parse @message '/simple/*/ ' as parsed_package",
        "| filter ispresent(parsed_package)",
        "| parse message ' HTTP/1.1\" * ' as parsed_status_code",
        "| parse message '\"-\" \"*\" ' as parsed_user_agent",
        "| parse message ' x_forwarded_for:\"*\"' as xff",
        "| parse xff '*,*' as client_ip, xff_rest",
        "| fields @timestamp, @message, parsed_user as user, parsed_org_id as org_id, parsed_domain as domain, parsed_distribution as distribution, parsed_package as package, parsed_status_code as status_code, parsed_user_agent as user_agent, coalesce(client_ip, xff) as x_forwarded_for",
    ])

    return "\n    ".join(query_parts)


def fetch_cloudwatch_logs(
    log_group: str,
    query: str,
    start_time: int,
    end_time: int,
    region: str = "us-east-1",
) -> List[Dict[str, Any]]:
    """
    Execute CloudWatch Logs Insights query and poll for results.

    Uses boto3 logs.start_query() to start async query, then polls with
    logs.get_query_results() until Complete. Handles time-based chunking
    for queries that may return >10K results.

    Args:
        log_group: CloudWatch log group name
        query: CloudWatch Logs Insights query string
        start_time: Start timestamp (Unix epoch seconds)
        end_time: End timestamp (Unix epoch seconds)
        region: AWS region

    Returns:
        List of structured records (already parsed by Logs Insights)
    """
    logs_client = boto3.client('logs', region_name=region)

    # Split time range into 5-minute chunks to handle >10K results
    chunk_duration = timedelta(minutes=5)
    start_dt = datetime.fromtimestamp(start_time)
    end_dt = datetime.fromtimestamp(end_time)

    all_results = []
    current = start_dt

    while current < end_dt:
        chunk_end = min(current + chunk_duration, end_dt)

        print(f"Querying logs from {current.isoformat()} to {chunk_end.isoformat()}...")

        # Start async query
        response = logs_client.start_query(
            logGroupName=log_group,
            startTime=int(current.timestamp()),
            endTime=int(chunk_end.timestamp()),
            queryString=query,
            limit=10000,  # CloudWatch max limit
        )

        query_id = response['queryId']

        # Poll until complete with timeout and exponential backoff
        max_wait_seconds = 300  # Maximum total wait time (5 minutes)
        poll_interval = 2       # Initial poll interval in seconds
        max_poll_interval = 30  # Maximum poll interval in seconds
        start_poll_time = time.time()

        while True:
            if time.time() - start_poll_time > max_wait_seconds:
                raise TimeoutError(
                    f"Timed out after {max_wait_seconds} seconds waiting for query {query_id} to complete"
                )

            result = logs_client.get_query_results(queryId=query_id)
            status = result['status']

            if status == 'Complete':
                break
            elif status in ['Failed', 'Cancelled']:
                raise RuntimeError(f"Query {query_id} failed with status: {status}")

            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 2, max_poll_interval)

        # Process results
        chunk_results = result['results']
        print(f"  Retrieved {len(chunk_results)} records")

        # Check if results were truncated
        stats = result.get('statistics', {})
        records_matched = stats.get('recordsMatched', 0)
        records_scanned = stats.get('recordsScanned', 0)

        if len(chunk_results) >= 10000:
            print(f"  WARNING: Result set may be truncated (matched: {records_matched}, scanned: {records_scanned})")

        # Convert from CloudWatch format to dict
        for record in chunk_results:
            parsed = {item['field']: item['value'] for item in record}
            all_results.append(parsed)

        current = chunk_end

    print(f"Total records retrieved: {len(all_results)}")
    return all_results


def convert_to_arrow_table(results: List[Dict[str, Any]]) -> pa.Table:
    """
    Convert CloudWatch Logs Insights results to PyArrow Table.

    Results are already parsed by CloudWatch query. Convert to PyArrow Table
    with proper schema. No pandas needed - pyarrow handles everything.

    Args:
        results: List of records from CloudWatch Logs Insights

    Returns:
        pyarrow.Table ready for Parquet export
    """
    if not results:
        # Return empty table with schema
        return pa.table({field.name: [] for field in SCHEMA}, schema=SCHEMA)

    # Convert CloudWatch results to records matching our schema
    records = []
    for result in results:
        # Convert "-" to None for proper null handling
        user = result.get('user')
        if user == '-':
            user = None

        org_id = result.get('org_id')
        if org_id == '-':
            org_id = None

        # Parse timestamp from ISO format as timezone-naive UTC
        # PyArrow schema uses pa.timestamp('ns') which is timezone-naive
        timestamp_str = result.get('@timestamp', '')
        timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).replace(tzinfo=None)

        # Convert status_code to int
        status_code = int(result.get('status_code', 0))

        record = {
            'timestamp': timestamp,
            'message': result.get('@message', ''),
            'user': user,
            'org_id': org_id,
            'domain': result.get('domain', ''),
            'distribution': result.get('distribution', ''),
            'package': result.get('package', ''),
            'status_code': status_code,
            'user_agent': result.get('user_agent', ''),
            'x_forwarded_for': result.get('x_forwarded_for', ''),
        }
        records.append(record)

    # Create PyArrow Table with schema
    table = pa.Table.from_pylist(records, schema=SCHEMA)
    return table
