"""CLI interface for access logs exporter."""

import argparse
import sys
from datetime import datetime
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


def main():
    """Main entry point for CLI."""
    args = parse_args()

    # TODO: Implement main logic
    # 1. Parse start/end times
    # 2. Build CloudWatch Logs Insights query
    # 3. Fetch logs from CloudWatch
    # 4. Convert to PyArrow Table
    # 5. Write Parquet file
    # 6. Print statistics

    print(f"CloudWatch group: {args.cloudwatch_group}")
    print(f"Time range: {args.start_time} to {args.end_time}")
    print(f"Output: {args.output_path}")
    print(f"Region: {args.aws_region}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
