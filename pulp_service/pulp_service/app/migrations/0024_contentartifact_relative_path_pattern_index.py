from django.db import migrations

_INDEX_NAME = "core_ca_relpath_pattern_idx"
_INDEX_MARKER = "pulp-service:0024:core_ca_relpath_pattern_idx"
_CREATE_INDEX_SQL = (
    'CREATE INDEX CONCURRENTLY IF NOT EXISTS "core_ca_relpath_pattern_idx" '
    'ON "core_contentartifact" USING btree ("relative_path" text_pattern_ops)'
)
_DROP_INDEX_SQL = 'DROP INDEX CONCURRENTLY IF EXISTS "core_ca_relpath_pattern_idx"'
_COMMENT_INDEX_SQL = (
    "COMMENT ON INDEX \"core_ca_relpath_pattern_idx\" IS 'pulp-service:0024:core_ca_relpath_pattern_idx'"
)


def _index_info(cursor):
    cursor.execute(
        """
        SELECT pg_index.indisvalid, obj_description(indexrel.oid, 'pg_class')
        FROM pg_class AS indexrel
        JOIN pg_index ON pg_index.indexrelid = indexrel.oid
        JOIN pg_namespace AS namespace ON namespace.oid = indexrel.relnamespace
        WHERE indexrel.relname = %s AND namespace.nspname = current_schema()
        """,
        [_INDEX_NAME],
    )
    return cursor.fetchone()


def create_relative_path_index(apps, schema_editor):  # noqa: ARG001
    """Create the index without removing indexes owned by another migration."""
    with schema_editor.connection.cursor() as cursor:
        info = _index_info(cursor)
        if info:
            if not info[0]:
                raise RuntimeError(f"Index {_INDEX_NAME!r} exists but is invalid; refusing to drop it automatically.")
            return

        # Multiple migration workers can safely race here: IF NOT EXISTS prevents a second
        # physical index, and this migration never drops an index during forward application.
        cursor.execute(_CREATE_INDEX_SQL)
        # COMMENT provides durable ownership information for the reverse migration.
        cursor.execute(_COMMENT_INDEX_SQL)


def drop_relative_path_index(apps, schema_editor):  # noqa: ARG001
    """Drop only an index created by this migration, leaving shared indexes intact."""
    with schema_editor.connection.cursor() as cursor:
        info = _index_info(cursor)
        if info and info[1] == _INDEX_MARKER:
            cursor.execute(_DROP_INDEX_SQL)


class Migration(migrations.Migration):
    """Add the shared index used by the optimized content-handler queries."""

    atomic = False  # CREATE/DROP INDEX CONCURRENTLY cannot run in a transaction.

    dependencies = [
        ("service", "0023_backfill_domain_creator_role"),
    ]

    operations = [
        migrations.RunPython(create_relative_path_index, drop_relative_path_index),
    ]
