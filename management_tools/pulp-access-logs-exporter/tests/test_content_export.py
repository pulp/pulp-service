"""Tests for content-app log exporter."""

import pyarrow.parquet as pq

from pulp_access_logs_exporter.content_parser import (
    matches_content_type,
    parse_content_log_line,
    parse_content_path,
    parse_maven_distribution,
    parse_rpm_filename,
    parse_wheel_filename,
)
from pulp_access_logs_exporter.content_cloudwatch import (
    _parse_request_time_ms,
    build_content_query,
    convert_content_to_arrow_table,
)
from pulp_access_logs_exporter.content_schemas import MAVEN_SCHEMA, PYTHON_SCHEMA, RPM_SCHEMA
from pulp_access_logs_exporter.writer import write_parquet


class TestParseContentLogLine:
    def test_parses_all_fields(self):
        line = '10.128.4.123 [09/Jun/2026:14:30:00 +0000] "GET /api/pulp-content/default/dist/pkg-1.0-py3-none-any.whl HTTP/1.1" 200 19456789 "-" "pip/24.0" cache:"HIT" artifact_size:"19456789" rh_org_id:"123456" x_forwarded_for:"23.48.249.160,10.0.0.1"'
        result = parse_content_log_line(line)
        assert result is not None
        assert result["src_ip"] == "10.128.4.123"
        assert result["method"] == "GET"
        assert (
            result["path"] == "/api/pulp-content/default/dist/pkg-1.0-py3-none-any.whl"
        )
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

    def test_parses_request_time(self):
        line = '10.128.4.123 [09/Jun/2026:14:30:00 +0000] "GET /api/pulp-content/default/dist/pkg-1.0-py3-none-any.whl HTTP/1.1" 200 19456789 "-" "pip/24.0" cache:"HIT" artifact_size:"19456789" rh_org_id:"123456" x_forwarded_for:"23.48.249.160,10.0.0.1" request_time:"0.042000"'
        result = parse_content_log_line(line)
        assert result is not None
        assert result["request_time"] == "0.042000"

    def test_parses_without_request_time(self):
        line = '10.0.0.1 [09/Jun/2026:14:30:00 +0000] "GET /api/pulp-content/d/r/file.rpm HTTP/1.1" 200 100 "-" "dnf/4.0" cache:"MISS" artifact_size:"100" rh_org_id:"-" x_forwarded_for:"1.2.3.4"'
        result = parse_content_log_line(line)
        assert result is not None
        assert result["request_time"] is None


class TestParseRequestTimeMs:
    def test_converts_seconds_to_milliseconds(self):
        assert _parse_request_time_ms("0.042000") == 42
        assert _parse_request_time_ms("0.003200") == 3
        assert _parse_request_time_ms("0.156000") == 156
        assert _parse_request_time_ms("1.234567") == 1235

    def test_handles_none_and_dash(self):
        assert _parse_request_time_ms(None) is None
        assert _parse_request_time_ms("-") is None
        assert _parse_request_time_ms("") is None

    def test_handles_invalid_values(self):
        assert _parse_request_time_ms("invalid") is None
        assert _parse_request_time_ms("not_a_number") is None

    def test_rounds_to_integer_milliseconds(self):
        assert _parse_request_time_ms("0.0016") == 2  # 1.6ms rounds to 2
        assert _parse_request_time_ms("0.0019") == 2  # 1.9ms rounds to 2


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


