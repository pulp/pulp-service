import pyarrow as pa

COMMON_FIELDS = [
    ("timestamp", pa.timestamp("ns")),
    ("domain", pa.string()),
    ("distribution", pa.string()),
    ("package_name", pa.string()),
    ("package_version", pa.string()),
    ("architecture", pa.string()),
    ("artifact_path", pa.string()),
    ("artifact_size", pa.int64()),
    ("status_code", pa.int16()),
    ("cache_hit", pa.bool_()),
    ("user_agent", pa.string()),
    ("org_id", pa.string()),
    ("x_forwarded_for", pa.string()),
]

PYTHON_SCHEMA = pa.schema(
    COMMON_FIELDS
    + [
        ("build_tag", pa.string()),
        ("pyver", pa.string()),
        ("abi", pa.string()),
    ]
)

RPM_SCHEMA = pa.schema(
    COMMON_FIELDS
    + [
        ("epoch", pa.int32()),
        ("release", pa.string()),
    ]
)

MAVEN_SCHEMA = pa.schema(
    COMMON_FIELDS
    + [
        ("group_id", pa.string()),
        ("classifier", pa.string()),
        ("packaging", pa.string()),
    ]
)

SCHEMAS = {
    "maven": MAVEN_SCHEMA,
    "python": PYTHON_SCHEMA,
    "rpm": RPM_SCHEMA,
}
