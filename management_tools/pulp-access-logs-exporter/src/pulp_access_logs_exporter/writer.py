"""Parquet file writing to local filesystem or S3."""

import os
import tempfile
from typing import Optional

import pyarrow as pa
import structlog

log = structlog.get_logger(__name__)


# Parquet schema for access logs
SCHEMA = pa.schema([
    ('timestamp', pa.timestamp('ns')),
    ('message', pa.string()),
    ('user', pa.string()),  # Nullable for unauthenticated users
    ('org_id', pa.string()),  # Nullable for unauthenticated users
    ('domain', pa.string()),
    ('distribution', pa.string()),
    ('package', pa.string()),
    ('status_code', pa.int16()),
    ('user_agent', pa.string()),
    ('x_forwarded_for', pa.string()),
])


def _make_s3_client(credentials: Optional[dict], region: Optional[str]):
    """Create a boto3 S3 client with explicit or ambient credentials."""
    import boto3
    if credentials:
        return boto3.client(
            's3',
            aws_access_key_id=credentials['access_key'],
            aws_secret_access_key=credentials['secret_key'],
            aws_session_token=credentials.get('session_token'),
            region_name=region,
        )
    return boto3.client('s3', region_name=region)


def _upload_to_s3(tmp_path: str, s3_uri: str, credentials: Optional[dict], region: Optional[str]) -> None:
    """Upload a local file to an S3 URI."""
    bucket, key = s3_uri[5:].split('/', 1)
    s3_client = _make_s3_client(credentials, region)
    log.info("uploading to s3", bucket=bucket, key=key)
    s3_client.upload_file(tmp_path, bucket, key)
    log.info("upload complete", bucket=bucket, key=key)


def write_parquet(
    table: pa.Table,
    destinations: list,
) -> None:
    """
    Write PyArrow Table to Parquet and upload to one or more destinations.

    The Parquet file is generated once to a temporary file, then uploaded
    to each destination. Temporary file is always cleaned up on exit.

    Schema includes:
      - timestamp: timestamp[ns]
      - message: string (full raw log entry)
      - user: string (nullable)
      - org_id: string (nullable)
      - domain: string
      - distribution: string
      - package: string
      - status_code: int16
      - user_agent: string
      - x_forwarded_for: string

    Args:
        table: PyArrow Table with log data
        destinations: List of dicts, each with:
            - path: Local file path or S3 URI (s3://bucket/key)
            - credentials: Optional dict with access_key, secret_key, session_token
            - region: Optional AWS region string
    """
    import pyarrow.parquet as pq

    s3_destinations = [d for d in destinations if d['path'].startswith('s3://')]
    local_destinations = [d for d in destinations if not d['path'].startswith('s3://')]

    # Handle S3 destinations: generate temp file once, upload to each
    if s3_destinations:
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.parquet', delete=False) as tmp_file:
            tmp_path = tmp_file.name

        try:
            pq.write_table(table, tmp_path, compression='snappy')
            file_size_kb = os.path.getsize(tmp_path) / 1024
            log.info("parquet file generated", size_kb=round(file_size_kb, 2), records=len(table))

            for dest in s3_destinations:
                _upload_to_s3(tmp_path, dest['path'], dest.get('credentials'), dest.get('region'))
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # Handle local destinations
    for dest in local_destinations:
        path = dest['path']
        log.info("writing to local file", output=path)
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        pq.write_table(table, path, compression='snappy')
        file_size_kb = os.path.getsize(path) / 1024
        log.info("write complete", output=path, records=len(table), size_kb=round(file_size_kb, 2))
