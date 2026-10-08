import logging

from django.conf import settings
from django.db import migrations

_logger = logging.getLogger(__name__)


def backfill_domain_creator_role(apps, schema_editor):  # noqa: ARG001
    """Grant ``core.domain_creator`` to users that predate the assign_domain_creator_role signal.

    The ``assign_domain_creator_role`` post_save signal only fires on ``created=True``, so
    users that existed before this deploy -- or were created by an old-version pod during a
    rolling deploy -- never receive the role and stay unauthorized for generic domain create.
    This one-time backfill writes the same model-level ``UserRole`` row the signal's
    ``assign_role("core.domain_creator", user)`` produces (content_type/object_id/domain all
    None). The signal grants to every new user, so this matches it and grants to all existing
    users. See PULP-2526.
    """
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Role = apps.get_model("core", "Role")
    UserRole = apps.get_model("core", "UserRole")

    # Locked role registered from pulpcore's LOCKED_ROLES. get_or_create keeps the FK safe on
    # a fresh/edge DB where post_migrate has not populated it yet (same pattern as 0019).
    creator_role, _ = Role.objects.get_or_create(name="core.domain_creator")

    granted = 0
    for user in User.objects.iterator(chunk_size=500):
        # assign_role is not idempotent (raw .create); get_or_create makes this backfill safe
        # to re-run and to coexist with rows the signal already wrote for newer users.
        _, created = UserRole.objects.get_or_create(
            user=user,
            role=creator_role,
            content_type=None,
            object_id=None,
            domain=None,
        )
        granted += int(created)

    _logger.info("domain_creator backfill: granted=%s", granted)


class Migration(migrations.Migration):
    dependencies = [
        ("service", "0022_backfill_org_group_roles"),
    ]

    operations = [
        migrations.RunPython(backfill_domain_creator_role, migrations.RunPython.noop, elidable=True),
    ]
