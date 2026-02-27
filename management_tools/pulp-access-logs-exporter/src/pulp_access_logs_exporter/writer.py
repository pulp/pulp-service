"""Parquet file writing to local filesystem or S3."""

import pyarrow as pa


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


def write_parquet(table: pa.Table, output_path: str, s3_credentials: dict = None):
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
    """
    import pyarrow.parquet as pq
    import pyarrow.fs as fs

    if output_path.startswith('s3://'):
        # S3 upload using PyArrow filesystem
        print(f"Writing to S3: {output_path}")

        # Create S3FileSystem with explicit credentials if provided
        if s3_credentials:
            kwargs = {
                'access_key': s3_credentials['access_key'],
                'secret_key': s3_credentials['secret_key'],
            }
            if s3_credentials.get('session_token'):
                kwargs['session_token'] = s3_credentials['session_token']
            if s3_credentials.get('endpoint_url'):
                kwargs['endpoint_override'] = s3_credentials['endpoint_url']
            if s3_credentials.get('region'):
                kwargs['region'] = s3_credentials['region']
            s3_fs = fs.S3FileSystem(**kwargs)
        else:
            # Use default credentials (from env vars or IAM role)
            s3_fs = fs.S3FileSystem()

        # Remove s3:// prefix for PyArrow
        s3_path = output_path[5:]

        with s3_fs.open_output_stream(s3_path) as f:
            pq.write_table(table, f, compression='snappy')

        print(f"Successfully wrote {len(table)} records to S3")
    else:
        # Local file
        print(f"Writing to local file: {output_path}")

        import os
        # Ensure parent directory exists for local file paths
        parent_dir = os.path.dirname(output_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        pq.write_table(table, output_path, compression='snappy')

        # Print file size
        file_size = os.path.getsize(output_path)
        file_size_kb = file_size / 1024
        print(f"Successfully wrote {len(table)} records ({file_size_kb:.2f} KB)")


def upload_file(source: str, destination: str, s3_credentials: dict = None):
    """
    Upload a local file to S3 using boto3.

    Args:
        source: Local file path
        destination: S3 URI (s3://bucket/key)
        s3_credentials: Optional dict with S3 credentials:
            - access_key: AWS access key ID
            - secret_key: AWS secret access key
            - session_token: AWS session token (optional)
            - endpoint_url: Custom S3 endpoint (optional, e.g. MinIO)
            - region: AWS region (optional)
    """
    import boto3

    if not destination.startswith('s3://'):
        raise ValueError(f"Destination must be an S3 URI (s3://...): {destination}")

    # Parse s3://bucket/key
    s3_path = destination[5:]
    bucket, _, key = s3_path.partition('/')

    kwargs = {}
    if s3_credentials:
        kwargs['aws_access_key_id'] = s3_credentials['access_key']
        kwargs['aws_secret_access_key'] = s3_credentials['secret_key']
        if s3_credentials.get('session_token'):
            kwargs['aws_session_token'] = s3_credentials['session_token']
        if s3_credentials.get('region'):
            kwargs['region_name'] = s3_credentials['region']
        if s3_credentials.get('endpoint_url'):
            kwargs['endpoint_url'] = s3_credentials['endpoint_url']

    s3_client = boto3.client('s3', **kwargs)

    print(f"Uploading {source} -> {destination}")
    s3_client.upload_file(source, bucket, key)
    print(f"Upload complete")

