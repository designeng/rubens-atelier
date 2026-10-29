"""HTTP API of the factory: projects (tenants), work items, the Kanban board and the inbox."""

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import github_app
from .db import Artifact, Database, HumanRequest, ItemEvent, Project, Run, WorkItem
from .engine import Engine, EngineError
from .scheduler import Scheduler

Kind = Literal["epic", "story", "task"]
# Board columns, derived from the kind of the item's current state.
Column = Literal["backlog", "working", "needs_human", "done", "failed"]

router = APIRouter()
page = APIRouter()


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def get_scheduler(request: Request) -> Scheduler:
    return request.app.state.scheduler


Db = Annotated[Database, Depends(get_db)]
Eng = Annotated[Engine, Depends(get_engine)]
Sched = Annotated[Scheduler, Depends(get_scheduler)]


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


ModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ProjectIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    base_branch: str | None = None
    verify_command: str | None = None
    installation_id: int | None = Field(default=None, description="GitHub App installation granting access to repo")


class ProjectPatch(BaseModel):
    """Editable project settings; fields left out stay as they are, null clears them."""

    model_config = ConfigDict(extra="forbid")

    # Passed to git as a ref, so it must not start with "-" (would be read as an option).
    base_branch: str | None = Field(default=None, max_length=200, pattern=r"^[\w.][\w./-]*$")
    # Blank is rejected rather than stored: send null to clear.
    verify_command: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None


class ProjectOut(Orm):
    id: str
    slug: str
    repo: str
    base_branch: str | None
    verify_command: str | None
    installation_id: int | None


class ItemIn(BaseModel):
    kind: Kind = "story"
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    workflow: str = "story"
    parent_id: str | None = None
    issue: int | None = Field(default=None, ge=1, description="GitHub issue the PR should close")
    priority: int = Field(default=0, ge=-100, le=100, description="Higher is scheduled first")
    model: ModelName | None = None
    start: bool = False


class ItemPatch(BaseModel):
    """Editable item settings; applies from the next agent step, null goes back to the default."""

    model_config = ConfigDict(extra="forbid")

    model: ModelName | None = None


class ItemOut(Orm):
    id: str
    project_id: str
    parent_id: str | None
    kind: str
    title: str
    workflow: str
    state: str | None
    version: int
    branch: str | None
    pr_url: str | None
    priority: int
    model: str | None
    updated_at: datetime
    column: Column = "backlog"
    # Only for items in the working column: what is happening right now (see _progress).
    progress: dict[str, Any] | None = None


class RunOut(Orm):
    id: str
    version: int
    state: str
    role: str
    task_id: str
    status: str
    verdict: str | None
    cost_usd: float | None
    created_at: datetime
    error: str | None = None  # the `error` artifact of a run that ended in the error verdict


class ArtifactOut(Orm):
    id: str
    name: str
    content: str
    created_at: datetime


class EventOut(Orm):
    from_state: str | None
    to_state: str
    verdict: str | None
    actor: str
    at: datetime


class RequestOut(Orm):
    id: str
    item_id: str
    state: str
    question: str
    options: list[str]
    answer: str | None
    created_at: datetime


class ItemDetail(ItemOut):
    body: str
    runs: list[RunOut]
    artifacts: list[ArtifactOut]
    events: list[EventOut]
    open_request: RequestOut | None


class Answer(BaseModel):
    option: str
    comment: str = ""


def _column(engine: Engine, item: WorkItem) -> Column:
    if item.state is None:
        return "backlog"
    wf = engine.workflows.get(item.workflow)
    st = wf.states.get(item.state) if wf else None
    if st is None:
        return "failed"
    if st.terminal:
        return "done" if st.terminal == "succeeded" else "failed"
    return "needs_human" if st.human else "working"


def _item_out(engine: Engine, item: WorkItem) -> ItemOut:
    return ItemOut.model_validate(item).model_copy(update={"column": _column(engine, item)})


async def _progress(engine: Engine, s, item: WorkItem) -> dict[str, Any] | None:
    """Agent step: its live run (turn, activity, todos) or that it waits for a scheduler slot.
    Action and check steps: just which one is running."""
    wf = engine.workflows.get(item.workflow)
    st = wf.states.get(item.state) if wf and item.state else None
    if st is None or not (st.agent or st.action or st.check):
        return None
    if not st.agent:
        return {"step": st.action or st.check, "kind": st.kind}
    run = await s.scalar(select(Run).where(Run.item_id == item.id, Run.version == item.version))
    if run is None:
        queued = bool(engine.scheduler and await engine.scheduler.is_pending(item.id, item.version))
        return {"step": st.agent, "kind": "agent", "queued": queued}
    live = await engine.store.progress(run.task_id) or {}
    return {"step": st.agent, "kind": "agent", "task_id": run.task_id, **live}


async def _item_with_progress(engine: Engine, s, item: WorkItem) -> ItemOut:
    out = _item_out(engine, item)
    if out.column == "working":
        out.progress = await _progress(engine, s, item)
    return out


async def _project(db: Database, slug: str) -> Project:
    async with db.session() as s:
        project = await s.scalar(select(Project).where(Project.slug == slug))
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


