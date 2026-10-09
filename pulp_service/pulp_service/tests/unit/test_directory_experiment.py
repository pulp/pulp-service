"""Database-backed equivalence checks for the hosted directory experiment."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from asgiref.sync import async_to_sync
from django.core.files.base import ContentFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django_guid import clear_guid, set_guid

from pulpcore.app.models import (
    Artifact,
    Content,
    ContentArtifact,
    Domain,
    Publication,
    PublishedArtifact,
    Remote,
    RemoteArtifact,
    Repository,
    RepositoryContent,
    RepositoryVersion,
)
from pulpcore.content.handler import Handler

from pulp_service.app import experiments


@pytest.fixture
def experiment_settings(settings):
    settings.CONTENT_DIRECTORY_AB_ENABLED = True
    settings.CONTENT_DIRECTORY_AB_PROBABILITY = 1.0
    settings.CONTENT_DIRECTORY_AB_REVISION = "test-revision"
    return settings


def make_version(repository, number, content):
    version = RepositoryVersion.objects.create(repository=repository, number=number, complete=True)
    RepositoryVersion.objects.filter(pk=version.pk).update(content_ids=[item.pk for item in content])
    return version


def make_artifact():
    return Artifact.objects.create(
        size=0,
        file=ContentFile(b"", name="empty"),
        **{algorithm: hashlib.new(algorithm, b"").hexdigest() for algorithm in Artifact.DIGEST_FIELDS},
    )


@pytest.fixture
def history(db):
    repository = Repository.objects.create(name="directory-experiment")
    units = Content.objects.bulk_create([Content() for _ in range(5)])
    artifact = make_artifact()
    paths = ["leaf/first", "leaf/second", "outside/file", "leaf/third", "leaf/extra"]
    cas = [
        ContentArtifact.objects.create(content=unit, relative_path=path, artifact=artifact if i == 0 else None)
        for i, (unit, path) in enumerate(zip(units, paths, strict=True))
    ]
    remote = Remote.objects.create(name="directory-experiment")
    RemoteArtifact.objects.create(remote=remote, content_artifact=cas[1], url="https://example.com/second", size=19)
    RemoteArtifact.objects.create(remote=remote, content_artifact=cas[3], url="https://example.com/third", size=None)
    v1 = make_version(repository, 1, units[:3])
    v2 = make_version(repository, 2, units[1:4])
    v3 = make_version(repository, 3, units[:4])
    memberships = [
        RepositoryContent.objects.create(repository=repository, content=unit, version_added=v1) for unit in units[:3]
    ]
    RepositoryContent.objects.filter(pk=memberships[0].pk).update(version_removed=v2)
    memberships.extend(
        [
            RepositoryContent.objects.create(repository=repository, content=units[3], version_added=v2),
            RepositoryContent.objects.create(repository=repository, content=units[0], version_added=v3),
        ]
    )
    dates = [datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(5)]
    for row, date in zip(memberships, dates, strict=True):
        RepositoryContent.objects.filter(pk=row.pk).update(pulp_created=date)
    other_domain = Domain.objects.create(name="directory-other", storage_class="pulpcore.app.models.storage.FileSystem")
    outsider = Content.objects.create(pulp_domain=other_domain)
    ContentArtifact.objects.create(content=outsider, relative_path="leaf/first")
    other_repo = Repository.objects.create(name="directory-other", pulp_domain=other_domain)
    other_version = make_version(other_repo, 1, [outsider])
    RepositoryContent.objects.create(repository=other_repo, content=outsider, version_added=other_version)
    return SimpleNamespace(versions=[v1, v2, v3], units=units, cas=cas, dates=dates)


@pytest.mark.parametrize("mode", ["repository", "publication", "pass_through"])
@pytest.mark.parametrize("version_index", [0, 1, 2])
@pytest.mark.parametrize("path", ["", "leaf/", "missing/", "renamed/"])
def test_full_listing_equivalence(history, experiment_settings, caplog, mode, version_index, path):
    version = history.versions[version_index]
    publication = None
    if mode != "repository":
        publication = Publication.objects.create(
            repository_version=version, pass_through=mode == "pass_through", complete=True
        )
        for ca in history.cas:
            PublishedArtifact.objects.create(
                publication=publication, content_artifact=ca, relative_path=ca.relative_path
            )
        PublishedArtifact.objects.create(
            publication=publication, content_artifact=history.cas[0], relative_path="renamed/first"
        )
    args = (version if publication is None else None, publication, path)
    handler = Handler()
    experiment_settings.CONTENT_DIRECTORY_AB_ENABLED = False
    baseline = async_to_sync(handler.list_directory)(*args)
    experiment_settings.CONTENT_DIRECTORY_AB_ENABLED = True
    with caplog.at_level("INFO", logger="pulp.experiment"):
        for probability in (0.0, 1.0):
            experiment_settings.CONTENT_DIRECTORY_AB_PROBABILITY = probability
            assert async_to_sync(handler.list_directory)(*args) == baseline
    entries, dates, sizes = baseline
    if path == "missing/":
        assert (entries, dates, sizes) == (set(), {}, {})
    if mode == "repository" and path == "leaf/":
        assert "extra" not in entries
        assert sizes["second"] == 19
        if version_index == 1:
            assert "first" not in entries
        else:
            assert sizes["first"] == 0
            assert dates["first"] == history.dates[0 if version_index == 0 else 4]
    records = [json.loads(record.message) for record in caplog.records if record.name == "pulp.experiment"]
    if path == "leaf/":
        assert {record.get("variant") for record in records} == {"A", "B"}
    elif path == "":
        assert all(record["reason"] == "shared_name" for record in records)


def test_multiple_artifacts_keep_existing_name_mapping(history, experiment_settings):
    ContentArtifact.objects.create(content=history.units[0], relative_path="leaf/another")
    handler = Handler()
    args = (history.versions[0], None, "leaf/")
    experiment_settings.CONTENT_DIRECTORY_AB_PROBABILITY = 0
    control = async_to_sync(handler.list_directory)(*args)
    experiment_settings.CONTENT_DIRECTORY_AB_PROBABILITY = 1
    candidate = async_to_sync(handler.list_directory)(*args)
    assert candidate == control
    assert {"first", "another", "second"} == candidate[0]


def test_json_listing_bypasses_experiment(history, experiment_settings, monkeypatch):
    dispatch = Mock(side_effect=AssertionError("JSON listings must not dispatch"))
    monkeypatch.setattr(experiments, "run_experiment", dispatch)
    entries, total = async_to_sync(Handler().list_directory_flat)(history.versions[0], None, "leaf/", 10, 0)
    assert total == 2
    assert [entry["path"] for entry in entries] == ["first", "second"]
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_more_than_parameter_limit(experiment_settings):
    count = 65536
    repository = Repository.objects.create(name="directory-large")
    units = Content.objects.bulk_create([Content() for _ in range(count)], batch_size=1000)
    artifact = make_artifact()
    ContentArtifact.objects.bulk_create(
        [ContentArtifact(content=unit, artifact=artifact, relative_path=f"leaf/{i}") for i, unit in enumerate(units)],
        batch_size=1000,
    )
    version = make_version(repository, 1, units)
    RepositoryContent.objects.bulk_create(
        [RepositoryContent(repository=repository, content=unit, version_added=version) for unit in units],
        batch_size=1000,
    )
    names = {unit.pk: {str(i)} for i, unit in enumerate(units)}
    sources = [(ContentArtifact.objects.filter(content__in=version.content), "content_id")]
    options = {"serving_mode": "repository", "directory_count": count, "is_root": False}
    experiment_settings.CONTENT_DIRECTORY_AB_PROBABILITY = 0
    control = experiments.directory_membership_dates(version, names, sources, **options)
    experiment_settings.CONTENT_DIRECTORY_AB_PROBABILITY = 1
    with CaptureQueriesContext(connection) as queries:
        candidate = experiments.directory_membership_dates(version, names, sources, **options)
    assert candidate == control
    assert len(candidate) == count
    assert len(queries) == 1
    assert len(queries[0]["sql"]) < 5000
    assert "pexp=PULP-2505 v=B" in queries[0]["sql"]


def test_candidate_selects_only_listed_columns_and_preserves_routing(history, experiment_settings, monkeypatch):
    version = history.versions[0]
    names = {history.units[0].pk: {"first"}}
    sources = [(ContentArtifact.objects.filter(relative_path="leaf/first"), "content_id")]
    real_memberships = version._content_relationships()
    routed_memberships = Mock(db="replica")
    routed_memberships.using.return_value = real_memberships
    monkeypatch.setattr(version, "_content_relationships", lambda: routed_memberships)
    connections = {"replica": connection}
    monkeypatch.setattr(experiments, "connections", connections)
    with CaptureQueriesContext(connection) as queries:
        result = experiments.directory_membership_dates(
            version, names, sources, serving_mode="repository", directory_count=1, is_root=False
        )
    routed_memberships.using.assert_called_once_with("replica")
    assert result == {history.units[0].pk: history.dates[0]}
    assert len(queries) == 1
    select = queries[0]["sql"].split(" FROM ")[0]
    assert '"content_id"' in select and '"pulp_created"' in select
    assert '"pulp_last_updated"' not in select
    with CaptureQueriesContext(connection) as queries:
        list(real_memberships.values_list("content_id", flat=True))
    assert "pexp=" not in queries[0]["sql"]


@pytest.mark.parametrize("variant,probability", [("A", 0.0), ("B", 1.0)])
def test_one_variant_and_timing(monkeypatch, caplog, variant, probability):
    calls = []
    clock = iter([2.0, 2.125])
    monkeypatch.setattr(experiments, "perf_counter", lambda: next(clock))

    def control():
        calls.append("A")
        return {"file": "date"}

    def candidate():
        calls.append("B")
        return {"file": "date"}

    set_guid("directory-test-request")
    try:
        with caplog.at_level("INFO", logger="pulp.experiment"):
            result = experiments.run_experiment(
                "PULP-2505", control, candidate, p_candidate=probability, context={"deployment_revision": "test"}
            )
    finally:
        clear_guid()
    assert result == {"file": "date"}
    assert calls == [variant]
    record = json.loads(caplog.records[-1].message)
    assert record["variant"] == variant
    assert record["duration_ms"] == 125
    assert record["result_count"] == 1
    assert record["correlation_id"] == "directory-test-request"
    assert record["outcome"] == "success"


def test_one_content_can_have_multiple_displayed_names(history, experiment_settings, caplog):
    content_id = history.units[0].pk
    names = {content_id: {"first", "renamed/first"}}
    sources = [(ContentArtifact.objects.filter(content_id=content_id), "content_id")]
    experiment_settings.CONTENT_DIRECTORY_AB_PROBABILITY = 1
    with caplog.at_level("INFO", logger="pulp.experiment"):
        dates = experiments.directory_membership_dates(
            history.versions[0],
            names,
            sources,
            serving_mode="publication",
            directory_count=2,
            is_root=True,
        )
    assert dates == {content_id: history.dates[0]}
    record = json.loads(caplog.records[-1].message)
    assert record["event"] == "ab_experiment"
    assert record["variant"] == "B"


def test_errors_are_logged_without_fallback(caplog, monkeypatch):
    control = Mock()
    failure = RuntimeError("do not log private exception messages")
    candidate = Mock(side_effect=failure)
    clock = iter([3.0, 3.25])
    monkeypatch.setattr(experiments, "perf_counter", lambda: next(clock))
    with caplog.at_level("INFO", logger="pulp.experiment"), pytest.raises(RuntimeError) as caught:
        experiments.run_experiment("PULP-2505", control, candidate, p_candidate=1.0, context={})
    assert caught.value is failure
    control.assert_not_called()
    candidate.assert_called_once()
    record = json.loads(caplog.records[-1].message)
    assert record["outcome"] == "error"
    assert record["error_type"] == "RuntimeError"
    assert record["duration_ms"] == 250
    assert "private" not in caplog.text
    assert "result_count" not in record


@pytest.mark.parametrize(
    "enabled,names,probability,revision,reason",
    [
        (False, {1: {"file"}}, 1, "test", None),
        (True, {}, 1, "test", "empty"),
        (True, {1: {"folder/"}, 2: {"folder/"}}, 1, "test", "shared_name"),
        (True, {1: {"file"}}, -1, "test", "invalid_probability"),
        (True, {1: {"file"}}, 2, "test", "invalid_probability"),
        (True, {1: {"file"}}, float("nan"), "test", "invalid_probability"),
        (True, {1: {"file"}}, float("inf"), "test", "invalid_probability"),
        (True, {1: {"file"}}, "0.5", "test", "invalid_probability"),
        (True, {1: {"file"}}, True, "test", "invalid_probability"),
        (True, {1: {"file"}}, 1, "", "missing_revision"),
    ],
)
def test_ineligible_calls_do_not_dispatch(settings, monkeypatch, caplog, enabled, names, probability, revision, reason):
    settings.CONTENT_DIRECTORY_AB_ENABLED = enabled
    settings.CONTENT_DIRECTORY_AB_PROBABILITY = probability
    settings.CONTENT_DIRECTORY_AB_REVISION = revision
    rows = [SimpleNamespace(content_id=pk, pulp_created=pk) for pk in names]
    version = SimpleNamespace(_content_relationships=lambda: rows)
    dispatch = Mock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(experiments, "run_experiment", dispatch)
    with caplog.at_level("INFO", logger="pulp.experiment"):
        result = experiments.directory_membership_dates(
            version, names, [], serving_mode="repository", directory_count=len(names), is_root=False
        )
    assert result == {pk: pk for pk in names}
    dispatch.assert_not_called()
    if reason is None:
        assert not caplog.records
    else:
        record = json.loads(caplog.records[-1].message)
        assert record["event"] == "ab_experiment_skipped"
        assert record["reason"] == reason
