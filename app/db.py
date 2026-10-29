"""Durable factory state in SQL (Postgres in production, SQLite in tests).

Redis keeps what is short-lived (queue, agent event streams, leases); this keeps
what the factory is about: projects, the work graph, and the history of every step.

  projects        tenants: one repo each, with its own verify command
  work_items      epics / stories / tasks; `state` + `version` drive the workflow engine
  runs            one agent step of an item: which state, which harness task, which verdict
  artifacts       what steps hand to each other (implementation summary, review, human notes)
  human_requests  the inbox: decisions the workflow is waiting for
  item_events     every state change, for the board and for loop guards
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime) -> datetime:
    # SQLite hands timestamps back naive; Postgres keeps the tz.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON, datetime: DateTime(timezone=True)}


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    repo: Mapped[str] = mapped_column(String(200))  # GitHub owner/name
    base_branch: Mapped[str | None] = mapped_column(String(200))  # default: the repo's default branch
    verify_command: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class WorkItem(Base):
    __tablename__ = "work_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("work_items.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # epic | story | task
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    workflow: Mapped[str] = mapped_column(String(64))
    # None = backlog (not started). Otherwise a state of `workflow`.
    state: Mapped[str | None] = mapped_column(String(64), index=True)
    # Bumped on every transition; steps and answers carry the version they were created
    # for, so a stale or duplicated one can never move the item.
    version: Mapped[int] = mapped_column(default=0)
    branch: Mapped[str | None] = mapped_column(String(200))
    base_branch: Mapped[str | None] = mapped_column(String(200))
    pr_url: Mapped[str | None] = mapped_column(String(500))
    head_sha: Mapped[str | None] = mapped_column(String(40))  # last pushed commit, for CI checks
    priority: Mapped[int] = mapped_column(default=0)  # higher runs first
    # Claude session per role, so a role can resume its own context on the next visit.
    sessions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Where the item came from, e.g. {"issue": 42} for a GitHub issue.
    source: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("item_id", "version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id"), index=True)
    version: Mapped[int]
    state: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(String(32))  # harness task in Redis (events, stream)
    status: Mapped[str] = mapped_column(String(16), default="running")
    verdict: Mapped[str | None] = mapped_column(String(64))
    cost_usd: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    name: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class HumanRequest(Base):
    __tablename__ = "human_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id"), index=True)
    version: Mapped[int]
    state: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list[str]]
    answer: Mapped[str | None] = mapped_column(String(64), index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    answered_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    answered_at: Mapped[datetime | None]


class ItemEvent(Base):
    __tablename__ = "item_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id"), index=True)
    from_state: Mapped[str | None] = mapped_column(String(64))
    to_state: Mapped[str] = mapped_column(String(64))
    verdict: Mapped[str | None] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(100))
    at: Mapped[datetime] = mapped_column(default=utcnow)


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        # Good enough until the schema needs its first migration; then switch to Alembic.
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as s:
            yield s

    async def dispose(self) -> None:
        await self.engine.dispose()
