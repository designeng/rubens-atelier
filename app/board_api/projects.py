"""Projects, their items, the Kanban board and the inbox; GitHub App and model lookups."""

import asyncio
import time
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, not_, or_, select
from sqlalchemy.exc import IntegrityError

from .. import github_app
from ..budget import forecast, project_spending
from ..config import settings
from ..db import HumanRequest, ItemEvent, Note, Project, WorkItem, new_id
from ..engine import BACKLOG, Engine, EngineError
from ..github import GitHub
from .deps import Db, Eng, _project
from .helpers import (
    _check_blockers,
    _dependents,
    _get_item_out,
    _item_out,
    _item_with_progress,
    _preload_runs,
    _project_out,
)
from .schemas import (
    Column,
    TransitionOut,
    TransitionsOut,
    ImportResult,
    ItemIn,
    ItemOut,
    PlanIn,
    PlanOut,
    ProjectIn,
    ProjectOut,
    ProjectPatch,
    RequestOut,
)

router = APIRouter()


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
        return await _project_out(s, project)


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


@router.get("/models")
async def list_models() -> dict:
    """Models the board offers for a story, and the one used when a story and its step set none;
    also whether agents run with lean context when the story doesn't say."""
    return {"default": settings.default_model, "models": settings.models, "lean_context": settings.lean_context}


@router.get("/projects")
async def list_projects(db: Db) -> list[ProjectOut]:
    async with db.session() as s:
        return [await _project_out(s, p) for p in await s.scalars(select(Project).order_by(Project.slug))]


@router.patch("/projects/{slug}")
async def update_project(slug: str, body: ProjectPatch, db: Db) -> ProjectOut:
    """Change base_branch / verify_command / auto_merge / budget_usd. slug, repo and installation_id are immutable.

    verify_command applies from the next agent step of every story, running ones included.
    auto_merge applies to every story that reaches its merge step from now on.
    base_branch only affects stories that have no workspace yet: started ones keep their base.
    budget_usd is checked before each new agent run (see budget.py); null removes the limit.
    """
    async with db.session() as s:
        project = await s.scalar(select(Project).where(Project.slug == slug))
        if project is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(project, field, value)
        await s.commit()
        return await _project_out(s, project)


@router.post("/projects/{slug}/items", status_code=status.HTTP_201_CREATED)
async def create_item(slug: str, body: ItemIn, db: Db, engine: Eng) -> ItemOut:
    project = await _project(db, slug)
    try:
        engine.workflow(body.workflow)
    except EngineError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    async with db.session() as s:
        await _check_parent(s, project.id, body.parent_id)
        item = _new_item(project.id, body, await _check_blockers(s, None, project.id, body.blocked_by))
        if body.start and (waiting := await engine.unfinished_blockers(s, item)):
            # Refused before anything is stored, so no half-created item is left behind.
            raise HTTPException(status.HTTP_409_CONFLICT, f"blocked by: {', '.join(waiting)}")
        s.add(item)
        await s.commit()
    if body.start:
        await engine.start(item.id)
    elif body.auto_start:
        await engine.start_when_ready([item.id])
    return await _get_item_out(db, engine, item.id)


@router.post("/projects/{slug}/plan", status_code=status.HTTP_201_CREATED)
async def create_plan(slug: str, body: PlanIn, db: Db, engine: Eng) -> PlanOut:
    """Create an epic (optional) and its items in one go. An item's `blocked_by` names items of the
    plan by `key`, or existing items of the project by id; items without a `parent_id` go under the
    epic. All or nothing: a bad reference, a cycle or a `start` that would be refused stores nothing.
    Items with `auto_start` and no unfinished blockers start right away, the others as their blockers
    succeed."""
    project = await _project(db, slug)
    keys = [i.key for i in body.items]
    if dupes := sorted({k for k in keys if keys.count(k) > 1}):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"duplicate keys: {', '.join(dupes)}")
    for spec in body.items:
        try:
            engine.workflow(spec.workflow)
        except EngineError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{spec.key}: {e}") from None
    ids = {k: new_id() for k in keys}
    local = {k: [b for b in dict.fromkeys(spec.blocked_by) if b in ids] for k, spec in zip(keys, body.items)}
    if cycle := _find_cycle(local):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"blockers form a cycle: {' -> '.join(cycle)}")

    async with db.session() as s:
        epic = None
        if body.epic:
            epic = WorkItem(id=new_id(), project_id=project.id, kind="epic", workflow="story", **body.epic.model_dump())
            s.add(epic)
        items: dict[str, WorkItem] = {}
        for spec in body.items:
            await _check_parent(s, project.id, spec.parent_id)
            existing = [b for b in spec.blocked_by if b not in ids]
            # Existing items never wait for new ones, so they cannot close a cycle through the plan.
            existing = await _check_blockers(s, None, project.id, existing)
            item = _new_item(project.id, spec, [ids[b] for b in local[spec.key]] + existing)
            item.id = ids[spec.key]
            if item.parent_id is None and epic is not None:
                item.parent_id = epic.id
            if spec.start:
                waiting = [f"{b} (this plan)" for b in local[spec.key]] + await engine.unfinished_blockers(
                    s, WorkItem(blocked_by=existing)
                )
                if waiting:
                    raise HTTPException(status.HTTP_409_CONFLICT, f"{spec.key}: blocked by: {', '.join(waiting)}")
            items[spec.key] = item
        s.add_all(items.values())
        await s.commit()

    for spec in body.items:
        if spec.start:
            await engine.start(ids[spec.key])
    await engine.start_when_ready(ids[spec.key] for spec in body.items if spec.auto_start and not spec.start)
    return PlanOut(
        epic=await _get_item_out(db, engine, epic.id) if epic else None,
        items={k: await _get_item_out(db, engine, ids[k]) for k in keys},
    )


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """A cycle of `graph` (node -> the nodes it waits for) as a path that ends where it starts, or None."""
    done: set[str] = set()

    def visit(node: str, path: list[str]) -> list[str] | None:
        if node in path:
            return path[path.index(node):] + [node]
        if node in done:
            return None
        for nxt in graph[node]:
            if cycle := visit(nxt, [*path, node]):
                return cycle
        done.add(node)
        return None

    return next(filter(None, (visit(node, []) for node in graph)), None)


