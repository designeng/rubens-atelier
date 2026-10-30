import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from redis.asyncio import Redis
from sse_starlette import EventSourceResponse

from . import board_api, checks, terminal, webhooks, workspaces
from .archive import events_after, load_transcript
from .config import settings
from .db import Database
from .engine import Engine, celery_enqueue
from .scheduler import Scheduler
from .models import IssueRef, TaskCreate, TaskInfo, TaskStatus
from .service import SubmitError, submit
from .store import TaskStore
from .workflow import load_workflows

SSE_BLOCK_MS = 15_000


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.store = TaskStore(app.state.redis)
    app.state.db = Database(settings.database_url)
    await app.state.db.create_all()
    app.state.scheduler = Scheduler(app.state.redis, celery_enqueue)
    app.state.exec_enqueue = terminal.celery_enqueue
    app.state.checks_enqueue = checks.celery_enqueue
    app.state.workspaces_enqueue = workspaces.celery_enqueue
    app.state.engine = Engine(
        app.state.db, load_workflows(settings.workflows_dir), app.state.store, celery_enqueue, app.state.scheduler
    )
    yield
    await app.state.db.dispose()
    await app.state.redis.aclose()


bearer = HTTPBearer(auto_error=False)


def require_token(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> None:
    if settings.api_token and not (
        creds and secrets.compare_digest(creds.credentials, settings.api_token)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token")


def get_store(request: Request) -> TaskStore:
    return request.app.state.store


Store = Annotated[TaskStore, Depends(get_store)]


def get_db(request: Request) -> Database:
    return request.app.state.db


Db = Annotated[Database, Depends(get_db)]

app = FastAPI(title="Claude Code agent harness", lifespan=lifespan)
app.include_router(board_api.router, dependencies=[Depends(require_token)])
app.include_router(board_api.page)
# Signed by GitHub with the webhook secret instead of the bearer token.
app.include_router(webhooks.router)


async def _get_or_404(store: TaskStore, task_id: str, db: Database | None = None) -> TaskInfo:
    """The task from Redis, or from the Postgres archive (`db`) once Redis has expired it."""
    info = await store.get(task_id)
    if info is None and db is not None and (transcript := await load_transcript(db, task_id)):
        info = TaskInfo.model_validate(transcript.task)
    if info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return info


async def _archived_events(
    store: TaskStore, db: Database, task_id: str, after: str, limit: int | None = None
) -> list[dict] | None:
    """Archived events of a task Redis no longer has; None while Redis is the source."""
    if await store.get_raw(task_id) is not None:
        return None
    transcript = await load_transcript(db, task_id)
    if transcript is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    try:
        return events_after(transcript.events, after, limit)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid event id {after!r}") from None


@app.get("/healthz")
async def healthz(store: Store) -> dict:
    await store.r.ping()
    return {"ok": True}


@app.post("/tasks", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_token)])
async def create_task(spec: TaskCreate, store: Store) -> TaskInfo:
    try:
        return await submit(store, spec)
    except SubmitError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None


class IssueRun(IssueRef):
    prompt: str = ""
    verify_command: str | None = None
    model: str | None = None
    max_budget_usd: float | None = None


@app.post("/issues/run", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_token)])
async def run_issue(body: IssueRun, store: Store) -> TaskInfo:
    """Shortcut for POST /tasks with an `issue`: implement, verify, push, open a PR."""
    spec = TaskCreate(
        issue=IssueRef(**body.model_dump(include=set(IssueRef.model_fields))),
        prompt=body.prompt,
        verify_command=body.verify_command,
        model=body.model,
        max_budget_usd=body.max_budget_usd,
    )
    return await create_task(spec, store)


@app.get("/tasks", dependencies=[Depends(require_token)])
async def list_tasks(
    store: Store,
    status_: Annotated[TaskStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[TaskInfo]:
    return await store.list_tasks(limit=limit, status=status_)


@app.get("/tasks/{task_id}", dependencies=[Depends(require_token)])
async def get_task(task_id: str, store: Store, db: Db) -> TaskInfo:
    return await _get_or_404(store, task_id, db)


@app.post("/tasks/{task_id}/cancel", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_token)])
async def cancel_task(task_id: str, store: Store) -> TaskInfo:
    await _get_or_404(store, task_id)
    await store.request_cancel(task_id)
    return await _get_or_404(store, task_id)


class Event(BaseModel):
    id: str
    type: str
    data: dict


@app.get("/tasks/{task_id}/events", dependencies=[Depends(require_token)])
async def get_events(
    task_id: str,
    store: Store,
    db: Db,
    after: str = "0",
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> list[Event]:
    """Paged event log; pass the last seen `id` as `after` to continue."""
    archived = await _archived_events(store, db, task_id, after, limit)
    if archived is not None:
        return [Event(**e) for e in archived]
    return [Event(id=i, type=t, data=d) for i, t, d in await store.read_events(task_id, after, limit)]


@app.get("/tasks/{task_id}/stream", dependencies=[Depends(require_token)])
async def stream_events(
    task_id: str,
    store: Store,
    db: Db,
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
    after: str = "0",
) -> EventSourceResponse:
    """Server-sent events: replays history, then follows live until the task finishes.
    Reconnecting clients resume via the standard Last-Event-ID header. A task Redis has
    expired is replayed from the archive; it is finished, so there is nothing to follow."""
    archived = await _archived_events(store, db, task_id, last_event_id or after)
    if archived is not None:

        async def replay() -> AsyncIterator[dict]:
            for e in archived:
                yield {"id": e["id"], "event": e["type"], "data": json.dumps(e["data"])}

        return EventSourceResponse(replay(), ping=15)

    async def gen() -> AsyncIterator[dict]:
        cursor = last_event_id or after
        while not await request.is_disconnected():
            events = await store.read_events(task_id, cursor, block_ms=SSE_BLOCK_MS)
            for event_id, type_, data in events:
                cursor = event_id
                yield {"id": event_id, "event": type_, "data": json.dumps(data)}
                if type_ == "status" and TaskStatus(data["status"]).is_terminal:
                    return
            if not events:
                info = await store.get(task_id)
                if info is None or info.status.is_terminal:
                    return

    return EventSourceResponse(gen(), ping=15)
