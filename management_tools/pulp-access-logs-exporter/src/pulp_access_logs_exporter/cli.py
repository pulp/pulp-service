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
        "--cloudwatch-stream",
        help="CloudWatch stream name (optional)",
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
        if time_str.endswith('Z'):
            return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        else:
            return datetime.fromisoformat(time_str)
    except ValueError:
        raise ValueError(f"Invalid time format: {time_str}")


def main():
    """Main entry point for CLI."""
    import time as time_module
    from pulp_access_logs_exporter.cloudwatch import (
        build_query,
        fetch_cloudwatch_logs,
        convert_to_arrow_table,
    )
    from pulp_access_logs_exporter.writer import write_parquet

    start_time = time_module.time()
    args = parse_args()

    print("=" * 60)
    print("Pulp Access Logs Exporter")
    print("=" * 60)

    # 1. Parse start/end times
    print("\nParsing time range...")
    start_dt = parse_time(args.start_time)
    end_dt = parse_time(args.end_time)

    print(f"  Start: {start_dt.isoformat()}")
    print(f"  End:   {end_dt.isoformat()}")
    print(f"  Duration: {end_dt - start_dt}")

    # 2. Build CloudWatch Logs Insights query
    print("\nBuilding CloudWatch Logs Insights query...")
    query = build_query(args.filter_paths, args.exclude_paths)
    print(f"  Filter paths: {args.filter_paths}")
    print(f"  Exclude paths: {args.exclude_paths}")

    # 3. Fetch logs from CloudWatch
    print(f"\nQuerying CloudWatch Logs...")
    print(f"  Log group: {args.cloudwatch_group}")
    print(f"  Region: {args.aws_region}")

    results = fetch_cloudwatch_logs(
        log_group=args.cloudwatch_group,
        query=query,
        start_time=int(start_dt.timestamp()),
        end_time=int(end_dt.timestamp()),
        region=args.aws_region,
    )

    if not results:
        print("\nNo logs found in the specified time range.")
        return 0

    # 4. Convert to PyArrow Table
    print("\nConverting to PyArrow Table...")
    table = convert_to_arrow_table(results)
    print(f"  Schema: {table.schema}")

    # 5. Write Parquet file
    print("\nWriting Parquet file...")
    print(f"  Output: {args.output_path}")
    write_parquet(table, args.output_path)

    # 6. Print statistics
    elapsed = time_module.time() - start_time
    print("\n" + "=" * 60)
    print("Export completed successfully!")
    print("=" * 60)
    print(f"  Records exported: {len(table)}")
    print(f"  Time elapsed: {elapsed:.2f} seconds")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