async def _check_parent(s, project_id: str, parent_id: str | None) -> None:
    if parent_id:
        parent = await s.get(WorkItem, parent_id)
        if parent is None or parent.project_id != project_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "parent not found in this project")


def _new_item(project_id: str, body: ItemIn, blocked_by: list[str]) -> WorkItem:
    return WorkItem(
        project_id=project_id,
        parent_id=body.parent_id,
        kind=body.kind,
        title=body.title,
        body=body.body,
        workflow=body.workflow,
        priority=body.priority,
        model=body.model,
        lean_context=body.lean_context,
        budget_usd=body.budget_usd,
        blocked_by=blocked_by,
        auto_start=body.auto_start,
        source={"issue": body.issue} if body.issue else {},
    )


# Issue authors whose text is trusted; anyone else (or an unknown association) is "external".
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


@router.post("/projects/{slug}/import-issues")
async def import_issues(slug: str, db: Db, engine: Eng) -> ImportResult:
    """Put every open issue of the project's repo into the backlog as a story; nothing is started.

    An issue that already has an item (in any state, cancelled included) is skipped, so a repeat is a no-op.
    """
    project = await _project(db, slug)
    try:
        gh = GitHub.for_project(project)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    try:
        issues = await gh.list_issues(project.repo)
    except httpx.HTTPError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub: {e}") from None
    finally:
        await gh.aclose()
    async with db.session() as s:
        # Compared in Python: JSON queries differ between SQLite and Postgres, and projects are small.
        sources = await s.scalars(select(WorkItem.source).where(WorkItem.project_id == project.id))
        known = {src.get("issue") for src in sources if src}
        new = [
            WorkItem(
                project_id=project.id,
                kind="story",
                title=(issue.get("title") or f"Issue #{issue['number']}")[:300],  # ItemIn.title limit
                body="\n\n".join(
                    filter(None, [(issue.get("body") or "").strip(), f"Imported from {issue['html_url']}"])
                ),
                workflow="story",
                source={
                    "issue": issue["number"],
                    "author": (issue.get("user") or {}).get("login"),
                    "author_association": issue.get("author_association"),
                    "external": issue.get("author_association") not in TRUSTED_ASSOCIATIONS,
                },
            )
            for issue in issues
            if issue["number"] not in known
        ]
        s.add_all(new)
        await s.commit()
        return ImportResult(imported=[_item_out(engine, i) for i in new], skipped=len(issues) - len(new))


DONE_LIMIT_DEFAULT = 50
DONE_LIMIT_MAX = 500


@router.get("/projects/{slug}/board")
async def board(
    slug: str,
    response: Response,
    db: Db,
    engine: Eng,
    done_limit: Annotated[int, Query(ge=1, le=DONE_LIMIT_MAX)] = DONE_LIMIT_DEFAULT,
) -> dict[Column, list[ItemOut]]:
    """The items of the project by column. The done column holds only the `done_limit` most
    recently updated items; how many there are in total is in the `X-Done-Total` header. Notes
    agents proposed and no person has reviewed yet are counted in `X-Proposed-Notes`."""
    project = await _project(db, slug)
    columns: dict[Column, list[ItemOut]] = {c: [] for c in Column.__args__}
    in_project = WorkItem.project_id == project.id
    done_cond = engine.states_where(lambda st: st.terminal == "succeeded")
    async with db.session() as s:
        if done_cond is None:
            open_query = select(WorkItem).where(in_project)
            done_items, done_total = [], 0
        else:
            open_query = select(WorkItem).where(in_project, or_(WorkItem.state.is_(None), not_(done_cond)))
            done_total = await s.scalar(select(func.count()).select_from(WorkItem).where(in_project, done_cond))
            done_items = (
                await s.scalars(
                    select(WorkItem).where(in_project, done_cond).order_by(WorkItem.updated_at.desc()).limit(done_limit)
                )
            ).all()
        open_items = (await s.scalars(open_query.order_by(WorkItem.updated_at.desc()))).all()
        dependents = await _dependents(s, project.id)
        blockers = await engine.load_blockers(s, (i for i in open_items if i.state is None))
        runs, live = await _preload_runs(engine, s, open_items)
        spending = await project_spending(s, project.id)
        proposed = await s.scalar(
            select(func.count()).select_from(Note).where(Note.project_id == project.id, Note.status == "proposed")
        )
        cursor = await _last_event_id(s, project.id)
        for item in [*open_items, *done_items]:
            out = await _item_with_progress(engine, s, item, dependents, blockers, runs, live, spending)
            columns[out.column].append(out)
    columns["done"].sort(key=lambda o: o.updated_at, reverse=True)
    response.headers["X-Done-Total"] = str(done_total)
    response.headers["X-Proposed-Notes"] = str(proposed)
    response.headers["X-Events-Cursor"] = str(cursor)
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


