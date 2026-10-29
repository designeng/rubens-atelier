"""HTTP API of the factory: projects (tenants), work items, the Kanban board and the inbox."""

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .db import Artifact, Database, HumanRequest, ItemEvent, Project, Run, WorkItem
from .engine import Engine, EngineError

Kind = Literal["epic", "story", "task"]
# Board columns, derived from the kind of the item's current state.
Column = Literal["backlog", "working", "needs_human", "done", "failed"]

router = APIRouter()
page = APIRouter()


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


Db = Annotated[Database, Depends(get_db)]
Eng = Annotated[Engine, Depends(get_engine)]


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    base_branch: str | None = None
    verify_command: str | None = None


class ProjectOut(Orm):
    id: str
    slug: str
    repo: str
    base_branch: str | None
    verify_command: str | None


class ItemIn(BaseModel):
    kind: Kind = "story"
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    workflow: str = "story"
    parent_id: str | None = None
    issue: int | None = Field(default=None, ge=1, description="GitHub issue the PR should close")
    start: bool = False


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
    updated_at: datetime
    column: Column = "backlog"


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
    async with db.session() as s:
        project = Project(**body.model_dump())
        s.add(project)
        try:
            await s.commit()
        except IntegrityError:
            raise HTTPException(status.HTTP_409_CONFLICT, "project slug already exists") from None
        return ProjectOut.model_validate(project)


@router.get("/projects")
async def list_projects(db: Db) -> list[ProjectOut]:
    async with db.session() as s:
        return [ProjectOut.model_validate(p) for p in await s.scalars(select(Project).order_by(Project.slug))]


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
        for item in items:
            out = _item_out(engine, item)
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
        artifacts = await s.scalars(select(Artifact).where(Artifact.item_id == item_id).order_by(Artifact.created_at))
        events = await s.scalars(select(ItemEvent).where(ItemEvent.item_id == item_id).order_by(ItemEvent.id))
        request = await s.scalar(
            select(HumanRequest).where(HumanRequest.item_id == item_id, HumanRequest.answer.is_(None))
        )
        return ItemDetail(
            **_item_out(engine, item).model_dump(),
            body=item.body,
            runs=[RunOut.model_validate(r) for r in runs],
            artifacts=[ArtifactOut.model_validate(a) for a in artifacts],
            events=[EventOut.model_validate(e) for e in events],
            open_request=RequestOut.model_validate(request) if request else None,
        )


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


@page.get("/board", include_in_schema=False)
async def board_page() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "board.html")
