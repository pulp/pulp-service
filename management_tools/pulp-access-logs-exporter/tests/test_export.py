"""Integration and unit tests for access log export."""

import re
import pytest
import pandas as pd
import pyarrow.parquet as pq


pytestmark = pytest.mark.integration


def test_cloudwatch_to_parquet_conversion(sample_cloudwatch_results, sample_parquet_path):
    """Test conversion from CloudWatch results to Parquet file."""
    from pulp_access_logs_exporter.cloudwatch import convert_to_arrow_table
    from pulp_access_logs_exporter.writer import write_parquet

    # Convert CloudWatch results to PyArrow Table
    table = convert_to_arrow_table(sample_cloudwatch_results)

    # Write to Parquet
    write_parquet(table, sample_parquet_path)

    # Read back and validate
    read_table = pq.read_table(sample_parquet_path)
    df = read_table.to_pandas()

    # Basic validations
    assert len(df) == 2, "Should have 2 records"

    # All records must have these fields (cannot be null)
    assert df['timestamp'].notna().all(), "All records must have timestamp"
    assert df['message'].notna().all(), "All records must have message"
    assert df['domain'].notna().all(), "All records must have domain"
    assert df['distribution'].notna().all(), "All records must have distribution"
    assert df['package'].notna().all(), "All records must have package"
    assert df['status_code'].notna().all(), "All records must have status_code"
    assert df['user_agent'].notna().all(), "All records must have user_agent"
    assert df['x_forwarded_for'].notna().all(), "All records must have x_forwarded_for"

    # Check authenticated user (first record)
    assert df.iloc[0]['user'] == 'testuser', "First record should have user"
    assert df.iloc[0]['org_id'] == 'org123', "First record should have org_id"
    assert df.iloc[0]['domain'] == 'domain1'
    assert df.iloc[0]['distribution'] == 'dist1'
    assert df.iloc[0]['package'] == 'package1'
    assert df.iloc[0]['status_code'] == 200
    assert df.iloc[0]['user_agent'] == 'uv/0.9.26 {}'
    assert df.iloc[0]['x_forwarded_for'] == '23.48.249.160'

    # Check unauthenticated user (second record) - should be null
    assert df.iloc[1]['user'] is None or pd.isna(df.iloc[1]['user']), \
        "Second record should have null user"
    assert df.iloc[1]['org_id'] is None or pd.isna(df.iloc[1]['org_id']), \
        "Second record should have null org_id"
    assert df.iloc[1]['domain'] == 'domain2'
    assert df.iloc[1]['distribution'] == 'dist2'
    assert df.iloc[1]['package'] == 'package2'
    assert df.iloc[1]['status_code'] == 404
    assert df.iloc[1]['user_agent'] == 'pip/23.0'
    assert df.iloc[1]['x_forwarded_for'] == '10.20.30.40'


def test_raw_log_parsing_validation(sample_cloudwatch_results, sample_parquet_path):
    """Validate that parsed fields match raw log content."""
    from pulp_access_logs_exporter.cloudwatch import convert_to_arrow_table
    from pulp_access_logs_exporter.writer import write_parquet

    # Convert and write
    table = convert_to_arrow_table(sample_cloudwatch_results)
    write_parquet(table, sample_parquet_path)

    # Read back
    read_table = pq.read_table(sample_parquet_path)
    df = read_table.to_pandas()

    # Validate first record by parsing raw log
    raw_log = df.iloc[0]['message']

    # Parse user_agent from raw log
    agent_match = re.search(r'"-" "([^"]+)" \d+ x_forwarded_for', raw_log)
    assert agent_match, "Should find user_agent in raw log"
    expected_agent = agent_match.group(1)
    actual_agent = df.iloc[0]['user_agent']
    assert actual_agent == expected_agent, f"User agent mismatch: {actual_agent} != {expected_agent}"

    # Parse x_forwarded_for
    xff_match = re.search(r'x_forwarded_for:"([^"]+)"', raw_log)
    assert xff_match, "Should find x_forwarded_for in raw log"
    # Extract first IP from comma-separated list
    expected_xff = xff_match.group(1).split(',')[0]
    actual_xff = df.iloc[0]['x_forwarded_for']
    assert actual_xff == expected_xff, f"XFF mismatch: {actual_xff} != {expected_xff}"

    # Parse status code
    status_match = re.search(r'HTTP/1.1" (\d+) ', raw_log)
    assert status_match, "Should find status code in raw log"
    expected_status = int(status_match.group(1))
    actual_status = df.iloc[0]['status_code']
    assert actual_status == expected_status, f"Status code mismatch: {actual_status} != {expected_status}"


def test_query_building():
    """Test CloudWatch Logs Insights query construction."""
    from pulp_access_logs_exporter.cloudwatch import build_query

    query = build_query()

    # Basic query structure checks
    assert 'fields @timestamp, @message' in query
    assert 'filter @message like' in query
    assert '/api/pypi' in query or r'\/api\/pypi' in query
    assert 'parse' in query.lower()


@pytest.mark.unit
def test_schema_definition():
    """Test that schema is properly defined."""
    from pulp_access_logs_exporter.writer import SCHEMA
    import pyarrow as pa

    # Check schema has expected fields
    field_names = [field.name for field in SCHEMA]

    expected_fields = [
        'timestamp',
        'message',
        'user',
        'org_id',
        'domain',
        'distribution',
        'package',
        'status_code',
        'user_agent',
        'x_forwarded_for',
    ]

    for expected in expected_fields:
        assert expected in field_names, f"Schema should include {expected}"

    # Check types
    assert SCHEMA.field('timestamp').type == pa.timestamp('ns')
    assert SCHEMA.field('status_code').type == pa.int16()
    assert SCHEMA.field('message').type == pa.string()
