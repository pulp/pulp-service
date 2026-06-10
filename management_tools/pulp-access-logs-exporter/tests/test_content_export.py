"""Tests for content-app log exporter."""

import pyarrow.parquet as pq

from pulp_access_logs_exporter.content_parser import (
    matches_content_type,
    parse_content_log_line,
    parse_content_path,
    parse_rpm_filename,
    parse_wheel_filename,
)
from pulp_access_logs_exporter.content_cloudwatch import (
    build_content_query,
    convert_content_to_arrow_table,
)
from pulp_access_logs_exporter.content_schemas import PYTHON_SCHEMA, RPM_SCHEMA
from pulp_access_logs_exporter.writer import write_parquet


class TestParseContentLogLine:
    def test_parses_all_fields(self):
        line = '10.128.4.123 [09/Jun/2026:14:30:00 +0000] "GET /api/pulp-content/default/dist/pkg-1.0-py3-none-any.whl HTTP/1.1" 200 19456789 "-" "pip/24.0" cache:"HIT" artifact_size:"19456789" rh_org_id:"123456" x_forwarded_for:"23.48.249.160,10.0.0.1"'
        result = parse_content_log_line(line)
        assert result is not None
        assert result["src_ip"] == "10.128.4.123"
        assert result["method"] == "GET"
        assert result["path"] == "/api/pulp-content/default/dist/pkg-1.0-py3-none-any.whl"
        assert result["status"] == "200"
        assert result["user_agent"] == "pip/24.0"
        assert result["cache"] == "HIT"
        assert result["artifact_size"] == "19456789"
        assert result["rh_org_id"] == "123456"
        assert result["x_forwarded_for"] == "23.48.249.160,10.0.0.1"

    def test_parses_cache_miss(self):
        line = '10.0.0.1 [09/Jun/2026:14:30:00 +0000] "GET /api/pulp-content/d/r/file.rpm HTTP/1.1" 200 100 "-" "dnf/4.0" cache:"MISS" artifact_size:"100" rh_org_id:"-" x_forwarded_for:"1.2.3.4"'
        result = parse_content_log_line(line)
        assert result is not None
        assert result["cache"] == "MISS"
        assert result["rh_org_id"] == "-"

    def test_returns_none_for_non_matching(self):
        result = parse_content_log_line("not a valid log line")
        assert result is None

    def test_parses_empty_org_id(self):
        line = '10.0.0.1 [09/Jun/2026:14:30:00 +0000] "GET /api/pulp-content/d/r/file.whl HTTP/1.1" 200 100 "-" "pip/24.0" cache:"HIT" artifact_size:"100" rh_org_id:"-" x_forwarded_for:"1.2.3.4"'
        result = parse_content_log_line(line)
        assert result["rh_org_id"] == "-"


class TestParseWheelFilename:
    def test_standard_wheel(self):
        result = parse_wheel_filename("requests-2.31.0-py3-none-any.whl")
        assert result is not None
        assert result["package_name"] == "requests"
        assert result["package_version"] == "2.31.0"
        assert result["build_tag"] is None
        assert result["pyver"] == "py3"
        assert result["abi"] == "none"
        assert result["architecture"] == "any"

    def test_platform_specific_wheel(self):
        result = parse_wheel_filename(
            "numpy-1.26.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
        )
        assert result is not None
        assert result["package_name"] == "numpy"
        assert result["package_version"] == "1.26.4"
        assert result["pyver"] == "cp312"
        assert result["abi"] == "cp312"
        assert result["architecture"] == "manylinux_2_17_x86_64.manylinux2014_x86_64"

    def test_wheel_with_build_tag(self):
        result = parse_wheel_filename("requests-2.34.2-2-py3-none-any.whl")
        assert result is not None
        assert result["package_name"] == "requests"
        assert result["package_version"] == "2.34.2"
        assert result["build_tag"] == "2"
        assert result["pyver"] == "py3"

    def test_wheel_metadata(self):
        result = parse_wheel_filename(
            "numpy-1.26.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata"
        )
        assert result is not None
        assert result["package_name"] == "numpy"
        assert result["package_version"] == "1.26.4"

    def test_malformed_wheel_returns_none(self):
        result = parse_wheel_filename("not-a-wheel.txt")
        assert result is None


class TestParseRpmFilename:
    def test_standard_rpm(self):
        result = parse_rpm_filename("bash-5.2.26-4.fc43.x86_64.rpm")
        assert result is not None
        assert result["package_name"] == "bash"
        assert result["package_version"] == "5.2.26"
        assert result["release"] == "4.fc43"
        assert result["architecture"] == "x86_64"
        assert result["epoch"] == 0

    def test_rpm_with_epoch(self):
        result = parse_rpm_filename("jay-3:3.10-4.fc3.x86_64.rpm")
        assert result is not None
        assert result["package_name"] == "jay"
        assert result["epoch"] == 3
        assert result["package_version"] == "3.10"
        assert result["release"] == "4.fc3"

    def test_noarch_rpm(self):
        result = parse_rpm_filename("nodejs26-npm-11.13.0-1.26.1.0.5.fc45.noarch.rpm")
        assert result is not None
        assert result["package_name"] == "nodejs26-npm"
        assert result["package_version"] == "11.13.0"
        assert result["release"] == "1.26.1.0.5.fc45"
        assert result["architecture"] == "noarch"

    def test_complex_name_rpm(self):
        result = parse_rpm_filename("kernel-core-6.8.0-300.fc40.x86_64.rpm")
        assert result is not None
        assert result["package_name"] == "kernel-core"
        assert result["package_version"] == "6.8.0"
        assert result["release"] == "300.fc40"

    def test_malformed_rpm_returns_none(self):
        result = parse_rpm_filename("notavalidrpm.rpm")
        assert result is None

    def test_non_rpm_returns_none(self):
        result = parse_rpm_filename("somefile.txt")
        assert result is None


