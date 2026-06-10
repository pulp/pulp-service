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


@pytest.fixture
def sample_content_cloudwatch_results():
    """Sample CloudWatch results wrapping content-app log lines."""
    return [
        {
            '@timestamp': '2026-06-09 14:30:00.000',
            'message': '10.128.4.123 [09/Jun/2026:14:30:00 +0000] "GET /api/pulp-content/public-rhai/rhoai/3.5-EA2/cpu-ubi9/requests-2.34.2-2-py3-none-any.whl HTTP/1.1" 200 19456789 "-" "pip/24.0" cache:"HIT" artifact_size:"19456789" rh_org_id:"123456" x_forwarded_for:"23.48.249.160,10.0.0.1"',
        },
        {
            '@timestamp': '2026-06-09 14:30:01.000',
            'message': '10.128.4.124 [09/Jun/2026:14:30:01 +0000] "GET /api/pulp-content/public-rhai/rhoai/3.5-EA2/cpu-ubi9/numpy-1.26.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata HTTP/1.1" 200 1234 "-" "uv/0.9.26" cache:"MISS" artifact_size:"1234" rh_org_id:"789012" x_forwarded_for:"10.20.30.40"',
        },
        {
            '@timestamp': '2026-06-09 14:30:02.000',
            'message': '10.128.4.125 [09/Jun/2026:14:30:02 +0000] "GET /api/pulp-content/public-copr/packit/teemtee-tmt-4901/fedora-43-x86_64/Packages/b/bash-5.2.26-4.fc43.x86_64.rpm HTTP/1.1" 200 5678901 "-" "dnf/4.18.0" cache:"MISS" artifact_size:"5678901" rh_org_id:"-" x_forwarded_for:"192.168.1.1"',
        },
        {
            '@timestamp': '2026-06-09 14:30:03.000',
            'message': '10.128.4.126 [09/Jun/2026:14:30:03 +0000] "GET /api/pulp-content/public-copr/packit/teemtee-tmt-4901/fedora-43-x86_64/repodata/repomd.xml HTTP/1.1" 200 3456 "-" "dnf/4.18.0" cache:"HIT" artifact_size:"3456" rh_org_id:"-" x_forwarded_for:"192.168.1.2"',
        },
        {
            '@timestamp': '2026-06-09 14:30:04.000',
            'message': '10.128.4.127 [09/Jun/2026:14:30:04 +0000] "GET /api/pulp-content/ccac33ac/templates/kernel-core-6.8.0-300.fc40.x86_64.rpm HTTP/1.1" 200 99887766 "-" "yum/4.0" cache:"HIT" artifact_size:"99887766" rh_org_id:"555666" x_forwarded_for:"172.16.0.1"',
        },
    ]


@pytest.fixture
def sample_content_parquet_path(tmp_path):
    """Temporary path for content Parquet file testing."""
    return str(tmp_path / "test_content_logs.parquet")
