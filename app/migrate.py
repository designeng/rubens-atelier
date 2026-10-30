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
from .db import Base

PRE_ALEMBIC_REVISION = "0001"
# Columns that migrations after PRE_ALEMBIC_REVISION add: a pre-Alembic database cannot have them yet.
ADDED_LATER = {"projects.budget_usd", "work_items.budget_usd"}


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
    """Columns of the models that a pre-Alembic database lacks (it never ran the last startup)."""
    return [
        f"{t.name}.{c.name}"
        for t in Base.metadata.sorted_tables
        for c in t.columns
        if c.name not in schema.get(t.name, set()) and f"{t.name}.{c.name}" not in ADDED_LATER
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
