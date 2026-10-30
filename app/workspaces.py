"""The working copies on the volume: what each one belongs to, whether it may be removed, and removing them.

Item workspaces live in `workspaces_root/items/<item id>`, task workspaces in
`workspaces_root/<task id>`. Everything else under the root (the shared `.cache`, stray files) is
ignored. Only the volume this process sees is listed: a copy on another worker host is invisible.
"""

import asyncio
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .archive import load_transcript
from .config import settings
from .db import Database, Project, WorkItem
from .engine import Engine
from .models import TaskInfo, TaskStatus

Phase = Literal["active", "idle", "finished", "failed", "orphan"]

ID = re.compile(r"^[0-9a-f]{32}$")
REMOVABLE: set[str] = {"finished", "failed", "orphan"}


class ItemRef(BaseModel):
    id: str
    project: str
    title: str
    state: str | None
    column: str
    branch: str | None
    pr_url: str | None


class TaskRef(BaseModel):
    id: str
    status: str
    prompt: str


class WorkspaceOut(BaseModel):
    id: str
    path: str
    size_bytes: int | None = Field(description="null when the size was not asked for")
    modified_at: datetime
    item: ItemRef | None = None
    task: TaskRef | None = None
    phase: Phase
    reason: str
    removable: bool


class PruneIn(BaseModel):
    older_than_hours: float = Field(default=0, ge=0, description="Only copies not modified for this long")
    include_failed: bool = Field(default=False, description="Also copies of failed and cancelled stories and tasks")
    dry_run: bool = False


class Removed(BaseModel):
    id: str
    path: str
    size_bytes: int


class Skipped(BaseModel):
    id: str
    error: str


class PruneOut(BaseModel):
    dry_run: bool
    removed: list[Removed]
    freed_bytes: int
    skipped: list[Skipped]


@dataclass
class Found:
    id: str
    path: Path
    is_item: bool
    modified_at: datetime


def _dirs(root: Path, is_item: bool) -> list[Found]:
    found = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return found
    for e in entries:
        if not ID.match(e.name) or e.is_symlink() or not e.is_dir(follow_symlinks=False):
            continue
        mtime = datetime.fromtimestamp(e.stat(follow_symlinks=False).st_mtime, UTC)
        found.append(Found(e.name, Path(e.path), is_item, mtime))
    return found


def find(only: str | None = None) -> list[Found]:
    """The workspace directories under the root, newest first. An item id and a task id never
    clash (both are random), so `only` can be matched by id alone."""
    root = settings.workspaces_root.resolve()
    out = _dirs(root / "items", True) + _dirs(root, False)
    out = [f for f in out if only is None or f.id == only]
    return sorted(out, key=lambda f: f.modified_at, reverse=True)


def dir_size(path: Path) -> int:
    """Bytes of the files under `path` (symlinks are counted as links, never followed)."""
    total = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    else:
                        try:
                            total += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass  # vanished meanwhile
        except OSError:
            pass
    return total


async def _task(db: Database, engine: Engine, task_id: str) -> TaskInfo | None:
    info = await engine.store.get(task_id)
    if info is not None:
        return info
    row = await load_transcript(db, task_id)
    return TaskInfo.model_validate(row.task) if row is not None else None


def _item_phase(engine: Engine, item: WorkItem, column: str) -> tuple[Phase, str]:
    if column == "done":
        return "finished", "the story is done"
    if column == "failed":
        return "failed", "the story failed or was cancelled; reopening it reuses this copy"
    if column == "working":
        wf = engine.workflows.get(item.workflow)
        st = wf.states.get(item.state) if wf and item.state else None
        step = (st.agent or st.action or st.check) if st else None
        return "active", f"step {step or item.state} is running here"
    if column == "needs_human":
        return "idle", "the story waits for a human and will continue in this copy"
    return "idle", "the story is in the backlog"


def _task_phase(info: TaskInfo) -> tuple[Phase, str]:
    if not info.status.is_terminal:
        return "active", f"the task is {info.status.value}"
    if info.status == TaskStatus.succeeded:
        return "finished", "the task succeeded"
    why = f": {info.failure_reason.value}" if info.failure_reason else ""
    return "failed", f"the task {info.status.value}{why}; a follow-up task can continue in this copy"


