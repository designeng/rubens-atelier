"""Workspaces on the API's volume: list, prune and delete."""

import asyncio

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import workspaces
from ..config import settings
from .deps import Eng

router = APIRouter()


@router.get("/workspaces")
async def list_workspaces(
    engine: Eng, size: bool = True, phase: workspaces.Phase | None = None, project: str | None = None
) -> dict:
    """Every workspace on the volume the API sees, with the story or task that owns it, its
    size and whether it can be removed. `size=false` skips the (slow) disk usage; `phase` and
    `project` filter. Phases: orphan, finished, failed, idle, busy (see app/workspaces.py)."""
    found = await asyncio.to_thread(workspaces.scan, size)
    rows = await workspaces.describe(engine, found)
    rows = [
        r
        for r in rows
        if (phase is None or r["phase"] == phase) and (project is None or (r["item"] or {}).get("project") == project)
    ]
    return {
        "root": str(settings.workspaces_root.resolve()),
        "count": len(rows),
        "size_bytes": sum(r["size_bytes"] or 0 for r in rows) if size else None,
        "workspaces": rows,
    }


class PruneIn(BaseModel):
    older_than_hours: float = Field(default=0, ge=0)
    # Also failed and cancelled stories/tasks and backlog stories: unpushed commits are lost.
    include_failed: bool = False
    dry_run: bool = False


@router.post("/workspaces/prune", status_code=status.HTTP_202_ACCEPTED)
async def prune_workspaces(body: PruneIn, request: Request, engine: Eng) -> dict:
    """Remove the workspaces of finished stories and tasks and of deleted ones (never one that
    is in use or waits for a person). The worker deletes them; list again to see the result."""
    rows = await workspaces.describe(engine, await asyncio.to_thread(workspaces.scan, True))
    picked = workspaces.prunable(rows, include_failed=body.include_failed, older_than_hours=body.older_than_hours)
    ids = [r["id"] for r in picked]
    if ids and not body.dry_run:
        await request.app.state.workspaces_enqueue(ids, body.include_failed)
    return {
        "dry_run": body.dry_run,
        "queued": [] if body.dry_run else ids,
        "would_remove": ids if body.dry_run else None,
        "size_bytes": sum(r["size_bytes"] or 0 for r in picked),
    }


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_workspace(workspace_id: str, request: Request, engine: Eng) -> dict:
    """Remove one workspace (a story's or a task's id). 409 while an agent, an action or a
    command uses it, or a person is about to continue in it. A failed or cancelled story loses
    unpushed commits."""
    found = await asyncio.to_thread(workspaces.find, workspace_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not found")
    (row,) = await workspaces.describe(engine, [found])
    if not row["removable"]:
        raise HTTPException(status.HTTP_409_CONFLICT, row["reason"])
    await request.app.state.workspaces_enqueue([workspace_id], True)
    return {"queued": [workspace_id], "phase": row["phase"]}