class TestParseMavenDistribution:
    def test_standard_jar(self):
        result = parse_maven_distribution(
            "maven-releases/org/springframework/cloud/spring-cloud-config-server/4.3.0-redhat-1",
            "spring-cloud-config-server-4.3.0-redhat-1.jar",
        )
        assert result is not None
        assert result["distribution"] == "maven-releases"
        assert result["group_id"] == "org.springframework.cloud"
        assert result["package_name"] == "spring-cloud-config-server"
        assert result["package_version"] == "4.3.0-redhat-1"
        assert result["classifier"] is None
        assert result["packaging"] == "jar"
        assert result["architecture"] is None

    def test_pom_file(self):
        result = parse_maven_distribution(
            "maven-releases/org/springframework/spring-expression/5.3.18-redhat-1",
            "spring-expression-5.3.18-redhat-1.pom",
        )
        assert result is not None
        assert result["group_id"] == "org.springframework"
        assert result["package_name"] == "spring-expression"
        assert result["packaging"] == "pom"
        assert result["classifier"] is None

    def test_with_classifier(self):
        result = parse_maven_distribution(
            "maven-releases/net/minidev/json-smart/2.5.0",
            "json-smart-2.5.0-sources.jar",
        )
        assert result is not None
        assert result["group_id"] == "net.minidev"
        assert result["package_name"] == "json-smart"
        assert result["package_version"] == "2.5.0"
        assert result["classifier"] == "sources"
        assert result["packaging"] == "jar"

    def test_javadoc_classifier(self):
        result = parse_maven_distribution(
            "maven-releases/com/google/guava/guava/33.0.0-jre",
            "guava-33.0.0-jre-javadoc.jar",
        )
        assert result is not None
        assert result["group_id"] == "com.google.guava"
        assert result["package_name"] == "guava"
        assert result["package_version"] == "33.0.0-jre"
        assert result["classifier"] == "javadoc"
        assert result["packaging"] == "jar"

    def test_single_segment_group_id(self):
        result = parse_maven_distribution(
            "maven-releases/junit/junit/4.13.2",
            "junit-4.13.2.jar",
        )
        assert result is not None
        assert result["group_id"] == "junit"
        assert result["package_name"] == "junit"
        assert result["package_version"] == "4.13.2"

    def test_snapshot_distribution(self):
        result = parse_maven_distribution(
            "maven-snapshots/com/mycompany/app/my-app/1.0-SNAPSHOT",
            "my-app-1.0-SNAPSHOT.jar",
        )
        assert result is not None
        assert result["distribution"] == "maven-snapshots"
        assert result["group_id"] == "com.mycompany.app"
        assert result["package_name"] == "my-app"
        assert result["package_version"] == "1.0-SNAPSHOT"
        assert result["packaging"] == "jar"

    def test_deeply_nested_group_id(self):
        result = parse_maven_distribution(
            "maven-releases/io/opentelemetry/instrumentation/opentelemetry-instrumentation-api/2.10.0",
            "opentelemetry-instrumentation-api-2.10.0.jar",
        )
        assert result is not None
        assert result["group_id"] == "io.opentelemetry.instrumentation"
        assert result["package_name"] == "opentelemetry-instrumentation-api"
        assert result["package_version"] == "2.10.0"

    def test_multi_hyphen_version(self):
        result = parse_maven_distribution(
            "maven-releases/org/jboss/resteasy/resteasy-core/6.2.0-alpha-1",
            "resteasy-core-6.2.0-alpha-1.jar",
        )
        assert result is not None
        assert result["group_id"] == "org.jboss.resteasy"
        assert result["package_name"] == "resteasy-core"
        assert result["package_version"] == "6.2.0-alpha-1"
        assert result["classifier"] is None
        assert result["packaging"] == "jar"

    def test_redhat_milestone_version(self):
        result = parse_maven_distribution(
            "maven-releases/org/springframework/boot/spring-boot/3.0.0-M1-redhat-00001",
            "spring-boot-3.0.0-M1-redhat-00001.jar",
        )
        assert result is not None
        assert result["package_name"] == "spring-boot"
        assert result["package_version"] == "3.0.0-M1-redhat-00001"

    def test_too_few_segments_returns_none(self):
        result = parse_maven_distribution("maven-releases/junit/4.13.2", "junit-4.13.2.jar")
        assert result is None

    def test_filename_mismatch_returns_none(self):
        result = parse_maven_distribution(
            "maven-releases/org/example/my-lib/1.0.0",
            "wrong-name-1.0.0.jar",
        )
        assert result is None

    def test_no_extension_returns_none(self):
        result = parse_maven_distribution(
            "maven-releases/org/example/my-lib/1.0.0",
            "my-lib-1.0.0",
        )
        assert result is None


