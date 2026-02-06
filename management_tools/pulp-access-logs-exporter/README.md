# Pulp Access Logs Exporter

Export Pulp PyPI access logs from CloudWatch Logs to Parquet format for analytics and monitoring.

## Overview

This package provides a CLI tool that:
- Queries CloudWatch Logs Insights for Pulp PyPI access logs
- Filters for `/api/pypi/*` endpoints (excludes health checks)
- Exports structured data to Parquet format
- Supports local file or S3 bucket output

## Installation

### From Pulp PyPI (recommended)

```bash
pip install --index-url https://pulp.example.com/api/pypi/internal/simple/ \
  pulp-access-logs-exporter
```

### From source

```bash
cd management_tools/pulp-access-logs-exporter
pip install -e .
```

## Usage

### Basic usage

```bash
export-access-logs \
  --cloudwatch-group /aws/containerinsights/pulp-prod/application \
  --start-time "2026-02-04T14:00:00Z" \
  --end-time "2026-02-04T15:00:00Z" \
  --output-path /tmp/logs.parquet
```

### Export to S3

```bash
export-access-logs \
  --cloudwatch-group /aws/containerinsights/pulp-prod/application \
  --start-time "1 hour ago" \
  --end-time "15 minutes ago" \
  --output-path s3://bucket/logs/2026-02-04-14.parquet \
  --aws-region us-east-1
```

## CLI Arguments

- `--cloudwatch-group`: CloudWatch log group name (required)
- `--cloudwatch-stream`: CloudWatch stream name (optional)
- `--start-time`: Start timestamp - ISO format or relative (e.g., "1 hour ago")
- `--end-time`: End timestamp - ISO format or relative (e.g., "now")
- `--output-path`: Local file path or S3 URI (s3://bucket/key)
- `--aws-region`: AWS region (default: us-east-1)
- `--filter-paths`: Include only these path prefixes (default: /api/pypi/)
- `--exclude-paths`: Exclude these paths (default: /livez,/status)

## Parquet Schema

The exported Parquet files contain the following fields:

- `timestamp`: Event timestamp (timestamp[ns])
- `message`: Full raw log entry (string)
- `user`: Username from authentication (string, nullable)
- `org_id`: Organization ID (string, nullable)
- `domain`: Pulp domain name (string)
- `distribution`: Distribution name (string)
- `package`: Package name (string)
- `status_code`: HTTP status code (int16)
- `user_agent`: User-Agent header with metadata (string)
- `x_forwarded_for`: Client IP from X-Forwarded-For (string)

**Note**: `user` and `org_id` are nullable for unauthenticated requests (appear as `null` in Parquet).

## AWS Credentials

The tool uses boto3's standard credential chain:
1. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
2. AWS credentials file (`~/.aws/credentials`)
3. IAM role (when running in AWS)
4. Container credentials (ECS/EKS)

In OpenShift with IRSA, credentials are automatically provided via the service account.

## Development

### Running tests

```bash
# Unit tests (no AWS credentials needed)
pytest tests/ -m "not integration"

# Integration tests (requires AWS credentials)
pytest tests/ -m integration

# All tests
pytest tests/
```

### Building the package

```bash
python -m build
# Creates dist/pulp_access_logs_exporter-2026.02.06.0.tar.gz
```

### Uploading to Pulp PyPI

```bash
twine upload --repository-url https://pulp.example.com/api/pypi/internal/simple/ \
  dist/pulp_access_logs_exporter-2026.02.06.0.tar.gz
```

**Versioning**: Uses CalVer format `YYYY.0M.0D.MICRO` (e.g., 2026.02.06.0 for first release on Feb 6, 2026)

## Architecture

```
CloudWatch Logs → Logs Insights Query → PyArrow Table → Parquet → S3
                  (boto3)              (pyarrow)       (pyarrow)
```

Key features:
- **Server-side parsing**: CloudWatch Logs Insights does all log parsing
- **Time-based chunking**: Handles >10K entries/hour by splitting into 5-minute chunks
- **PyArrow native**: No pandas dependency for core export (only for test validation)
- **Efficient storage**: Snappy compression, columnar format

## License

See repository root for license information.
