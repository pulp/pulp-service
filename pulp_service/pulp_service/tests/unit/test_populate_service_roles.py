"""Unit tests for _populate_service_roles (app/__init__.py).

Regression coverage for the post_migrate ordering bug: service.domain_admin /
service.domain_viewer must end up with EVERY plugin's permissions regardless of
which plugin's post_migrate fires the receiver. The old code only ran on
pulp_service's own post_migrate, so any plugin whose permissions were created
afterwards (e.g. pulp_file) was silently dropped from the role -- domain owners
then got 403 creating file repositories despite holding service.domain_admin.
"""

from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps

from pulp_service.app import _populate_service_roles

pytestmark = pytest.mark.django_db


def _role(name):
    Role = django_apps.get_model("core", "Role")
    return Role.objects.get(name=name)


def test_populates_role_for_any_plugin_sender():
    """Firing on a non-service plugin's post_migrate still populates the role.

    Fails on the old code: the sender.label != "service" guard returns early for
    a "file" sender, leaving the cleared role empty.
    """
    admin = _role("service.domain_admin")
    admin.permissions.clear()

    _populate_service_roles(sender=SimpleNamespace(label="file"), apps=django_apps)

    admin.refresh_from_db()
    assert admin.permissions.filter(codename="add_filerepository", content_type__app_label="file").exists(), (
        "service.domain_admin is missing file.add_filerepository"
    )


def test_viewer_role_gets_only_view_permissions():
    viewer = _role("service.domain_viewer")
    viewer.permissions.clear()

    _populate_service_roles(sender=SimpleNamespace(label="file"), apps=django_apps)

    viewer.refresh_from_db()
    codenames = set(viewer.permissions.values_list("codename", flat=True))
    assert codenames, "viewer role should have permissions"
    assert all(c.startswith("view") for c in codenames), f"non-view perms leaked: {codenames}"


def test_idempotent_no_write_when_unchanged():
    """A repopulate with an unchanged permission set issues no INSERT/DELETE.

    Guards the rolling-upgrade property: permissions.set() does DELETE+INSERT,
    which opens a window where domain owners get 403s. Running on every plugin's
    post_migrate must not churn the m2m table when nothing changed.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _populate_service_roles(sender=SimpleNamespace(label="file"), apps=django_apps)

    with CaptureQueriesContext(connection) as ctx:
        _populate_service_roles(sender=SimpleNamespace(label="file"), apps=django_apps)

    writes = [
        q["sql"]
        for q in ctx.captured_queries
        if "role_permissions" in q["sql"].lower() and ("insert" in q["sql"].lower() or "delete" in q["sql"].lower())
    ]
    assert writes == [], f"expected no m2m writes on unchanged repopulate, got: {writes}"
