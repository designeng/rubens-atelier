"""One work item: details, edits, workspace access and the lifecycle actions; answering requests."""

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import gitops, terminal
from ..config import settings
from ..db import Artifact, HumanRequest, ItemEvent, Run, WorkItem
from ..engine import EngineError
from ..execution import item_workspace
from ..scheduler import SchedulerError
from .deps import Db, Eng, Sched, _engine_error
from .helpers import _check_blockers, _column, _get_item_out, _item_with_progress
from .schemas import (
    Answer,
    ArtifactOut,
    CommentIn,
    EventOut,
    ItemDetail,
    ItemOut,
    ItemPatch,
    RequestOut,
    RunOut,
)

router = APIRouter()


@router.get("/items/{item_id}")
async def get_item(item_id: str, db: Db, engine: Eng) -> ItemDetail:
    async with db.session() as s:
        item = await s.get(WorkItem, item_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
        runs = await s.scalars(select(Run).where(Run.item_id == item_id).order_by(Run.created_at))
        artifacts = list(
            await s.scalars(select(Artifact).where(Artifact.item_id == item_id).order_by(Artifact.created_at))
        )
        events = await s.scalars(select(ItemEvent).where(ItemEvent.item_id == item_id).order_by(ItemEvent.id))
        request = await s.scalar(
            select(HumanRequest).where(HumanRequest.item_id == item_id, HumanRequest.answer.is_(None))
        )
        errors = {a.run_id: a.content for a in artifacts if a.name == "error" and a.run_id}
        return ItemDetail(
            **(await _item_with_progress(engine, s, item)).model_dump(),
            body=item.body,
            runs=[RunOut.model_validate(r).model_copy(update={"error": errors.get(r.id)}) for r in runs],
            artifacts=[ArtifactOut.model_validate(a) for a in artifacts],
            events=[EventOut.model_validate(e) for e in events],
            open_request=RequestOut.model_validate(request) if request else None,
        )


@router.patch("/items/{item_id}")
async def update_item(item_id: str, body: ItemPatch, db: Db, engine: Eng) -> ItemOut:
    async with db.session() as s:
        item = await s.get(WorkItem, item_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
        old_priority = item.priority
        for field, value in body.model_dump(exclude_unset=True).items():
            if value is None and field in ("title", "body", "priority", "blocked_by"):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} cannot be null")
            if field == "blocked_by":
                value = await _check_blockers(s, item.id, item.project_id, value)
            setattr(item, field, value)
        await s.commit()
        if engine.scheduler and item.priority != old_priority:
            await engine.scheduler.reprioritize(item.id, item.version, old_priority, item.priority)
        return await _item_with_progress(engine, s, item)


@router.get("/items/{item_id}/git")
async def item_git(item_id: str, db: Db, diff: bool = False) -> dict:
    """What is in the story's workspace right now, read-only: `git status`, the commits and the
    changes against the base branch (and with `diff=true` the full diff)."""
    async with db.session() as s:
        item = await s.get(WorkItem, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
    cwd = item_workspace(item.id)
    if not item.branch or not (cwd / ".git").exists():
        raise HTTPException(status.HTTP_409_CONFLICT, "no workspace for this story (yet, or it is on another host)")
    return await gitops.snapshot(cwd, item.base_branch, diff)


class ExecIn(BaseModel):
    command: str = Field(min_length=1, max_length=terminal.COMMAND_LIMIT)


@router.post("/items/{item_id}/exec", status_code=status.HTTP_202_ACCEPTED)
async def exec_in_workspace(item_id: str, body: ExecIn, request: Request, db: Db, engine: Eng) -> dict:
    """Run a shell command in the story's workspace (on the worker); poll GET .../exec/{id}.
    Refused while an agent is working there, and unless the terminal is enabled and the API
    is token-protected."""
    if not settings.terminal_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "the terminal is disabled (HARNESS_TERMINAL_ENABLED)")
    if not settings.api_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "the terminal needs HARNESS_API_TOKEN to be set")
    async with db.session() as s:
        item = await s.get(WorkItem, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
    if not item.branch:
        raise HTTPException(status.HTTP_409_CONFLICT, "the story has no workspace yet")
    if _column(engine, item) == "working" and (st := engine.workflows[item.workflow].states[item.state]).agent:
        raise HTTPException(status.HTTP_409_CONFLICT, f"the {st.agent} agent is working in this workspace")
    redis = engine.store.r
    exec_id = await terminal.create(redis, item_id, body.command)
    if exec_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "another command is still running in this workspace")
    await request.app.state.exec_enqueue(exec_id)
    return {"id": exec_id}


