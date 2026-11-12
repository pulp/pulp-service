from django.db import migrations

_INDEX_NAME = "core_ca_relpath_pattern_idx"
_INDEX_MARKER = "pulp-service:0024:core_ca_relpath_pattern_idx"
_DROP_INDEX_SQL = f'DROP INDEX CONCURRENTLY IF EXISTS "{_INDEX_NAME}"'


def remove_experiment_index(apps, schema_editor):  # noqa: ARG001
    """Remove only the index owned by the temporary pulp-service migration."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT obj_description(indexrel.oid, 'pg_class')
            FROM pg_class AS indexrel
            JOIN pg_namespace AS namespace ON namespace.oid = indexrel.relnamespace
            WHERE indexrel.relname = %s AND namespace.nspname = current_schema()
            """,
            [_INDEX_NAME],
        )
        row = cursor.fetchone()
        if row and row[0] == _INDEX_MARKER:
            cursor.execute(_DROP_INDEX_SQL)


class Migration(migrations.Migration):
    """Remove the temporary index before pulpcore owns the replacement."""

    atomic = False  # DROP INDEX CONCURRENTLY cannot run in a transaction.

    dependencies = [
        ("service", "0024_contentartifact_relative_path_pattern_index"),
    ]

    operations = [
        migrations.RunPython(remove_experiment_index, migrations.RunPython.noop),
    ]
