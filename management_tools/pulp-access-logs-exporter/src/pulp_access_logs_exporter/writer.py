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
    import pyarrow.fs as fs

    if output_path.startswith('s3://'):
        # S3 upload using PyArrow filesystem
        print(f"Writing to S3: {output_path}")

        # Create S3FileSystem with explicit credentials if provided
        if s3_credentials:
            s3_fs = fs.S3FileSystem(
                access_key=s3_credentials['access_key'],
                secret_key=s3_credentials['secret_key'],
                session_token=s3_credentials.get('session_token'),
                region=region,
            )
        else:
            # Use default credentials (from env vars or IAM role)
            s3_fs = fs.S3FileSystem(region=region)

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