class TestContentTypeFiltering:
    def test_python_whl_matches(self):
        assert matches_content_type("pkg-1.0-py3-none-any.whl", "python") is True

    def test_python_metadata_matches(self):
        assert (
            matches_content_type("pkg-1.0-py3-none-any.whl.metadata", "python") is True
        )

    def test_rpm_matches(self):
        assert matches_content_type("bash-5.2-1.fc43.x86_64.rpm", "rpm") is True

    def test_python_rejects_rpm(self):
        assert matches_content_type("bash-5.2-1.fc43.x86_64.rpm", "python") is False

    def test_rpm_rejects_whl(self):
        assert matches_content_type("pkg-1.0-py3-none-any.whl", "rpm") is False

    def test_maven_jar_matches(self):
        assert matches_content_type("spring-core-6.2.0.jar", "maven") is True

    def test_maven_pom_matches(self):
        assert matches_content_type("spring-core-6.2.0.pom", "maven") is True

    def test_maven_rejects_jar_checksum(self):
        assert matches_content_type("spring-core-6.2.0.jar.sha1", "maven") is False

    def test_maven_rejects_pom_checksum(self):
        assert matches_content_type("spring-core-6.2.0.pom.sha1", "maven") is False

    def test_maven_rejects_rpm(self):
        assert matches_content_type("bash-5.2-1.fc43.x86_64.rpm", "maven") is False

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

    def test_rpm_path_strips_packages_dir(self):
        result = parse_content_path(
            "/api/pulp-content/public-copr/packit/tmt/fedora-43-x86_64/Packages/b/bash-5.2.26-4.fc43.x86_64.rpm"
        )
        assert result is not None
        assert result["domain"] == "public-copr"
        assert result["distribution"] == "packit/tmt/fedora-43-x86_64"
        assert result["filename"] == "bash-5.2.26-4.fc43.x86_64.rpm"

    def test_rpm_path_strips_repodata(self):
        result = parse_content_path(
            "/api/pulp-content/public-copr/packit/tmt/fedora-43-x86_64/repodata/repomd.xml"
        )
        assert result is not None
        assert result["distribution"] == "packit/tmt/fedora-43-x86_64"
        assert result["filename"] == "repomd.xml"

    def test_rpm_path_without_packages_dir(self):
        result = parse_content_path(
            "/api/pulp-content/ccac33ac/templates/kernel-core-6.8.0-300.fc40.x86_64.rpm"
        )
        assert result is not None
        assert result["domain"] == "ccac33ac"
        assert result["distribution"] == "templates"
        assert result["filename"] == "kernel-core-6.8.0-300.fc40.x86_64.rpm"

    def test_empty_distribution_after_stripping_returns_none(self):
        result = parse_content_path("/api/pulp-content/domain/repodata/repomd.xml")
        assert result is None

    def test_non_content_path_returns_none(self):
        result = parse_content_path("/api/pypi/default/dist/simple/pkg/")
        assert result is None

    def test_too_short_path_returns_none(self):
        result = parse_content_path("/api/pulp-content/domain")
        assert result is None


