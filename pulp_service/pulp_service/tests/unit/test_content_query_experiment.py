"""Database-backed checks for the PULP-2346 content query experiment."""

import json
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from pulpcore.app.models import Content, ContentArtifact, Repository

from pulp_service.app.experiments import CONTENT_QUERY_EXPERIMENT, content_query_experiment, single_or_raise

MEMBER_PATHS = ["org/a/a-1.0.jar", "org/a/a-1.0.pom", "org/b/index.html", "top.txt"]
OTHER_PATHS = ["org/b/b-2.0.jar", "org/a/not-a-member.jar"]


@pytest.fixture
def version(db):  # noqa: ARG001
    """A repository version holding MEMBER_PATHS, with OTHER_PATHS existing outside of it."""
    repository = Repository.objects.create(name=uuid4())
    repository.CONTENT_TYPES = [Content]
    members = []
    for path in [*MEMBER_PATHS, *OTHER_PATHS]:
        content = Content.objects.create(pulp_type="core.content")
        ContentArtifact.objects.create(content=content, relative_path=path)
        if path in MEMBER_PATHS:
            members.append(content.pk)
    with repository.new_version() as new_version:
        new_version.add_content(Content.objects.filter(pk__in=members))
    return new_version


@pytest.fixture
def force_variant(monkeypatch):
    def _force(variant):
        # random() < p_candidate (0.5) selects B, otherwise A.
        monkeypatch.setattr("pulp_service.app.experiments.random.random", lambda: 0.1 if variant == "B" else 0.9)

    return _force


LOOKUPS = [
    pytest.param(
        {"relative_path__startswith": "org/"},
        None,
        ["org/a/a-1.0.jar", "org/a/a-1.0.pom", "org/b/index.html"],
        id="directory-listing",
    ),
    pytest.param(
        {"relative_path__startswith": "org/a/"}, None, ["org/a/a-1.0.jar", "org/a/a-1.0.pom"], id="specific-folder"
    ),
    pytest.param({"relative_path__startswith": "nothing/"}, None, [], id="missing-folder"),
    pytest.param({"relative_path": "org/b/index.html"}, 1, ["org/b/index.html"], id="index-probe-hit"),
    pytest.param({"relative_path": "org/a/index.html"}, 1, [], id="index-probe-miss"),
    pytest.param({"relative_path": "org/a/a-1.0.jar"}, 2, ["org/a/a-1.0.jar"], id="file-lookup-hit"),
    # Exists as a ContentArtifact, but its content is not in the version.
    pytest.param({"relative_path": "org/b/b-2.0.jar"}, 2, [], id="file-lookup-not-in-version"),
]


@pytest.mark.parametrize(("filters", "limit", "expected"), LOOKUPS)
def test_variants_return_expected_rows(version, force_variant, filters, limit, expected):
    """Both variants return exactly the version's matching artifacts, not just each other's rows."""
    results = {}
    for variant in ("A", "B"):
        force_variant(variant)
        rows, _ = content_query_experiment(
            "test", version, ContentArtifact.objects.select_related("artifact"), filters=filters, limit=limit
        )
        results[variant] = sorted(row.relative_path for row in rows)
    assert results["A"] == results["B"] == expected


def test_variants_use_their_own_query_shape(version, force_variant):
    """A is the stock unnest(content_ids) query; B is the correlated EXISTS and never unnests."""
    sql = {}
    for variant in ("A", "B"):
        force_variant(variant)
        with CaptureQueriesContext(connection) as ctx:
            content_query_experiment(
                "test", version, ContentArtifact.objects.all(), filters={"relative_path": "org/a/a-1.0.jar"}, limit=2
            )
        sql[variant] = " ".join(query["sql"] for query in ctx.captured_queries)
    assert "unnest" in sql["A"] and "EXISTS" not in sql["A"]
    assert "EXISTS" in sql["B"] and "unnest" not in sql["B"]


def test_candidate_index_probe_executes_membership_query(version, force_variant):
    force_variant("B")
    with CaptureQueriesContext(connection) as ctx:
        rows, _ = content_query_experiment(
            "index_probe",
            version,
            ContentArtifact.objects.all(),
            filters={"relative_path": "org/b/index.html"},
            limit=1,
        )
    assert [row.relative_path for row in rows] == ["org/b/index.html"]
    assert any("EXISTS" in query["sql"] for query in ctx.captured_queries)


def test_logs_variant_query_and_repository(version, caplog, force_variant):
    caplog.set_level("INFO", logger="pulp.experiment")
    for variant in ("B", "A"):
        force_variant(variant)
        content_query_experiment(
            "directory_listing", version, ContentArtifact.objects.all(), filters={"relative_path__startswith": "org/"}
        )
    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == "pulp.experiment"]
    assert [record["variant"] for record in records] == ["B", "A"]
    for record in records:
        assert record["event"] == "ab_experiment"
        assert record["exp_id"] == CONTENT_QUERY_EXPERIMENT
        assert record["query"] == "directory_listing"
        assert record["repository_id"] == str(version.repository_id)
        assert record["outcome"] == "success"
        assert record["result_count"] == 3
        assert record["duration_ms"] >= 0


def test_sql_carries_variant_and_repository_markers(version, force_variant):
    for variant in ("A", "B"):
        force_variant(variant)
        with CaptureQueriesContext(connection) as ctx:
            content_query_experiment(
                "test", version, ContentArtifact.objects.all(), filters={"relative_path__startswith": "org/"}
            )
        marker = f"/* pexp={CONTENT_QUERY_EXPERIMENT} v={variant} */ /* repo={version.repository_id} */"
        assert any(marker in query["sql"] for query in ctx.captured_queries), marker
        # Once per statement: the collector adds a digest's load once for every marker it finds.
        assert all(query["sql"].count("pexp=") == 1 for query in ctx.captured_queries)


def test_lazy_queryset_keeps_variant_marker(version, force_variant):
    """A later subquery evaluation must retain the PULP-2346 marker."""
    for variant in ("A", "B"):
        force_variant(variant)
        _, queryset = content_query_experiment(
            "directory_listing",
            version,
            ContentArtifact.objects.all(),
            filters={"relative_path__startswith": "org/"},
        )
        marker = f"/* pexp={CONTENT_QUERY_EXPERIMENT} v={variant} */ /* repo={version.repository_id} */"
        with CaptureQueriesContext(connection) as ctx:
            list(queryset.values("content_id"))
        assert any(marker in query["sql"] for query in ctx.captured_queries), marker


def test_single_or_raise_matches_get_semantics(version):  # noqa: ARG001
    rows = list(ContentArtifact.objects.filter(relative_path="top.txt"))
    assert single_or_raise(ContentArtifact, rows) == rows[0]
    with pytest.raises(ContentArtifact.DoesNotExist):
        single_or_raise(ContentArtifact, [])
    with pytest.raises(ContentArtifact.MultipleObjectsReturned):
        single_or_raise(ContentArtifact, list(ContentArtifact.objects.all()[:2]))
