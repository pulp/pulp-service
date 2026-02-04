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


def write_parquet(table: pa.Table, output_path: str):
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
    """
    # TODO: Implement Parquet writing
    # - Detect s3:// prefix for S3 upload
    # - Use pyarrow.parquet.write_table for local files
    # - Use pyarrow.fs.S3FileSystem for S3 uploads
    # - Use snappy compression (default)
    pass