class TestContentToParquetPython:
    def test_converts_python_downloads(
        self, sample_content_cloudwatch_results, sample_content_parquet_path
    ):
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
        assert rows["distribution"][0] == "rhoai/3.5-EA2/cpu-ubi9"

        assert rows["package_name"][1] == "numpy"
        assert rows["package_version"][1] == "1.26.4"
        assert rows["cache_hit"][1] is False
        assert rows["distribution"][1] == "rhoai/3.5-EA2/cpu-ubi9"

        assert rows["request_time_ms"][0] == 3  # 0.003200s = 3ms
        assert rows["request_time_ms"][1] == 46  # 0.045600s = 45.6ms, rounds to 46

    def test_skips_invalid_and_non_matching_records(
        self, sample_content_cloudwatch_results
    ):
        extra_records = [
            {"message": "not a valid log line"},
            {
                "@timestamp": "2026-06-09 14:30:05.000",
                "message": '10.0.0.1 [09/Jun/2026:14:30:05 +0000] "GET /api/pypi/default/simple/pkg/ HTTP/1.1" 200 100 "-" "pip/24.0" cache:"HIT" artifact_size:"100" rh_org_id:"-" x_forwarded_for:"1.2.3.4"',
            },
        ]
        mixed_results = sample_content_cloudwatch_results + extra_records
        table = convert_content_to_arrow_table(mixed_results, "python")
        assert table.num_rows == 2

    def test_skips_empty_distribution_after_stripping(self):
        results = [
            {
                "@timestamp": "2026-06-09 14:30:00.000",
                "message": '10.0.0.1 [09/Jun/2026:14:30:00 +0000] "GET /api/pulp-content/domain/repodata/repomd.xml HTTP/1.1" 200 3456 "-" "dnf/4.18.0" cache:"HIT" artifact_size:"3456" rh_org_id:"-" x_forwarded_for:"1.2.3.4"',
            },
        ]
        table = convert_content_to_arrow_table(results, "rpm")
        assert table.num_rows == 0

    def test_skips_malformed_filename_and_warns(self, capsys):
        results = [
            {
                "@timestamp": "2026-06-09 14:30:00.000",
                "message": '10.0.0.1 [09/Jun/2026:14:30:00 +0000] "GET /api/pulp-content/default/dist/badfile.whl HTTP/1.1" 200 100 "-" "pip/24.0" cache:"HIT" artifact_size:"100" rh_org_id:"-" x_forwarded_for:"1.2.3.4"',
            },
        ]
        table = convert_content_to_arrow_table(results, "python")
        assert table.num_rows == 0
        captured = capsys.readouterr()
        assert "malformed" in captured.err.lower()

    def test_skips_bad_timestamp(self):
        results = [
            {
                "@timestamp": "",
                "message": '10.0.0.1 [09/Jun/2026:14:30:00 +0000] "GET /api/pulp-content/public-rhai/rhoai/3.5/requests-2.31.0-py3-none-any.whl HTTP/1.1" 200 100 "-" "pip/24.0" cache:"HIT" artifact_size:"100" rh_org_id:"-" x_forwarded_for:"1.2.3.4"',
            },
        ]
        table = convert_content_to_arrow_table(results, "python")
        assert table.num_rows == 0


class TestContentToParquetRpm:
    def test_converts_rpm_downloads(
        self, sample_content_cloudwatch_results, sample_content_parquet_path
    ):
        table = convert_content_to_arrow_table(sample_content_cloudwatch_results, "rpm")
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
        assert rows["distribution"][0] == "packit/teemtee-tmt-4901/fedora-43-x86_64"

        assert rows["package_name"][1] == "kernel-core"
        assert rows["package_version"][1] == "6.8.0"
        assert rows["cache_hit"][1] is True
        assert rows["org_id"][1] == "555666"
        assert rows["distribution"][1] == "templates"

        assert rows["request_time_ms"][0] == 78  # 0.078300s = 78ms
        assert rows["request_time_ms"][1] == 2  # 0.002500s = 2ms


