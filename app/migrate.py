"""Bring the database to the latest migration: `python -m app.migrate` (make migrate, compose `migrate`).

Runs as its own step before api, worker and beat. A database built before Alembic (tables but
no alembic_version) is first stamped at the initial revision, whose schema it already has.
"""

import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from .config import ROOT, settings

PRE_ALEMBIC_REVISION = "0001"

# The columns of revision 0001, frozen. A pre-Alembic database is stamped at that revision, so it is
# checked against that schema and not against the current models. Never edit this: a column added
# later belongs in a new revision, which `upgrade head` applies after the stamp.
REVISION_0001_COLUMNS: dict[str, frozenset[str]] = {
    "projects": frozenset(
        {"id", "slug", "repo", "base_branch", "verify_command", "installation_id", "auto_merge", "created_at"}
    ),
    "work_items": frozenset(
        {
            "id", "project_id", "parent_id", "kind", "title", "body", "workflow", "state", "version",
            "branch", "base_branch", "pr_url", "head_sha", "priority", "model", "blocked_by", "sessions",
            "source", "created_at", "updated_at",
        }
    ),
    "human_requests": frozenset(
        {
            "id", "item_id", "version", "state", "question", "options", "answer", "comment",
            "answered_by", "created_at", "answered_at",
        }
    ),
    "item_events": frozenset({"id", "item_id", "from_state", "to_state", "verdict", "actor", "at"}),
    "runs": frozenset(
        {
            "id", "item_id", "version", "state", "role", "task_id", "status", "verdict", "cost_usd",
            "created_at", "finished_at",
        }
    ),
    "artifacts": frozenset({"id", "item_id", "run_id", "name", "content", "created_at"}),
    "task_transcripts": frozenset({"task_id", "run_id", "task", "events", "archived_at"}),
}


def alembic_config() -> Config:
    return Config(ROOT / "alembic.ini")


def _columns(conn) -> dict[str, set[str]]:
    insp = inspect(conn)
    return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()}


async def _schema() -> dict[str, set[str]]:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(_columns)
    finally:
        await engine.dispose()


def _missing(schema: dict[str, set[str]]) -> list[str]:
    """Columns of revision 0001 that a pre-Alembic database lacks (it never ran the last startup)."""
    return [
        f"{table}.{column}"
        for table, columns in REVISION_0001_COLUMNS.items()
        for column in sorted(columns - schema.get(table, set()))
    ]


def migrate() -> None:
    config = alembic_config()
    schema = asyncio.run(_schema())
    if "projects" in schema and "alembic_version" not in schema:
        if missing := _missing(schema):
            lacks = ", ".join(missing)
            raise SystemExit(f"pre-Alembic database lacks {lacks}: not stamping it at {PRE_ALEMBIC_REVISION}")
        command.stamp(config, PRE_ALEMBIC_REVISION)
    command.upgrade(config, "head")


if __name__ == "__main__":
    migrate()
