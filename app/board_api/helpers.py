"""Building the board's responses from the database: columns, progress, attention, blockers."""

import asyncio
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select

from ..budget import Spending, project_spending
from ..db import Artifact, Database, ItemEvent, Project, Run, WorkItem
from ..engine import Engine
from .schemas import Column, ItemOut, ProjectOut

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
    source = item.source or {}
    return ItemOut.model_validate(item).model_copy(
        update={
            "column": _column(engine, item),
            "issue": source.get("issue"),
            "author": source.get("author"),
            "external": bool(source.get("external")) and not source.get("external_confirmed"),
        }
    )


async def _progress(
    engine: Engine,
    s,
    item: WorkItem,
    runs: dict[tuple[str, int], Run] | None = None,
    live: dict[str, dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    """Agent step: its live run (turn, activity, todos) or that it waits for a scheduler slot.
    Action and check steps: just which one is running. `runs` (by item id and version) and
    `live` (store progress by task id) are preloaded by the board; without them they are read here."""
    wf = engine.workflows.get(item.workflow)
    st = wf.states.get(item.state) if wf and item.state else None
    if st is None or not (st.agent or st.action or st.check):
        return None
    if not st.agent:
        return {"step": st.action or st.check, "kind": st.kind}
    if runs is None:
        run = await s.scalar(select(Run).where(Run.item_id == item.id, Run.version == item.version))
    else:
        run = runs.get((item.id, item.version))
    if run is None:
        queued = bool(engine.scheduler and await engine.scheduler.is_pending(item.id, item.version))
        return {"step": st.agent, "kind": "agent", "queued": queued}
    progress = await engine.store.progress(run.task_id) if live is None else live.get(run.task_id)
    return {"step": st.agent, "kind": "agent", "task_id": run.task_id, **(progress or {})}


_OPEN_QUESTIONS = re.compile(r"^#{1,6}\s*open questions\s*:?\s*$", re.I | re.M)
_NO_QUESTIONS = {"", "none", "none.", "n/a", "-", "no", "no open questions"}


def _open_questions(text: str) -> str | None:
    """The body of an 'Open questions' section of an agent summary, if it lists anything."""
    m = _OPEN_QUESTIONS.search(text)
    if m is None:
        return None
    body = re.split(r"^#{1,6}\s", text[m.end():], maxsplit=1, flags=re.M)[0].strip()
    return body if body.lower().strip("*_ ") not in _NO_QUESTIONS else None


async def _attention(engine: Engine, s, item: WorkItem) -> dict[str, str] | None:
    """Why an item waits for a human. `pr` while its PR waits for the merge; otherwise from the
    newest thing the workflow produced: the error of a step that failed (even if an earlier run
    of it succeeded), or questions an agent left in its summary; failing that, how the item got
    here: `exhausted` (a step hit its max_visits), or the verdict that led here (`timeout`,
    `too_big`, `needs_clarification`, a reviewer's `needs_human`, ...)."""
    wf = engine.workflows.get(item.workflow)
    st = wf.states.get(item.state) if wf and item.state else None
    if st is not None and st.watch == "merge":
        return {"kind": "pr", "text": item.pr_url or ""}
    latest = await s.scalar(
        select(Artifact)
        .where(Artifact.item_id == item.id, ~Artifact.name.startswith("human:"))
        .order_by(Artifact.created_at.desc())
        .limit(1)
    )
    if latest is not None and latest.name == "error":
        return {"kind": "error", "text": latest.content}
    if latest is not None and (questions := _open_questions(latest.content)):
        return {"kind": "questions", "text": questions, "from": latest.name}
    entered = await s.scalar(
        select(ItemEvent)
        .where(ItemEvent.item_id == item.id, ItemEvent.to_state == item.state)
        .order_by(ItemEvent.id.desc())
        .limit(1)
    )
    if entered is None or not entered.verdict:
        return None
    came_from = wf.states.get(entered.from_state) if wf and entered.from_state else None
    if came_from is not None and came_from.next.get(entered.verdict) not in (None, item.state):
        target = came_from.next[entered.verdict]
        return {"kind": "exhausted", "text": f"{target} ran its maximum number of times", "from": target}
    return {"kind": entered.verdict, "text": "", "from": entered.from_state or ""}


async def _dependents(s, project_id: str) -> dict[str, list[str]]:
    """For every item of the project, the ids of the backlog items it blocks."""
    rows = await s.execute(
        select(WorkItem.id, WorkItem.blocked_by).where(WorkItem.project_id == project_id, WorkItem.state.is_(None))
    )
    deps: dict[str, list[str]] = {}
    for dependent_id, blockers in rows:
        for blocker_id in blockers or []:
            deps.setdefault(blocker_id, []).append(dependent_id)
    return deps


async def _item_with_progress(
    engine: Engine,
    s,
    item: WorkItem,
    dependents: dict[str, list[str]] | None = None,
    blockers: dict[str, WorkItem] | None = None,
    runs: dict[tuple[str, int], Run] | None = None,
    live: dict[str, dict[str, Any] | None] | None = None,
    spending: Spending | None = None,
) -> ItemOut:
    out = _item_out(engine, item)
    if spending is None:
        spending = await project_spending(s, item.project_id)
    out.spent_usd = spending.item(item.id)
    if dependents is None:
        dependents = await _dependents(s, item.project_id)
    out.blocking = dependents.get(item.id, [])
    if out.column == "backlog":
        out.blocked = await engine.unfinished_blockers(s, item, blockers)
        out.ready = bool(item.blocked_by) and not out.blocked
    if out.column == "working":
        out.progress = await _progress(engine, s, item, runs, live)
    elif out.column == "needs_human":
        out.attention = await _attention(engine, s, item)
    return out


async def _project_out(s, project: Project) -> ProjectOut:
    spent = (
        await s.scalar(
            select(func.coalesce(func.sum(Run.cost_usd), 0.0))
            .join(WorkItem, WorkItem.id == Run.item_id)
            .where(WorkItem.project_id == project.id)
        )
        or 0.0
    )
    return ProjectOut.model_validate(project).model_copy(update={"spent_usd": float(spent)})


async def _check_blockers(s, item_id: str | None, project_id: str, blocked_by: list[str]) -> list[str]:
    """Validated, de-duplicated blocker ids: they exist in the project, none is the item itself,
    and following their own blockers never leads back to the item."""
    ids = list(dict.fromkeys(blocked_by))

    def refuse(why: str) -> HTTPException:
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, why)

    if item_id in ids:
        raise refuse("an item cannot block itself")
    rows = (await s.execute(select(WorkItem.id, WorkItem.project_id, WorkItem.blocked_by))).all() if ids else []
    graph = {r.id: r.blocked_by or [] for r in rows if r.project_id == project_id}
    if missing := [i for i in ids if i not in graph]:
        raise refuse(f"blocker not found in this project: {', '.join(missing)}")
    seen, todo = set(), list(ids)
    while todo:
        current = todo.pop()
        if current == item_id:
            raise refuse("blockers form a cycle")
        if current not in seen:
            seen.add(current)
            todo.extend(graph.get(current, []))
    return ids


async def _preload_runs(
    engine: Engine, s, items: list[WorkItem]
) -> tuple[dict[tuple[str, int], Run], dict[str, dict[str, Any] | None]]:
    """The runs of the items' current versions, and their live progress, for the agent steps
    among `items`: one query and concurrent store reads instead of a round trip per item."""
    ids = []
    for item in items:
        wf = engine.workflows.get(item.workflow)
        st = wf.states.get(item.state) if wf and item.state else None
        if st is not None and st.agent and not st.terminal and not st.human:
            ids.append(item.id)
    if not ids:
        return {}, {}
    versions = {i.id: i.version for i in items}
    rows = await s.scalars(select(Run).where(Run.item_id.in_(ids)))
    runs = {(r.item_id, r.version): r for r in rows if versions[r.item_id] == r.version}
    task_ids = [r.task_id for r in runs.values()]
    progress = await asyncio.gather(*(engine.store.progress(t) for t in task_ids))
    return runs, dict(zip(task_ids, progress))


async def _get_item_out(db: Database, engine: Engine, item_id: str) -> ItemOut:
    async with db.session() as s:
        item = await s.get(WorkItem, item_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
        return await _item_with_progress(engine, s, item)
