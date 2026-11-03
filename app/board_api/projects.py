"""Projects, their items, the Kanban board and the inbox; GitHub App and model lookups."""

from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, not_, or_, select
from sqlalchemy.exc import IntegrityError

from .. import github_app
from ..budget import project_spending
from ..config import settings
from ..db import HumanRequest, Note, Project, WorkItem
from ..engine import EngineError
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
from .schemas import Column, ImportResult, ItemIn, ItemOut, ProjectIn, ProjectOut, ProjectPatch, RequestOut

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
        if body.parent_id:
            parent = await s.get(WorkItem, body.parent_id)
            if parent is None or parent.project_id != project.id:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "parent not found in this project")
        blocked_by = await _check_blockers(s, None, project.id, body.blocked_by)
        item = WorkItem(
            project_id=project.id,
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
            source={"issue": body.issue} if body.issue else {},
        )
        if body.start and (waiting := await engine.unfinished_blockers(s, item)):
            # Refused before anything is stored, so no half-created item is left behind.
            raise HTTPException(status.HTTP_409_CONFLICT, f"blocked by: {', '.join(waiting)}")
        s.add(item)
        await s.commit()
    if body.start:
        await engine.start(item.id)
    return await _get_item_out(db, engine, item.id)


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
                source={"issue": issue["number"]},
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
        for item in [*open_items, *done_items]:
            out = await _item_with_progress(engine, s, item, dependents, blockers, runs, live, spending)
            columns[out.column].append(out)
    columns["done"].sort(key=lambda o: o.updated_at, reverse=True)
    response.headers["X-Done-Total"] = str(done_total)
    response.headers["X-Proposed-Notes"] = str(proposed)
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
