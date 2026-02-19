"""Parquet file writing to local filesystem or S3."""

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


def write_parquet(table: pa.Table, output_path: str, s3_credentials: dict = None, region: str = None):
    """
    Write PyArrow Table to Parquet file.

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
        output_path: Local file path or S3 URI (s3://bucket/key)
        s3_credentials: Optional dict with S3 credentials:
            - access_key: AWS access key ID
            - secret_key: AWS secret access key
            - session_token: AWS session token (optional)
        region: AWS region for S3 bucket (optional)
    """
    import pyarrow.parquet as pq
    import os
    import tempfile

    if output_path.startswith('s3://'):
        # Write to temporary file, then upload to S3
        log.info("writing to s3", output=output_path)

        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.parquet', delete=False) as tmp_file:
            tmp_path = tmp_file.name

        try:
            # Write Parquet to temporary file
            pq.write_table(table, tmp_path, compression='snappy')

            file_size_kb = os.path.getsize(tmp_path) / 1024
            log.info("parquet file generated", size_kb=round(file_size_kb, 2))

            # Upload to S3 using boto3
            import boto3

            # Parse S3 URI
            s3_path = output_path[5:]  # Remove 's3://'
            bucket, key = s3_path.split('/', 1)

            # Create S3 client with explicit credentials if provided
            if s3_credentials:
                s3_client = boto3.client(
                    's3',
                    aws_access_key_id=s3_credentials['access_key'],
                    aws_secret_access_key=s3_credentials['secret_key'],
                    aws_session_token=s3_credentials.get('session_token'),
                    region_name=region,
                )
            else:
                # Use default credentials
                s3_client = boto3.client('s3', region_name=region)

            log.info("uploading to s3", bucket=bucket, key=key)
            s3_client.upload_file(tmp_path, bucket, key)

            log.info("upload complete", records=len(table))
        finally:
            # Clean up temporary file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    else:
        # Local file
        log.info("writing to local file", output=output_path)

        # Ensure parent directory exists for local file paths
        parent_dir = os.path.dirname(output_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        pq.write_table(table, output_path, compression='snappy')

        file_size_kb = os.path.getsize(output_path) / 1024
        log.info("write complete", records=len(table), size_kb=round(file_size_kb, 2))

