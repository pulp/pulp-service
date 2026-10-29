"""Database-backed equivalence checks for the hosted interval content query experiment."""

import json
from itertools import compress
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from pulpcore.app.models import Content, Repository

EXPERIMENT_ID = "PULP-1996-CONTENT-MEMBERSHIP"
MARKER_A = f"/* pexp={EXPERIMENT_ID} v=A */"
MARKER_B = f"/* pexp={EXPERIMENT_ID} v=B */"


@pytest.fixture
def repository(db):  # noqa: ARG001
    repository = Repository.objects.create(name=uuid4())
    repository.CONTENT_TYPES = [Content]
    return repository


@pytest.fixture
def content_pks(db):  # noqa: ARG001
    contents = [Content(pulp_type="core.content") for _ in range(5)]
    Content.objects.bulk_create(contents)
    return sorted([content.pk for content in contents])


@pytest.fixture
def add_content(content_pks):
    def _add_content(version, mask):
        version.add_content(Content.objects.filter(pk__in=compress(content_pks, mask)))

    return _add_content


@pytest.fixture
def remove_content(content_pks):
    def _remove_content(version, mask):
        version.remove_content(Content.objects.filter(pk__in=compress(content_pks, mask)))

    return _remove_content


def test_content_query_variants_select_same_content(repository, content_pks, add_content, remove_content):
    """
    Both content query experiment variants must select identical content UUIDs.

    Prerequisite for running the A/B experiment: variant A (unnest of
    content_ids) and variant B (core_repositorycontent interval) agree with an
    independently tracked expected set across versions with adds and removes.
    """
    c0, c1, c2, c3, c4 = content_pks
    expected = set()

    versions = [repository.latest_version()]
    expected_by_version = [set(expected)]

    with repository.new_version() as version:
        add_content(version, (1, 1, 0, 1, 0))
    expected |= {c0, c1, c3}
    versions.append(version)
    expected_by_version.append(set(expected))

    with repository.new_version() as version:
        remove_content(version, (0, 1, 0, 0, 0))
    expected -= {c1}
    versions.append(version)
    expected_by_version.append(set(expected))

    with repository.new_version() as version:
        add_content(version, (0, 0, 1, 0, 1))
    expected |= {c2, c4}
    versions.append(version)
    expected_by_version.append(set(expected))

    for version, expected_ids in zip(versions, expected_by_version, strict=True):
        variant_a = set(version.content_ids_subquery())
        variant_b = set(version.content_pks_subquery())
        assert variant_a == expected_ids
        assert variant_b == expected_ids


def test_content_query_variants_do_not_cross_repository(repository, content_pks, add_content):
    """The interval query must only return membership for this repository."""
    other_repository = Repository.objects.create(name=uuid4())
    other_repository.CONTENT_TYPES = [Content]

    with repository.new_version() as version:
        add_content(version, (1, 0, 0, 0, 0))

    with other_repository.new_version() as other_version:
        other_version.add_content(Content.objects.filter(pk__in=content_pks[1:]))

    assert set(version.content_ids_subquery()) == set(version.content_pks_subquery())
    assert set(other_version.content_ids_subquery()) == set(other_version.content_pks_subquery())


def test_content_query_variants_for_empty_version(repository):
    """Both content query variants return no content for an empty repository version."""
    version = repository.latest_version()

    assert not set(version.content_ids_subquery())
    assert not set(version.content_pks_subquery())


def test_get_content_experiment_dispatch(repository, content_pks, add_content, monkeypatch, caplog):
    """get_content() routes through the A/B dispatcher, logs the dispatch, and both variants agree."""
    with repository.new_version() as version:
        add_content(version, (1, 0, 1, 0, 1))

    expected = {content_pks[0], content_pks[2], content_pks[4]}

    with caplog.at_level("INFO", logger="pulp.experiment"):
        # random() < p_candidate (0.5) selects B, otherwise A.
        monkeypatch.setattr("pulp_service.app.experiments.random.random", lambda: 0.1)
        assert {content.pk for content in version.get_content()} == expected
        monkeypatch.setattr("pulp_service.app.experiments.random.random", lambda: 0.9)
        assert {content.pk for content in version.get_content()} == expected

    dispatches = [json.loads(record.getMessage()) for record in caplog.records if record.name == "pulp.experiment"]
    assert [dispatch["variant"] for dispatch in dispatches] == ["B", "A"]
    assert all(
        dispatch["event"] == "ab_experiment_dispatch" and dispatch["exp_id"] == EXPERIMENT_ID for dispatch in dispatches
    )
    assert all(dispatch.get("correlation_id") for dispatch in dispatches)


def test_get_content_marks_sql_per_variant(repository, content_pks, add_content, monkeypatch):  # noqa: ARG001
    """Both variants must reach the database with their RDS Performance Insights marker."""
    with repository.new_version() as version:
        add_content(version, (1, 1, 1, 1, 1))

    for random_value, marker in ((0.1, MARKER_B), (0.9, MARKER_A)):
        monkeypatch.setattr("pulp_service.app.experiments.random.random", lambda rv=random_value: rv)
        with CaptureQueriesContext(connection) as ctx:
            assert list(version.get_content())
        assert any(marker in query["sql"] for query in ctx.captured_queries), marker
