"""Migrations: sync tests, because Alembic's env.py runs its own event loop (asyncio.run)."""

import asyncio

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db import Base
from app.migrate import PRE_ALEMBIC_REVISION, REVISION_0001_COLUMNS, _missing, alembic_config, migrate


def _run(fn):
    """fn(sync connection) on the test database, in a transaction."""

    async def go():
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                return await conn.run_sync(fn)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _diff(conn):
    return compare_metadata(MigrationContext.configure(conn), Base.metadata)


def _revision(conn):
    return MigrationContext.configure(conn).get_current_revision()


def _head():
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def _create_pre_alembic(conn, *, drop: str | None = None) -> None:
    """The schema create_all + the old ad-hoc ALTERs left: blocked_by nullable, no alembic_version."""
    legacy = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(legacy)
    legacy.tables["work_items"].c.blocked_by.nullable = True
    legacy.create_all(conn)
    if drop:
        conn.execute(text(f"ALTER TABLE work_items DROP COLUMN {drop}"))


def test_migrations_build_the_schema_of_the_models():
    migrate()

    assert _run(_revision) == _head()
    # A model change without a migration (or a migration the models disagree with) shows up here.
    assert _run(_diff) == []


def test_downgrade_to_base_drops_every_table():
    migrate()
    command.downgrade(alembic_config(), "base")

    assert set(_run(lambda c: inspect(c).get_table_names())) == {"alembic_version"}


def test_pre_alembic_database_is_stamped_and_upgraded():
    def setup(conn):
        _create_pre_alembic(conn)
        conn.execute(
            text(
                "INSERT INTO projects (id, slug, repo, auto_merge, created_at)"
                " VALUES ('p', 'p', 'o/r', false, '2026-01-01')"
            )
        )
        conn.execute(text(
            "INSERT INTO work_items (id, project_id, kind, title, body, workflow, version, priority, sessions, source,"
            " created_at, updated_at)"
            " VALUES ('old', 'p', 'story', 't', '', 'story', 0, 0, '{}', '{}', '2026-01-01', '2026-01-01')"
        ))

    _run(setup)
    migrate()

    assert _run(_revision) == _head()
    assert _run(lambda c: c.execute(text("SELECT blocked_by FROM work_items")).scalar()) == "[]"
    assert _run(_diff) == []


def test_pre_alembic_database_missing_a_column_is_not_stamped():
    _run(lambda c: _create_pre_alembic(c, drop="model"))

    with pytest.raises(SystemExit, match="work_items.model"):
        migrate()
    assert "alembic_version" not in _run(lambda c: inspect(c).get_table_names())


def test_revision_0001_columns_match_the_migration():
    command.upgrade(alembic_config(), PRE_ALEMBIC_REVISION)

    def reflect(conn):
        insp = inspect(conn)
        return {
            t: frozenset(c["name"] for c in insp.get_columns(t))
            for t in insp.get_table_names()
            if t != "alembic_version"
        }

    assert _run(reflect) == REVISION_0001_COLUMNS


def test_pre_alembic_check_ignores_columns_added_after_0001():
    schema = {t: set(cols) for t, cols in REVISION_0001_COLUMNS.items()}
    schema["work_items"].add("column_from_a_later_revision")
    assert _missing(schema) == []

    schema["work_items"].remove("model")
    assert _missing(schema) == ["work_items.model"]
