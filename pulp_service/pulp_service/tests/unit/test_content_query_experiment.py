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
def experiment_on(settings):
    settings.CONTENT_QUERY_AB_ENABLED = True
    settings.CONTENT_QUERY_AB_PROBABILITY = 0.5


@pytest.fixture
def force_variant(monkeypatch):
    def _force(variant):
        # random() < p_candidate (0.5) selects B, otherwise A.
        monkeypatch.setattr("pulp_service.app.experiments.random.random", lambda: 0.1 if variant == "B" else 0.9)

    return _force


LOOKUPS = [
    pytest.param({"relative_path__startswith": "org/"}, None, False, id="directory-listing"),
    pytest.param({"relative_path__startswith": "org/a/"}, None, False, id="specific-folder"),
    pytest.param({"relative_path__startswith": "nothing/"}, None, False, id="missing-folder"),
    pytest.param({"relative_path": "org/b/index.html"}, 1, False, id="index-probe-hit"),
    pytest.param({"relative_path": "org/a/index.html"}, 1, False, id="index-probe-miss"),
    pytest.param({"relative_path": "org/a/a-1.0.jar"}, 2, False, id="file-lookup-hit"),
    pytest.param({"relative_path": "org/b/b-2.0.jar"}, 2, False, id="file-lookup-not-in-version"),
]


@pytest.mark.parametrize(("filters", "limit", "skip"), LOOKUPS)
def test_variants_return_same_rows(version, experiment_on, force_variant, filters, limit, skip):  # noqa: ARG001
    """Variant A (stock) and variant B (EXISTS) must return identical content artifacts."""
    results = {}
    for variant in ("A", "B"):
        force_variant(variant)
        rows, _ = content_query_experiment(
            "test", version, ContentArtifact.objects.select_related("artifact"), filters=filters, limit=limit
        )
        results[variant] = sorted(row.relative_path for row in rows)
    assert results["A"] == results["B"]


def test_skip_candidate_returns_no_rows_without_querying(version, experiment_on, force_variant):  # noqa: ARG001
    force_variant("B")
    with CaptureQueriesContext(connection) as ctx:
        rows, _ = content_query_experiment(
            "index_probe",
            version,
            ContentArtifact.objects.all(),
            filters={"relative_path": "org/a/a-1.0.jarindex.html"},
            limit=1,
            skip_candidate=True,
        )
    assert rows == []
    assert not ctx.captured_queries


def test_disabled_runs_stock_query_only_and_logs_nothing(version, settings, caplog, force_variant):
    settings.CONTENT_QUERY_AB_ENABLED = False
    force_variant("B")
    with CaptureQueriesContext(connection) as ctx:
        rows, _ = content_query_experiment(
            "test", version, ContentArtifact.objects.all(), filters={"relative_path__startswith": "org/a/"}
        )
    assert sorted(row.relative_path for row in rows) == ["org/a/a-1.0.jar", "org/a/a-1.0.pom"]
    assert not [record for record in caplog.records if record.name == "pulp.experiment"]
    assert not any("pexp=" in query["sql"] for query in ctx.captured_queries)
    assert any("content_ids" in query["sql"] for query in ctx.captured_queries)


def test_enabled_logs_variant_query_and_repository(version, experiment_on, caplog, force_variant):  # noqa: ARG001
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


def test_sql_carries_variant_and_repository_markers(version, experiment_on, force_variant):  # noqa: ARG001
    for variant in ("A", "B"):
        force_variant(variant)
        with CaptureQueriesContext(connection) as ctx:
            content_query_experiment(
                "test", version, ContentArtifact.objects.all(), filters={"relative_path__startswith": "org/"}
            )
        marker = f"/* pexp={CONTENT_QUERY_EXPERIMENT} v={variant} */ /* repo={version.repository_id} */"
        assert any(marker in query["sql"] for query in ctx.captured_queries), marker


def test_single_or_raise_matches_get_semantics(version):  # noqa: ARG001
    rows = list(ContentArtifact.objects.filter(relative_path="top.txt"))
    assert single_or_raise(ContentArtifact, rows) == rows[0]
    with pytest.raises(ContentArtifact.DoesNotExist):
        single_or_raise(ContentArtifact, [])
    with pytest.raises(ContentArtifact.MultipleObjectsReturned):
        single_or_raise(ContentArtifact, list(ContentArtifact.objects.all()[:2]))
