"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture
def sample_cloudwatch_results():
    """Sample CloudWatch Logs Insights results for testing."""
    return [
        {
            '@timestamp': '2026-02-04T14:30:00.123Z',
            '@message': 'pulp [abc-123]: 192.168.1.1 - user:testuser org_id:org123 [04/Feb/2026:14:30:00 +0000] "GET /api/pypi/domain1/dist1/simple/package1/ HTTP/1.1" 200 456 "-" "uv/0.9.26 {}" 550 x_forwarded_for:"23.48.249.160,10.0.0.1"',
            'user': 'testuser',
            'org_id': 'org123',
            'domain': 'domain1',
            'distribution': 'dist1',
            'package': 'package1',
            'status_code': '200',
            'user_agent': 'uv/0.9.26 {}',
            'x_forwarded_for': '23.48.249.160',
        },
        {
            '@timestamp': '2026-02-04T14:30:01.456Z',
            '@message': 'pulp [def-456]: 192.168.1.2 - user:- org_id:- [04/Feb/2026:14:30:01 +0000] "GET /api/pypi/domain2/dist2/simple/package2/ HTTP/1.1" 404 0 "-" "pip/23.0" 120 x_forwarded_for:"10.20.30.40"',
            'user': '-',
            'org_id': '-',
            'domain': 'domain2',
            'distribution': 'dist2',
            'package': 'package2',
            'status_code': '404',
            'user_agent': 'pip/23.0',
            'x_forwarded_for': '10.20.30.40',
        },
    ]


@pytest.fixture
def sample_parquet_path(tmp_path):
    """Temporary path for Parquet file testing."""
    return str(tmp_path / "test_logs.parquet")