@router.get("/items/{item_id}/exec/{exec_id}")
async def exec_result(item_id: str, exec_id: str, engine: Eng) -> dict:
    row = await terminal.get(engine.store.r, exec_id)
    if row is None or row["item_id"] != item_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "command not found (results are kept for an hour)")
    return {"id": exec_id, **{k: row.get(k) for k in ("command", "status", "exit_code", "output")}}


@router.post("/items/{item_id}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_item(item_id: str, db: Db, engine: Eng, confirm_external: bool = False) -> ItemOut:
    """`confirm_external` acknowledges that a story imported from an outside author's issue is untrusted."""
    try:
        await engine.start(item_id, confirm_external=confirm_external)
    except EngineError as e:
        raise _engine_error(e) from None
    return await _get_item_out(db, engine, item_id)


@router.post("/items/{item_id}/comments", status_code=status.HTTP_202_ACCEPTED)
async def comment_item(item_id: str, body: CommentIn, db: Db, engine: Eng) -> ItemOut:
    """Comment on the story's agent step: the run in progress finishes, then the same role
    resumes its session to read the comment before the story moves on. Costs an extra run."""
    try:
        await engine.comment(item_id, body.body)
    except EngineError as e:
        raise _engine_error(e) from None
    return await _get_item_out(db, engine, item_id)


@router.post("/items/{item_id}/run-now", status_code=status.HTTP_202_ACCEPTED)
async def run_item_now(item_id: str, db: Db, engine: Eng, scheduler: Sched) -> ItemOut:
    """Start the story's agent step that waits for a slot now, even while the scheduler is
    paused or full: lets the operator resume stopped stories one by one."""
    async with db.session() as s:
        item = await s.get(WorkItem, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"item {item_id} not found")
    try:
        await scheduler.run_now(item.id, item.version)
    except SchedulerError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    return await _get_item_out(db, engine, item_id)


@router.post("/items/{item_id}/open-pr", status_code=status.HTTP_202_ACCEPTED)
async def open_pr(item_id: str, db: Db, engine: Eng) -> ItemOut:
    """Push the branch and open the PR now (or update the open one), whatever the story is doing.
    A running agent is cancelled; the review and CI steps are skipped."""
    try:
        await engine.run_action(item_id, "open_pr")
    except EngineError as e:
        raise _engine_error(e) from None
    return await _get_item_out(db, engine, item_id)


@router.post("/items/{item_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_item(item_id: str, db: Db, engine: Eng) -> ItemOut:
    try:
        await engine.cancel(item_id)
    except EngineError as e:
        raise _engine_error(e) from None
    return await _get_item_out(db, engine, item_id)


@router.post("/items/{item_id}/reopen", status_code=status.HTTP_202_ACCEPTED)
async def reopen_item(item_id: str, db: Db, engine: Eng) -> ItemOut:
    """Return a failed or cancelled story to the backlog, to edit it and start it again."""
    try:
        await engine.reopen(item_id)
    except EngineError as e:
        raise _engine_error(e) from None
    return await _get_item_out(db, engine, item_id)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: str, engine: Eng) -> Response:
    """Delete a failed or cancelled story with its history. The confirmation is the client's."""
    try:
        await engine.delete(item_id)
    except EngineError as e:
        raise _engine_error(e) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/requests/{request_id}/answer")
async def answer_request(request_id: str, body: Answer, engine: Eng) -> dict:
    try:
        moved = await engine.answer(request_id, body.option, body.comment)
    except EngineError as e:
        raise _engine_error(e) from None
    return {"advanced": moved}