class TestContentToParquetMaven:
    def test_converts_maven_downloads(
        self, sample_maven_cloudwatch_results, sample_content_parquet_path
    ):
        table = convert_content_to_arrow_table(
            sample_maven_cloudwatch_results, "maven"
        )
        assert table.num_rows == 3
        assert table.schema == MAVEN_SCHEMA

        write_parquet(table, sample_content_parquet_path)
        read_table = pq.read_table(sample_content_parquet_path)
        assert read_table.num_rows == 3

        rows = read_table.to_pydict()
        assert rows["domain"][0] == "balor-stage"
        assert rows["distribution"][0] == "maven-releases"
        assert rows["group_id"][0] == "org.springframework.cloud"
        assert rows["package_name"][0] == "spring-cloud-config-server"
        assert rows["package_version"][0] == "4.3.0-redhat-1"
        assert rows["classifier"][0] is None
        assert rows["packaging"][0] == "jar"
        assert rows["org_id"][0] == "5894300"
        assert rows["cache_hit"][0] is False
        assert rows["artifact_size"][0] == 18432000

        assert rows["group_id"][1] == "org.springframework"
        assert rows["package_name"][1] == "spring-expression"
        assert rows["packaging"][1] == "pom"

        assert rows["group_id"][2] == "net.minidev"
        assert rows["package_name"][2] == "json-smart"
        assert rows["classifier"][2] == "sources"
        assert rows["packaging"][2] == "jar"
        assert rows["cache_hit"][2] is True

        assert rows["request_time_ms"][0] == 156  # 0.156000s = 156ms
        assert rows["request_time_ms"][1] == 89  # 0.089000s = 89ms
        assert rows["request_time_ms"][2] == 4  # 0.004200s = 4ms

    def test_skips_checksum_files(self):
        results = [
            {
                "@timestamp": "2026-06-20 13:47:49.000",
                "message": '10.131.32.14 [20/Jun/2026:13:47:49 +0000] "GET /api/pulp-content/balor-stage/maven-releases/org/springframework/cloud/spring-cloud-config-server/4.3.0-redhat-1/spring-cloud-config-server-4.3.0-redhat-1-provenance.json.md5 HTTP/1.1" 302 727 "-" "curl/8.15.0" cache:"MISS" artifact_size:"32" rh_org_id:"5894300" x_forwarded_for:"66.187.232.140"',
            },
        ]
        table = convert_content_to_arrow_table(results, "maven")
        assert table.num_rows == 0

    def test_skips_metadata_xml(self):
        results = [
            {
                "@timestamp": "2026-06-20 13:47:49.000",
                "message": '10.131.32.14 [20/Jun/2026:13:47:49 +0000] "GET /api/pulp-content/balor-stage/maven-releases/org/springframework/cloud/spring-cloud-config-server/maven-metadata.xml HTTP/1.1" 200 500 "-" "curl/8.15.0" cache:"HIT" artifact_size:"500" rh_org_id:"5894300" x_forwarded_for:"66.187.232.140"',
            },
        ]
        table = convert_content_to_arrow_table(results, "maven")
        assert table.num_rows == 0


class TestEmptyContentResults:
    def test_empty_results_python(self):
        table = convert_content_to_arrow_table([], "python")
        assert table.num_rows == 0
        assert table.schema == PYTHON_SCHEMA

    def test_empty_results_rpm(self):
        table = convert_content_to_arrow_table([], "rpm")
        assert table.num_rows == 0
        assert table.schema == RPM_SCHEMA

    def test_empty_results_maven(self):
        table = convert_content_to_arrow_table([], "maven")
        assert table.num_rows == 0
        assert table.schema == MAVEN_SCHEMA


class TestContentBackwardCompatibility:
    def test_old_format_without_request_time(self):
        """Old-format log lines (before request_time was added) produce request_time_ms=None."""
        results = [
            {
                "@timestamp": "2026-06-09 14:30:00.000",
                "message": '10.128.4.123 [09/Jun/2026:14:30:00 +0000] "GET /api/pulp-content/public-rhai/rhoai/3.5-EA2/cpu-ubi9/requests-2.34.2-2-py3-none-any.whl HTTP/1.1" 200 19456789 "-" "pip/24.0" cache:"HIT" artifact_size:"19456789" rh_org_id:"123456" x_forwarded_for:"23.48.249.160,10.0.0.1"',
            },
        ]
        table = convert_content_to_arrow_table(results, "python")
        assert table.num_rows == 1
        rows = table.to_pydict()
        assert rows["request_time_ms"][0] is None


class TestContentQueryBuilding:
    def test_query_contains_content_path_filter(self):
        query = build_content_query("python")
        assert "pulp-content" in query
        assert "livez" in query
        assert "status" in query

    def test_query_fetches_timestamp_and_message(self):
        query = build_content_query("python")
        assert "@timestamp" in query
        assert "message" in query

    def test_python_query_filters_by_whl_extensions(self):
        query = build_content_query("python")
        assert '.whl"' in query
        assert '.whl.metadata"' in query
        assert ".rpm" not in query

    def test_rpm_query_filters_by_rpm_extension(self):
        query = build_content_query("rpm")
        assert '.rpm"' in query
        assert ".whl" not in query

    def test_maven_query_filters_by_jar_and_pom_extensions(self):
        query = build_content_query("maven")
        assert '.jar"' in query
        assert '.pom"' in query
        assert ".whl" not in query
        assert ".rpm" not in query

    def test_unknown_content_type_raises(self):
        import pytest

        with pytest.raises(ValueError, match="Unknown content type"):
            build_content_query("unknown")