class TestContentTypeFiltering:
    def test_python_whl_matches(self):
        assert matches_content_type("pkg-1.0-py3-none-any.whl", "python") is True

    def test_python_metadata_matches(self):
        assert matches_content_type("pkg-1.0-py3-none-any.whl.metadata", "python") is True

    def test_rpm_matches(self):
        assert matches_content_type("bash-5.2-1.fc43.x86_64.rpm", "rpm") is True

    def test_python_rejects_rpm(self):
        assert matches_content_type("bash-5.2-1.fc43.x86_64.rpm", "python") is False

    def test_rpm_rejects_whl(self):
        assert matches_content_type("pkg-1.0-py3-none-any.whl", "rpm") is False

    def test_rejects_repodata(self):
        assert matches_content_type("repomd.xml", "python") is False
        assert matches_content_type("repomd.xml", "rpm") is False


class TestParseContentPath:
    def test_python_path(self):
        result = parse_content_path(
            "/api/pulp-content/public-rhai/rhoai/3.5-EA2/cpu-ubi9/requests-2.34.2-2-py3-none-any.whl"
        )
        assert result is not None
        assert result["domain"] == "public-rhai"
        assert result["distribution"] == "rhoai/3.5-EA2/cpu-ubi9"
        assert result["filename"] == "requests-2.34.2-2-py3-none-any.whl"

    def test_rpm_path(self):
        result = parse_content_path(
            "/api/pulp-content/public-copr/packit/tmt/fedora-43-x86_64/Packages/b/bash-5.2.26-4.fc43.x86_64.rpm"
        )
        assert result is not None
        assert result["domain"] == "public-copr"
        assert result["distribution"] == "packit/tmt/fedora-43-x86_64/Packages/b"
        assert result["filename"] == "bash-5.2.26-4.fc43.x86_64.rpm"

    def test_non_content_path_returns_none(self):
        result = parse_content_path("/api/pypi/default/dist/simple/pkg/")
        assert result is None

    def test_too_short_path_returns_none(self):
        result = parse_content_path("/api/pulp-content/domain")
        assert result is None


class TestContentToParquetPython:
    def test_converts_python_downloads(self, sample_content_cloudwatch_results, sample_content_parquet_path):
        table = convert_content_to_arrow_table(
            sample_content_cloudwatch_results, "python"
        )
        assert table.num_rows == 2
        assert table.schema == PYTHON_SCHEMA

        write_parquet(table, sample_content_parquet_path)
        read_table = pq.read_table(sample_content_parquet_path)
        assert read_table.num_rows == 2

        rows = read_table.to_pydict()
        assert rows["package_name"][0] == "requests"
        assert rows["package_version"][0] == "2.34.2"
        assert rows["build_tag"][0] == "2"
        assert rows["cache_hit"][0] is True
        assert rows["artifact_size"][0] == 19456789
        assert rows["org_id"][0] == "123456"

        assert rows["package_name"][1] == "numpy"
        assert rows["package_version"][1] == "1.26.4"
        assert rows["cache_hit"][1] is False


class TestContentToParquetRpm:
    def test_converts_rpm_downloads(self, sample_content_cloudwatch_results, sample_content_parquet_path):
        table = convert_content_to_arrow_table(
            sample_content_cloudwatch_results, "rpm"
        )
        assert table.num_rows == 2
        assert table.schema == RPM_SCHEMA

        write_parquet(table, sample_content_parquet_path)
        read_table = pq.read_table(sample_content_parquet_path)
        assert read_table.num_rows == 2

        rows = read_table.to_pydict()
        assert rows["package_name"][0] == "bash"
        assert rows["package_version"][0] == "5.2.26"
        assert rows["release"][0] == "4.fc43"
        assert rows["architecture"][0] == "x86_64"
        assert rows["epoch"][0] == 0
        assert rows["org_id"][0] is None
        assert rows["cache_hit"][0] is False

        assert rows["package_name"][1] == "kernel-core"
        assert rows["package_version"][1] == "6.8.0"
        assert rows["cache_hit"][1] is True
        assert rows["org_id"][1] == "555666"


class TestEmptyContentResults:
    def test_empty_results_python(self):
        table = convert_content_to_arrow_table([], "python")
        assert table.num_rows == 0
        assert table.schema == PYTHON_SCHEMA

    def test_empty_results_rpm(self):
        table = convert_content_to_arrow_table([], "rpm")
        assert table.num_rows == 0
        assert table.schema == RPM_SCHEMA


class TestContentQueryBuilding:
    def test_query_contains_content_path_filter(self):
        query = build_content_query()
        assert "pulp-content" in query
        assert "livez" in query
        assert "status" in query

    def test_query_fetches_timestamp_and_message(self):
        query = build_content_query()
        assert "@timestamp" in query
        assert "message" in query
