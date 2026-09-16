"""Database routing for the content service."""

from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings

_use_primary = ContextVar("use_primary", default=False)


@contextmanager
def use_primary_database():
    """Route reads in the current context to the primary database."""
    token = _use_primary.set(True)
    try:
        yield
    finally:
        _use_primary.reset(token)


class ContentReplicaRouter:
    """Route content-service reads to the replica while keeping writes on primary."""

    def db_for_read(self, model, **hints):
        if "replica" not in settings.DATABASES or _use_primary.get() or model._meta.model_name == "appstatus":
            return "default"
        return "replica"

    def db_for_write(self, model, **hints):
        return "default"

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == "default"
