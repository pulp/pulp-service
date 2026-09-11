import asyncio
from types import SimpleNamespace

import pytest
from django.test import override_settings

from pulp_service.app.database_router import ContentReplicaRouter, use_primary_database


def _model(model_name):
    return SimpleNamespace(_meta=SimpleNamespace(model_name=model_name))


class TestContentReplicaRouter:
    def test_reads_use_default_without_replica(self):
        with override_settings(DATABASES={"default": {}}):
            assert ContentReplicaRouter().db_for_read(_model("content")) == "default"

    def test_reads_use_replica_when_configured(self):
        with override_settings(DATABASES={"default": {}, "replica": {}}):
            assert ContentReplicaRouter().db_for_read(_model("content")) == "replica"

    def test_app_status_reads_use_default(self):
        with override_settings(DATABASES={"default": {}, "replica": {}}):
            assert ContentReplicaRouter().db_for_read(_model("appstatus")) == "default"

    def test_primary_context_routes_reads_to_default(self):
        router = ContentReplicaRouter()
        with override_settings(DATABASES={"default": {}, "replica": {}}):
            assert router.db_for_read(_model("content")) == "replica"
            with use_primary_database():
                assert router.db_for_read(_model("content")) == "default"
            assert router.db_for_read(_model("content")) == "replica"

    def test_primary_context_resets_after_error(self):
        router = ContentReplicaRouter()
        with override_settings(DATABASES={"default": {}, "replica": {}}):
            with pytest.raises(RuntimeError), use_primary_database():
                assert router.db_for_read(_model("content")) == "default"
                raise RuntimeError
            assert router.db_for_read(_model("content")) == "replica"

    def test_primary_context_is_task_local(self):
        router = ContentReplicaRouter()

        async def route(use_primary):
            if use_primary:
                with use_primary_database():
                    await asyncio.sleep(0)
                    return router.db_for_read(_model("content"))
            await asyncio.sleep(0)
            return router.db_for_read(_model("content"))

        async def run_routes():
            return await asyncio.gather(route(True), route(False))

        with override_settings(DATABASES={"default": {}, "replica": {}}):
            assert asyncio.run(run_routes()) == ["default", "replica"]

    def test_writes_use_default(self):
        with override_settings(DATABASES={"default": {}, "replica": {}}):
            assert ContentReplicaRouter().db_for_write(_model("content")) == "default"

    @pytest.mark.parametrize("database", ["default", "replica", "other"])
    def test_only_default_allows_migrations(self, database):
        assert ContentReplicaRouter().allow_migrate(database, "content") is (database == "default")