async def classify(
    db: Database, engine: Engine, column: Callable[[WorkItem], str], f: Found
) -> WorkspaceOut:
    base = dict(id=f.id, path=str(f.path), size_bytes=None, modified_at=f.modified_at)
    phase: Phase = "orphan"
    reason = ""
    item = task = None
    if f.is_item:
        async with db.session() as s:
            row = await s.get(WorkItem, f.id)
            project = await s.get(Project, row.project_id) if row else None
        if row is not None and project is not None:
            col = column(row)
            phase, reason = _item_phase(engine, row, col)
            item = ItemRef(
                id=row.id, project=project.slug, title=row.title, state=row.state, column=col,
                branch=row.branch, pr_url=row.pr_url,
            )
    elif info := await _task(db, engine, f.id):
        phase, reason = _task_phase(info)
        task = TaskRef(id=info.id, status=info.status.value, prompt=info.prompt)
    if phase == "orphan":
        reason = "no story or task refers to this copy"
    return WorkspaceOut(**base, item=item, task=task, phase=phase, reason=reason, removable=phase in REMOVABLE)


async def scan(
    db: Database,
    engine: Engine,
    column: Callable[[WorkItem], str],
    *,
    size: bool = True,
    phase: Phase | None = None,
    project: str | None = None,
    only: str | None = None,
) -> list[WorkspaceOut]:
    """Every workspace with its binding and phase. The filters apply before the sizes are
    measured, so a filtered list stays cheap."""
    found = await asyncio.to_thread(find, only)
    rows = [await classify(db, engine, column, f) for f in found]
    rows = [
        r for r in rows
        if (phase is None or r.phase == phase) and (project is None or (r.item and r.item.project == project))
    ]
    if size:
        sizes = await asyncio.gather(*(asyncio.to_thread(dir_size, Path(r.path)) for r in rows))
        for r, n in zip(rows, sizes):
            r.size_bytes = n
    return rows


def _remove(path: Path, expected_parent: Path) -> None:
    """rmtree behind a last guard: the directory must be a real one directly in its expected parent."""
    if path.is_symlink() or path.resolve().parent != expected_parent.resolve():
        raise ValueError("not a workspace directory")
    shutil.rmtree(path)


async def remove(
    db: Database, engine: Engine, column: Callable[[WorkItem], str], found: Found
) -> WorkspaceOut:
    """Delete one workspace if it is removable right now (decided again here, not taken from an
    earlier listing); returns it as it was. Raises PermissionError with the reason when it is not.
    A step that starts between this check and the deletion is not noticed."""
    row = await classify(db, engine, column, found)
    if not row.removable:
        raise PermissionError(row.reason)
    root = settings.workspaces_root.resolve()
    parent = root / "items" if found.is_item else root
    await asyncio.to_thread(_remove, found.path, parent)
    return row


async def prune(
    db: Database, engine: Engine, column: Callable[[WorkItem], str], body: PruneIn
) -> PruneOut:
    """Remove the copies of finished stories and tasks and the orphans (those of failed ones too
    with `include_failed`) that were not modified for `older_than_hours`; with `dry_run` only
    report them."""
    wanted: set[str] = {"finished", "orphan"} | ({"failed"} if body.include_failed else set())
    cutoff = datetime.now(UTC).timestamp() - body.older_than_hours * 3600
    removed, skipped, freed = [], [], 0
    for f in await asyncio.to_thread(find):
        if f.modified_at.timestamp() > cutoff:
            continue
        row = await classify(db, engine, column, f)
        if row.phase not in wanted:
            continue
        size = await asyncio.to_thread(dir_size, f.path)
        if not body.dry_run:
            try:
                await remove(db, engine, column, f)
            except (OSError, ValueError, PermissionError) as e:
                skipped.append(Skipped(id=f.id, error=str(e)))
                continue
        removed.append(Removed(id=f.id, path=str(f.path), size_bytes=size))
        freed += size
    return PruneOut(dry_run=body.dry_run, removed=removed, freed_bytes=freed, skipped=skipped)
