"""CLI interface for access logs exporter."""

import argparse
import sys
import re
from datetime import datetime, timedelta
from pathlib import Path


def parse_args(args=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Export Pulp access logs from CloudWatch to Parquet format"
    )

    parser.add_argument(
        "--cloudwatch-group",
        required=True,
        help="CloudWatch log group name (e.g., /aws/containerinsights/cluster/application)",
    )

    parser.add_argument(
        "--start-time",
        required=True,
        help='Start timestamp - ISO format (2026-02-04T14:00:00Z) or relative ("1 hour ago")',
    )

    parser.add_argument(
        "--end-time",
        required=True,
        help='End timestamp - ISO format or relative ("now", "15 minutes ago")',
    )

    parser.add_argument(
        "--output-path",
        required=True,
        help="Local file path or S3 URI (s3://bucket/key)",
    )

    parser.add_argument(
        "--aws-region",
        default="us-east-1",
        help="AWS region (default: us-east-1)",
    )

    parser.add_argument(
        "--filter-paths",
        default="/api/pypi/",
        help="Include only these path prefixes (default: /api/pypi/)",
    )

    parser.add_argument(
        "--exclude-paths",
        default="/livez,/status",
        help="Exclude these paths (default: /livez,/status)",
    )

    parser.add_argument(
        "--s3-access-key-id",
        help="AWS access key ID for S3 (if different from CloudWatch credentials)",
    )

    parser.add_argument(
        "--s3-secret-access-key",
        help="AWS secret access key for S3 (if different from CloudWatch credentials)",
    )

    parser.add_argument(
        "--s3-session-token",
        help="AWS session token for S3 (if using temporary credentials)",
    )

    parser.add_argument(
        "--s3-region",
        help="AWS region for S3 bucket (defaults to --aws-region if not specified)",
    )

    return parser.parse_args(args)


def parse_time(time_str: str) -> datetime:
    """
    Parse time from ISO format or relative string.

    Args:
        time_str: ISO timestamp or relative time ("1 hour ago", "now", etc.)

    Returns:
        datetime object
    """
    # Handle "now"
    if time_str.lower() == "now":
        return datetime.utcnow()

    # Handle relative times like "1 hour ago", "15 minutes ago"
    relative_match = re.match(r'(\d+)\s+(minute|hour|day)s?\s+ago', time_str.lower())
    if relative_match:
        value = int(relative_match.group(1))
        unit = relative_match.group(2)

        if unit == 'minute':
            delta = timedelta(minutes=value)
        elif unit == 'hour':
            delta = timedelta(hours=value)
        elif unit == 'day':
            delta = timedelta(days=value)

        return datetime.utcnow() - delta

    # Try to parse as ISO format
    try:
        # Handle both with and without 'Z' suffix
        # Always return timezone-naive UTC for consistency
        if time_str.endswith('Z'):
            dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            return dt.replace(tzinfo=None)  # Strip timezone, treat as UTC
        else:
            dt = datetime.fromisoformat(time_str)
            # If timezone-aware, convert to naive UTC
            if dt.tzinfo is not None:
                return dt.replace(tzinfo=None)
            return dt
    except ValueError:
        raise ValueError(f"Invalid time format: {time_str}")


def main():
    """Main entry point for CLI."""
    import time as time_module
    import structlog
    from pulp_access_logs_exporter.logging_config import setup_logging
    from pulp_access_logs_exporter.cloudwatch import (
        build_query,
        fetch_cloudwatch_logs,
        convert_to_arrow_table,
    )
    from pulp_access_logs_exporter.writer import write_parquet

    setup_logging()
    log = structlog.get_logger()

    start_time = time_module.time()
    args = parse_args()

    log.info("starting export")

    # 1. Parse start/end times
    start_dt = parse_time(args.start_time)
    end_dt = parse_time(args.end_time)
    log.info(
        "time range parsed",
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
        duration=str(end_dt - start_dt),
    )

    # 2. Build CloudWatch Logs Insights query
    query = build_query(args.filter_paths, args.exclude_paths)
    log.info(
        "query built",
        filter_paths=args.filter_paths,
        exclude_paths=args.exclude_paths,
    )

    # 3. Fetch logs from CloudWatch
    log.info(
        "querying cloudwatch",
        log_group=args.cloudwatch_group,
        region=args.aws_region,
    )

    results = fetch_cloudwatch_logs(
        log_group=args.cloudwatch_group,
        query=query,
        start_time=int(start_dt.timestamp()),
        end_time=int(end_dt.timestamp()),
        region=args.aws_region,
    )

    if not results:
        log.info("no logs found in the specified time range")
        return 0

    # 4. Convert to PyArrow Table
    table = convert_to_arrow_table(results)
    log.info("converted to arrow table", schema=str(table.schema))

    # 5. Write Parquet file
    log.info("writing parquet file", output=args.output_path)

    # Pass S3 credentials if provided
    s3_credentials = None
    if args.s3_access_key_id and args.s3_secret_access_key:
        s3_credentials = {
            'access_key': args.s3_access_key_id,
            'secret_key': args.s3_secret_access_key,
        }
        if args.s3_session_token:
            s3_credentials['session_token'] = args.s3_session_token

    # Determine S3 region (use s3_region if specified, otherwise fall back to aws_region)
    s3_region = args.s3_region or args.aws_region

    write_parquet(table, args.output_path, s3_credentials=s3_credentials, region=s3_region)

    # 6. Log final statistics
    elapsed = time_module.time() - start_time
    log.info(
        "export complete",
        records=len(table),
        elapsed_seconds=round(elapsed, 2),
        output=args.output_path,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
