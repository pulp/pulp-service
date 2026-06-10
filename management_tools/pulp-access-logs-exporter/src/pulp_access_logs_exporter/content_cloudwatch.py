import sys
from datetime import datetime

import pyarrow as pa

from pulp_access_logs_exporter.content_parser import (
    matches_content_type,
    parse_content_log_line,
    parse_content_path,
    parse_rpm_filename,
    parse_wheel_filename,
)
from pulp_access_logs_exporter.content_schemas import SCHEMAS


def build_content_query():
    return "\n".join(
        [
            "filter @message like /pulp-content/",
            "| filter @message not like /livez/",
            "| filter @message not like /status\\/$/",
            "| fields @timestamp, message",
        ]
    )


def _parse_cache_hit(cache_value):
    if cache_value == "HIT":
        return True
    if cache_value == "MISS":
        return False
    return None


def _parse_artifact_size(size_value):
    if not size_value or size_value == "-":
        return None
    try:
        return int(size_value)
    except ValueError:
        return None


def _parse_org_id(org_value):
    if not org_value or org_value == "-":
        return None
    return org_value


def _parse_timestamp(timestamp_str):
    if timestamp_str.endswith("Z"):
        timestamp_str = timestamp_str[:-1]
    return datetime.fromisoformat(timestamp_str)


FILENAME_PARSERS = {
    "python": parse_wheel_filename,
    "rpm": parse_rpm_filename,
}


def convert_content_to_arrow_table(results, content_type):
    schema = SCHEMAS[content_type]
    filename_parser = FILENAME_PARSERS[content_type]
    records = []
    skipped_count = 0

    for result in results:
        message = result.get("message", result.get("@message", ""))
        timestamp_str = result.get("@timestamp", "")

        parsed_line = parse_content_log_line(message)
        if parsed_line is None:
            continue

        parsed_path = parse_content_path(parsed_line["path"])
        if parsed_path is None:
            continue

        filename = parsed_path["filename"]
        if not matches_content_type(filename, content_type):
            continue

        parsed_filename = filename_parser(filename)
        if parsed_filename is None:
            print(
                f"WARNING: Skipping malformed filename: {filename}",
                file=sys.stderr,
            )
            skipped_count += 1
            continue

        record = {
            "timestamp": _parse_timestamp(timestamp_str),
            "domain": parsed_path["domain"],
            "distribution": parsed_path["distribution"],
            "artifact_path": parsed_path["artifact_path"],
            "artifact_size": _parse_artifact_size(parsed_line["artifact_size"]),
            "status_code": int(parsed_line["status"]),
            "cache_hit": _parse_cache_hit(parsed_line["cache"]),
            "user_agent": parsed_line["user_agent"],
            "org_id": _parse_org_id(parsed_line["rh_org_id"]),
            "x_forwarded_for": parsed_line["x_forwarded_for"],
        }
        record.update(parsed_filename)
        records.append(record)

    if skipped_count > 0:
        print(
            f"Records skipped (malformed filename): {skipped_count}",
            file=sys.stderr,
        )

    return pa.Table.from_pylist(records, schema=schema)