def _engine_error(e: EngineError) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if "not found" in str(e) else status.HTTP_409_CONFLICT
    return HTTPException(code, str(e))


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectIn, db: Db) -> ProjectOut:
    if body.installation_id:
        # Only repos the installation was granted can be used; GitHub is the source of truth.
        try:
            repos = await github_app.list_repositories(body.installation_id)
        except (github_app.AppNotConfigured, httpx.HTTPError) as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"installation unavailable: {e}") from None
        if body.repo.lower() not in {r["full_name"].lower() for r in repos}:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "repository is not granted to this installation"
            )
    async with db.session() as s:
        project = Project(**body.model_dump())
        s.add(project)
        try:
            await s.commit()
        except IntegrityError:
            raise HTTPException(status.HTTP_409_CONFLICT, "project slug already exists") from None
        return ProjectOut.model_validate(project)


@router.get("/github/app")
async def github_app_status() -> dict:
    """Whether the GitHub App is set up, and where it is installed."""
    if not github_app.configured():
        return {"configured": False, "install_url": None, "installations": []}
    try:
        installations = await github_app.list_installations()
    except httpx.HTTPError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub: {e}") from None
    return {"configured": True, "install_url": github_app.install_url(), "installations": installations}


@router.get("/github/installations/{installation_id}/repositories")
async def installation_repositories(installation_id: int) -> list[dict]:
    try:
        return await github_app.list_repositories(installation_id)
    except github_app.AppNotConfigured as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    except httpx.HTTPError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub: {e}") from None


@router.get("/projects")
async def list_projects(db: Db) -> list[ProjectOut]:
    async with db.session() as s:
        return [ProjectOut.model_validate(p) for p in await s.scalars(select(Project).order_by(Project.slug))]


@router.patch("/projects/{slug}")
async def update_project(slug: str, body: ProjectPatch, db: Db) -> ProjectOut:
    """Change base_branch / verify_command. slug, repo and installation_id are immutable.

    verify_command applies from the next agent step of every story, running ones included.
    base_branch only affects stories that have no workspace yet: started ones keep their base.
    """
    async with db.session() as s:
        project = await s.scalar(select(Project).where(Project.slug == slug))
        if project is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(project, field, value)
        await s.commit()
        return ProjectOut.model_validate(project)


@router.post("/projects/{slug}/items", status_code=status.HTTP_201_CREATED)
async def create_item(slug: str, body: ItemIn, db: Db, engine: Eng) -> ItemOut:
    project = await _project(db, slug)
    try:
        engine.workflow(body.workflow)
    except EngineError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    async with db.session() as s:
        if body.parent_id:
            parent = await s.get(WorkItem, body.parent_id)
            if parent is None or parent.project_id != project.id:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "parent not found in this project")
        item = WorkItem(
            project_id=project.id,
            parent_id=body.parent_id,
            kind=body.kind,
            title=body.title,
            body=body.body,
            workflow=body.workflow,
            priority=body.priority,
            model=body.model,
            source={"issue": body.issue} if body.issue else {},
        )
        s.add(item)
        await s.commit()
    if body.start:
        await engine.start(item.id)
    return await _get_item_out(db, engine, item.id)


@router.get("/projects/{slug}/board")
async def board(slug: str, db: Db, engine: Eng) -> dict[Column, list[ItemOut]]:
    project = await _project(db, slug)
    columns: dict[Column, list[ItemOut]] = {c: [] for c in Column.__args__}
    async with db.session() as s:
        items = await s.scalars(
            select(WorkItem).where(WorkItem.project_id == project.id).order_by(WorkItem.updated_at.desc())
        )
        for item in items.all():
            out = await _item_with_progress(engine, s, item)
            columns[out.column].append(out)
    return columns


@router.get("/projects/{slug}/inbox")
async def inbox(slug: str, db: Db) -> list[RequestOut]:
    project = await _project(db, slug)
    async with db.session() as s:
        rows = await s.scalars(
            select(HumanRequest)
            .join(WorkItem, WorkItem.id == HumanRequest.item_id)
            .where(WorkItem.project_id == project.id, HumanRequest.answer.is_(None))
            .order_by(HumanRequest.created_at)
        )
        return [RequestOut.model_validate(r) for r in rows]


async def _get_item_out(db: Database, engine: Engine, item_id: str) -> ItemOut:
    async with db.session() as s:
        item = await s.get(WorkItem, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
    return _item_out(engine, item)


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
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(item, field, value)
        await s.commit()
        return _item_out(engine, item)


@router.post("/items/{item_id}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_item(item_id: str, db: Db, engine: Eng) -> ItemOut:
    try:
        await engine.start(item_id)
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


@router.post("/requests/{request_id}/answer")
async def answer_request(request_id: str, body: Answer, engine: Eng) -> dict:
    try:
        moved = await engine.answer(request_id, body.option, body.comment)
    except EngineError as e:
        raise _engine_error(e) from None
    return {"advanced": moved}


class Pause(BaseModel):
    reason: str = ""


@router.get("/scheduler")
async def scheduler_status(scheduler: Sched) -> dict:
    """Slots in use, queue, and the subscription windows the pacing is based on."""
    return await scheduler.status()


@router.post("/scheduler/pause")
async def pause_scheduler(body: Pause, scheduler: Sched) -> dict:
    """Stop dispatching new agent steps; running ones finish."""
    await scheduler.pause(body.reason)
    return await scheduler.status()


@router.post("/scheduler/resume")
async def resume_scheduler(scheduler: Sched) -> dict:
    await scheduler.resume()
    return await scheduler.status()


@page.get("/board", include_in_schema=False)
async def board_page() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "board.html")
