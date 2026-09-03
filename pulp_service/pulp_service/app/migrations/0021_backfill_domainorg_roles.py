from importlib import import_module

from django.db import migrations

# Reuse migration 0019's conversion. Migration module names start with a digit, so a
# normal `from .0019_... import` will not parse -- load it by dotted string instead.
# 0019 is a frozen, already-applied migration: importing its function guarantees this
# backfill applies the same DomainOrg role-assignment rules (PULP-1893), which is the
# ticket's acceptance criterion.
#
# The conversion is idempotent (get_or_create throughout). This re-runs it over every
# DomainOrg row; writes are limited to the gap-window DomainOrgs -- those created after
# 0019 ran but before the PULP-2119 dual-write deployed -- because get_or_create is a
# no-op for rows that already have their roles.
_migration_0019 = import_module("pulp_service.app.migrations.0019_convert_domainorg_to_roles")


def backfill_domainorg_roles(apps, schema_editor):
    # set_permissions=False: the backfill must only fill missing DomainOrg role
    # assignments. permissions.set(...) replaces the service roles' permission
    # membership, so running it here would overwrite any service-role permission
    # changes made since 0019. The post_migrate handler keeps those permissions
    # authoritative; this backfill leaves them untouched.
    #
    # include_readonly_policies=False: 0019's readonly-policy loop reads the CURRENT
    # DOMAIN_ACCESS_POLICIES, not DomainOrg rows. Re-running it here would grant readonly
    # roles for any policy added or changed since 0019, to domains unrelated to the
    # gap-window DomainOrgs this backfill targets. Skip it to stay within scope.
    _migration_0019.convert_domainorgs_to_roles(
        apps, schema_editor, set_permissions=False, include_readonly_policies=False
    )


class Migration(migrations.Migration):
    dependencies = [
        ("service", "0020_contentview"),
    ]

    operations = [
        migrations.RunPython(
            backfill_domainorg_roles,
            migrations.RunPython.noop,
        ),
    ]
