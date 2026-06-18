"""Tests for CloudWatch query truncation handling and automatic subdivision."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from pulp_access_logs_exporter.cloudwatch import (
    CLOUDWATCH_RESULT_LIMIT,
    MAX_SUBDIVISION_DEPTH,
    _fetch_chunk,
    fetch_cloudwatch_logs,
)


def _make_cloudwatch_record(index):
    """Build a single CloudWatch Logs Insights result row."""
    return [
        {"field": "@timestamp", "value": f"2026-06-01T00:00:{index:02d}.000Z"},
        {"field": "@message", "value": f"log line {index}"},
    ]


def _make_query_result(count, records_matched=None):
    """Build a get_query_results response with `count` records."""
    return {
        "status": "Complete",
        "results": [_make_cloudwatch_record(idx % 60) for idx in range(count)],
        "statistics": {
            "recordsMatched": records_matched or count,
            "recordsScanned": count * 2,
        },
    }


@pytest.fixture
def mock_logs_client():
    client = MagicMock()
    client.start_query.return_value = {"queryId": "test-query-id"}
    return client


@pytest.mark.unit
class TestFetchChunkSubdivision:
    def test_no_subdivision_when_under_limit(self, mock_logs_client):
        mock_logs_client.get_query_results.return_value = _make_query_result(500)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 6, 1, 0, 5, 0)

        results = _fetch_chunk(mock_logs_client, "/log-group", "query", start, end)

        assert len(results) == 500
        assert mock_logs_client.start_query.call_count == 1

    def test_subdivides_on_truncation(self, mock_logs_client):
        truncated = _make_query_result(CLOUDWATCH_RESULT_LIMIT, records_matched=11882)
        not_truncated = _make_query_result(6000)

        mock_logs_client.get_query_results.side_effect = [
            truncated,
            not_truncated,
            not_truncated,
        ]

        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 6, 1, 0, 5, 0)

        results = _fetch_chunk(mock_logs_client, "/log-group", "query", start, end)

        assert len(results) == 12000
        assert mock_logs_client.start_query.call_count == 3

    def test_recursive_subdivision(self, mock_logs_client):
        truncated = _make_query_result(CLOUDWATCH_RESULT_LIMIT)
        ok_result = _make_query_result(3000)

        mock_logs_client.get_query_results.side_effect = [
            truncated,
            truncated,
            ok_result,
            ok_result,
            ok_result,
        ]

        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 6, 1, 0, 5, 0)

        results = _fetch_chunk(mock_logs_client, "/log-group", "query", start, end)

        assert len(results) == 3000 * 3
        assert mock_logs_client.start_query.call_count == 5

    def test_stops_at_max_depth(self, mock_logs_client):
        truncated = _make_query_result(CLOUDWATCH_RESULT_LIMIT, records_matched=15000)
        mock_logs_client.get_query_results.return_value = truncated

        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 6, 1, 0, 5, 0)

        results = _fetch_chunk(
            mock_logs_client,
            "/log-group",
            "query",
            start,
            end,
            depth=MAX_SUBDIVISION_DEPTH,
        )

        assert len(results) == CLOUDWATCH_RESULT_LIMIT
        assert mock_logs_client.start_query.call_count == 1

    def test_midpoint_splits_time_evenly(self, mock_logs_client):
        truncated = _make_query_result(CLOUDWATCH_RESULT_LIMIT)
        ok_result = _make_query_result(5000)

        mock_logs_client.get_query_results.side_effect = [
            truncated,
            ok_result,
            ok_result,
        ]

        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 6, 1, 0, 10, 0)

        _fetch_chunk(mock_logs_client, "/log-group", "query", start, end)

        calls = mock_logs_client.start_query.call_args_list
        first_half_end = calls[1][1]["endTime"]
        second_half_start = calls[2][1]["startTime"]
        assert first_half_end == second_half_start

        midpoint = start + (end - start) / 2
        assert first_half_end == int(midpoint.timestamp())


@pytest.mark.unit
class TestFetchCloudwatchLogsIntegration:
    @patch("pulp_access_logs_exporter.cloudwatch.boto3")
    def test_multiple_chunks_with_subdivision(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.start_query.return_value = {"queryId": "q1"}

        truncated = _make_query_result(CLOUDWATCH_RESULT_LIMIT)
        ok_small = _make_query_result(100)

        mock_client.get_query_results.side_effect = [
            ok_small,
            truncated,
            ok_small,
            ok_small,
        ]

        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 6, 1, 0, 10, 0)

        results = fetch_cloudwatch_logs(
            log_group="/log-group",
            query="fields @timestamp",
            start_time=int(start.timestamp()),
            end_time=int(end.timestamp()),
        )

        assert len(results) == 100 + 100 + 100
        assert mock_client.start_query.call_count == 4