EVENTS_WAIT_MAX = 300
EVENTS_POLL_SECONDS = 1.0
EVENTS_LIMIT = 200


async def _last_event_id(s, project_id: str) -> int:
    return await s.scalar(
        select(func.coalesce(func.max(ItemEvent.id), 0))
        .join(WorkItem, WorkItem.id == ItemEvent.item_id)
        .where(WorkItem.project_id == project_id)
    )


def _event_out(engine: Engine, event: ItemEvent, item: WorkItem) -> TransitionOut:
    wf = engine.workflows.get(item.workflow)
    st = wf.states.get(event.to_state) if wf else None
    if event.to_state == BACKLOG and st is None:
        kind = "backlog"
    else:
        kind = st.kind if st else "unknown"
    return TransitionOut(
        id=event.id, item_id=item.id, title=item.title, from_state=event.from_state, to_state=event.to_state,
        verdict=event.verdict, actor=event.actor, at=event.at, kind=kind, outcome=st.terminal if st else None,
    )


@router.get("/projects/{slug}/events")
async def events(
    slug: str,
    db: Db,
    engine: Eng,
    after: Annotated[int | None, Query(ge=0, description="Cursor from a previous call or the board")] = None,
    wait: Annotated[float, Query(ge=0, le=EVENTS_WAIT_MAX, description="Seconds to wait for a matching event")] = 0,
    items: Annotated[list[str], Query(description="Only these items (default: all of the project)")] = [],
    kinds: Annotated[
        list[Literal["agent", "action", "check", "human", "terminal", "backlog"]],
        Query(description="Only transitions into states of these kinds (default: all)"),
    ] = [],
) -> TransitionsOut:
    """State transitions of the project's items after the cursor `after`, oldest first.
    Without `after`, starts from now: returns no past events, only ones that happen while waiting.
    With `wait`, holds the request until a matching event arrives or `wait` seconds pass.
    The returned cursor moves past every event looked at, including ones the filters left out."""
    project = await _project(db, slug)
    deadline = time.monotonic() + wait
    async with db.session() as s:
        cursor = after if after is not None else await _last_event_id(s, project.id)
    while True:
        async with db.session() as s:
            query = (
                select(ItemEvent, WorkItem)
                .join(WorkItem, WorkItem.id == ItemEvent.item_id)
                .where(WorkItem.project_id == project.id, ItemEvent.id > cursor)
                .order_by(ItemEvent.id)
                .limit(EVENTS_LIMIT)
            )
            if items:
                query = query.where(ItemEvent.item_id.in_(items))
            rows = (await s.execute(query)).all()
        found = []
        for event, item in rows:
            cursor = event.id
            out = _event_out(engine, event, item)
            if not kinds or out.kind in kinds:
                found.append(out)
        if found or time.monotonic() >= deadline:
            return TransitionsOut(cursor=cursor, events=found)
        await asyncio.sleep(min(EVENTS_POLL_SECONDS, max(0.0, deadline - time.monotonic())))


@router.get("/projects/{slug}/forecast")
async def cost_forecast(
    slug: str,
    db: Db,
    engine: Eng,
    stories: Annotated[int, Query(ge=0, le=1000, description="New stories about to be planned")] = 0,
) -> dict:
    """What `stories` new stories will likely cost, from what the project's succeeded stories cost
    (all projects' while it has fewer than three), and how that fits the project budget next to
    what its open stories will likely still spend."""
    project = await _project(db, slug)
    succeeded = engine.states_where(lambda st: st.terminal == "succeeded")
    open_cond = engine.states_where(lambda st: st.terminal is None)
    async with db.session() as s:
        is_open = WorkItem.state.is_(None) if open_cond is None else or_(WorkItem.state.is_(None), open_cond)
        open_items = (
            await s.scalars(select(WorkItem).where(WorkItem.project_id == project.id, WorkItem.kind != "epic", is_open))
        ).all()
        return await forecast(s, project, succeeded, list(open_items), stories, settings.default_max_budget_usd)
