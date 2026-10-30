"""Workspaces on disk: what is there, whom it belongs to, and what can go.

The API sees the volume read-only, so it only lists and decides; `harness.remove_workspaces`
on the worker (which owns the writable volume) deletes, and re-checks every workspace first.

Layout under `workspaces_root`:
  items/{item_id}  a work item's workspace, kept across its steps
  {task_id}        a plain task's workspace (reused by its follow-ups)
  .cache/          shared caches, never listed

Phases, from what the owner is doing:
  orphan    nobody owns it any more (item deleted, task expired from Redis)
  finished  the item succeeded / the task succeeded
  failed    the item failed or was cancelled / the task failed or was cancelled; unpushed
            commits would be lost, and a reopened item would start from a fresh clone
  idle      the item is in the backlog
  busy      an agent, action or command is using it, or a person is about to continue in it
"""

import asyncio
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select

from .config import settings
from .db import Project, WorkItem
from .engine import Engine
from .models import TaskStatus
from .terminal import lock_key

Phase = Literal["orphan", "finished", "failed", "idle", "busy"]
ID = re.compile(r"^[0-9a-f]{32}$")
# Phases that prune takes without being asked.
SAFE = ("orphan", "finished")
# Phases an explicit delete or `include_failed` may take.
RISKY = ("failed", "idle")


@dataclass
class Found:
    id: str
    kind: str  # item | task | other
    path: Path
    mtime: float
    size_bytes: int | None = None


async def celery_enqueue(ids: list[str], include_failed: bool) -> None:
    from .celery_app import remove_workspaces

    await asyncio.to_thread(remove_workspaces.apply_async, args=[ids, include_failed])


def _du(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def scan(with_size: bool = True) -> list[Found]:
    root = settings.workspaces_root.resolve()
    found: list[Found] = []

    def add(path: Path, kind: str) -> None:
        if not path.is_dir() or path.is_symlink():
            return
        found.append(Found(path.name, kind, path, path.stat().st_mtime, _du(path) if with_size else None))

    if (root / "items").is_dir():
        for p in sorted((root / "items").iterdir()):
            add(p, "item" if ID.match(p.name) else "other")
    if root.is_dir():
        for p in sorted(root.iterdir()):
            if p.name != "items" and not p.name.startswith("."):
                add(p, "task" if ID.match(p.name) else "other")
    return found


def find(workspace_id: str) -> Found | None:
    return next((f for f in scan(with_size=False) if f.id == workspace_id), None)


def _item_phase(engine: Engine, item: WorkItem) -> tuple[str, str, str]:
    """(phase, column, why) for a work item."""
    wf = engine.workflows.get(item.workflow)
    st = wf.states.get(item.state) if wf and item.state else None
    if item.state is None:
        return "idle", "backlog", "in the backlog"
    if st is None:
        return "failed", "failed", f"unknown state {item.state!r}"
    if st.terminal:
        return ("finished" if st.terminal == "succeeded" else "failed"), item.state, f"story {st.terminal}"
    if st.human:
        return "busy", "needs_human", "waits for a person; the workspace is needed to continue"
    return "busy", "working", f"the {item.state} step is running"


async def describe(engine: Engine, found: list[Found]) -> list[dict[str, Any]]:
    """Join the directories with their items (Postgres) and tasks (Redis)."""
    r = engine.store.r
    items: dict[str, tuple[WorkItem, str]] = {}
    async with engine.db.session() as s:
        ids = [f.id for f in found if f.kind == "item"]
        if ids:
            rows = (await s.execute(
                select(WorkItem, Project.slug).join(Project, Project.id == WorkItem.project_id).where(WorkItem.id.in_(ids))
            )).all()
            items = {it.id: (it, slug) for it, slug in rows}

    out = []
    for f in found:
        row: dict[str, Any] = {
            "id": f.id,
            "kind": f.kind,
            "path": str(f.path),
            "size_bytes": f.size_bytes,
            "modified_at": datetime.fromtimestamp(f.mtime).astimezone().isoformat(),
            "item": None,
            "task": None,
        }
        if f.kind == "item":
            if f.id in items:
                item, slug = items[f.id]
                phase, column, why = _item_phase(engine, item)
                if phase != "busy" and await r.exists(lock_key(f.id)):
                    phase, why = "busy", "a terminal command is running in it"
                row["item"] = {
                    "id": item.id, "project": slug, "kind": item.kind, "title": item.title, "state": item.state,
                    "column": column, "branch": item.branch, "pr_url": item.pr_url,
                    "updated_at": item.updated_at.isoformat(),
                }
            else:
                phase, why = "orphan", "no such story (deleted)"
        elif f.kind == "task":
            info = await engine.store.get(f.id)
            if info is None:
                phase, why = "orphan", "the task has expired from Redis"
            else:
                row["task"] = {
                    "id": info.id, "status": info.status.value, "prompt": info.prompt[:200],
                    "finished_at": info.finished_at.isoformat() if info.finished_at else None,
                }
                if not info.status.is_terminal:
                    phase, why = "busy", f"the task is {info.status.value}"
                elif info.status == TaskStatus.succeeded:
                    phase, why = "finished", "the task succeeded"
                else:
                    phase, why = "failed", f"the task {info.status.value}"
        else:
            phase, why = "busy", "not a harness workspace"
        row.update(phase=phase, reason=why, removable=phase in SAFE + RISKY)
        out.append(row)
    return out


def old_enough(row: dict[str, Any], older_than_hours: float) -> bool:
    """Age by the owner's last change; the directory's own mtime for orphans."""
    if not older_than_hours:
        return True
    stamp = (row["item"] or {}).get("updated_at") or (row["task"] or {}).get("finished_at") or row["modified_at"]
    when = datetime.fromisoformat(stamp)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)  # item timestamps are naive UTC
    return time.time() - when.timestamp() >= older_than_hours * 3600


def prunable(rows: list[dict[str, Any]], *, include_failed: bool = False, older_than_hours: float = 0) -> list[dict[str, Any]]:
    phases = SAFE + (RISKY if include_failed else ())
    return [r for r in rows if r["phase"] in phases and old_enough(r, older_than_hours)]


async def remove(engine: Engine, ids: list[str], *, include_failed: bool = False) -> dict[str, str]:
    """Worker side: delete the given workspaces that are still removable. id → "removed" or why not."""
    wanted = set(ids)
    found = [f for f in scan(with_size=False) if f.id in wanted]
    rows = {r["id"]: r for r in await describe(engine, found)}
    result: dict[str, str] = {}
    for i in ids:
        row = rows.get(i)
        if row is None:
            result[i] = "not found"
        elif row["phase"] in SAFE or (row["phase"] in RISKY and include_failed):
            path = Path(row["path"])
            # Only ever under the root, and never through a link.
            if path.is_symlink() or settings.workspaces_root.resolve() not in path.resolve().parents:
                result[i] = "outside the workspaces root"
                continue
            await asyncio.to_thread(shutil.rmtree, path, True)
            result[i] = "removed"
        else:
            result[i] = f"kept: {row['reason']}"
    return result
