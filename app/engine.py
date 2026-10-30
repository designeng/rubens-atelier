"""Workflow engine: moves work items through their workflow's states.

All transitions go through `_enter`, which is a compare-and-set on `work_items.version`.
Every step, human request and answer is tied to the version it was created for, so
duplicates (Celery redelivery, double clicks, a reconciler racing a slow worker) and
stale ones (the item was cancelled meanwhile) are no-ops instead of double moves.

Agent and action states are executed by workers (`steps.run_step`), which report the
verdict back through `advance`; agent steps first wait for a slot in the scheduler.
Check states are polled by `checks.poll_checks`. Human states open a request in the
inbox; `answer` closes it and advances with the chosen option.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import timedelta

from sqlalchemy import ColumnElement, and_, delete, func, or_, select, update

from .config import settings
from .db import Artifact, Database, HumanRequest, ItemEvent, Run, TaskTranscript, WorkItem, aware, utcnow
from .scheduler import Scheduler
from .store import TaskStore
from .workflow import State, Workflow

log = logging.getLogger(__name__)

BACKLOG = "backlog"  # to_state of the event that returns an item to the backlog

Enqueue = Callable[[str, int], Awaitable[None]]
Cleanup = Callable[[str], Awaitable[None]]


class EngineError(ValueError):
    pass


async def celery_cleanup(item_id: str) -> None:
    from .celery_app import cleanup_workspace

    await asyncio.to_thread(cleanup_workspace.apply_async, args=[item_id])


async def celery_enqueue(item_id: str, version: int) -> None:
    from .celery_app import step

    await asyncio.to_thread(step.apply_async, args=[item_id, version])


class Engine:
    def __init__(
        self,
        db: Database,
        workflows: dict[str, Workflow],
        store: TaskStore,
        enqueue: Enqueue = celery_enqueue,
        scheduler: Scheduler | None = None,
        cleanup: Cleanup = celery_cleanup,
    ):
        self.db = db
        self.workflows = workflows
        self.store = store
        self.enqueue = enqueue
        self.scheduler = scheduler
        self.cleanup = cleanup

    def workflow(self, name: str) -> Workflow:
        try:
            return self.workflows[name]
        except KeyError:
            raise EngineError(f"unknown workflow {name!r}") from None

    async def start(self, item_id: str, actor: str = "user") -> bool:
        async with self.db.session() as s:
            item = await _get(s, item_id)
            if item.state is not None:
                raise EngineError(f"item is already in state {item.state!r}")
            wf = self.workflow(item.workflow)
            if waiting := await self.unfinished_blockers(s, item):
                raise EngineError(f"blocked by: {', '.join(waiting)}")
            return await self._enter(s, item, wf, wf.initial, None, actor)

    def states_where(self, pred: Callable[[State], bool]) -> ColumnElement[bool] | None:
        """SQL condition: the item's (workflow, state) is a state of its workflow that satisfies
        `pred`. None if there is no such state. Items of unknown workflows or states never match."""
        clauses = [
            and_(WorkItem.workflow == wf.name, WorkItem.state.in_(names))
            for wf in self.workflows.values()
            if (names := [n for n, st in wf.states.items() if pred(st)])
        ]
        return or_(*clauses) if clauses else None

    async def unfinished_blockers(self, s, item: WorkItem, known: dict[str, WorkItem] | None = None) -> list[str]:
        """Titles (or ids, for blockers that no longer exist) of the blockers that have not succeeded.
        `known` holds already loaded blockers by id (see `load_blockers`) and saves a query per blocker."""
        waiting = []
        for blocker_id in item.blocked_by or []:
            blocker = known.get(blocker_id) if known is not None else await s.get(WorkItem, blocker_id)
            wf = self.workflows.get(blocker.workflow) if blocker else None
            st = wf.states.get(blocker.state) if wf and blocker.state else None
            if st is None or st.terminal != "succeeded":
                waiting.append(f"{blocker.title} ({blocker_id})" if blocker else blocker_id)
        return waiting

    async def load_blockers(self, s, items: Iterable[WorkItem]) -> dict[str, WorkItem]:
        """The blockers of all `items`, by id, in one query."""
        ids = {b for item in items for b in item.blocked_by or []}
        if not ids:
            return {}
        return {b.id: b for b in await s.scalars(select(WorkItem).where(WorkItem.id.in_(ids)))}

    async def advance(self, item_id: str, version: int, verdict: str, actor: str = "engine") -> bool:
        """Leave the item's current state with `verdict`. False if the item has moved on."""
        async with self.db.session() as s:
            item = await _get(s, item_id)
            if item.version != version or item.state is None:
                return False
            wf = self.workflow(item.workflow)
            if wf.states[item.state].terminal:
                return False
            return await self._enter(s, item, wf, wf.decide(item.state, verdict), verdict, actor)

    async def answer(self, request_id: str, option: str, comment: str = "", actor: str = "user") -> bool:
        async with self.db.session() as s:
            req = await s.get(HumanRequest, request_id)
            if req is None:
                raise EngineError("request not found")
            if req.answer is not None:
                raise EngineError(f"request was already answered: {req.answer}")
            if option not in req.options:
                raise EngineError(f"option must be one of {req.options}")
            req.answer, req.comment, req.answered_by, req.answered_at = option, comment, actor, utcnow()
            if comment.strip():
                s.add(Artifact(item_id=req.item_id, name=f"human:{req.state}", content=f"{option}: {comment}"))
            await s.commit()
        return await self.advance(req.item_id, req.version, option, actor)

    async def cancel(self, item_id: str, actor: str = "user") -> bool:
        async with self.db.session() as s:
            item = await _get(s, item_id)
            wf = self.workflow(item.workflow)
            if item.state is not None and wf.states[item.state].terminal:
                return False
            running = await s.scalars(select(Run.task_id).where(Run.item_id == item_id, Run.status == "running"))
            task_ids = list(running)
            ok = await self._enter(s, item, wf, wf.cancelled_state, "cancel", actor)
        for task_id in task_ids:
            await self.store.request_cancel(task_id)
        return ok

    async def reopen(self, item_id: str, actor: str = "user") -> bool:
        """Move a failed or cancelled item back to the backlog so it can be edited and started
        again. The branch and workspace are kept; the visit limits of the steps start over."""
        async with self.db.session() as s:
            item = await _get(s, item_id)
            wf = self.workflow(item.workflow)
            if item.state is None:
                raise EngineError("item is already in the backlog")
            if wf.states[item.state].terminal not in ("failed", "cancelled"):
                raise EngineError(f"only failed or cancelled items can go back to the backlog (state {item.state!r})")
            res = await s.execute(
                update(WorkItem)
                .where(WorkItem.id == item.id, WorkItem.version == item.version)
                .values(state=None, version=item.version + 1, updated_at=utcnow())
                .execution_options(synchronize_session=False)
            )
            if res.rowcount != 1:
                await s.rollback()
                return False
            s.add(ItemEvent(item_id=item.id, from_state=item.state, to_state=BACKLOG, verdict="reopen", actor=actor))
            await s.commit()
        return True

    async def delete(self, item_id: str) -> None:
        """Remove a failed or cancelled item with its runs, artifacts, requests and events.
        Child stories lose their parent link. The branch, workspace and issue are kept."""
        async with self.db.session() as s:
            item = await _get(s, item_id)
            wf = self.workflow(item.workflow)
            if item.state is None or wf.states[item.state].terminal not in ("failed", "cancelled"):
                raise EngineError(f"only failed or cancelled items can be deleted (state {item.state!r})")
            run_ids = select(Run.id).where(Run.item_id == item_id)
            await s.execute(delete(TaskTranscript).where(TaskTranscript.run_id.in_(run_ids)))
            for model in (Artifact, HumanRequest, ItemEvent, Run):
                await s.execute(delete(model).where(model.item_id == item_id))
            await s.execute(update(WorkItem).where(WorkItem.parent_id == item_id).values(parent_id=None))
            await s.delete(item)
            await s.commit()

    async def run_action(self, item_id: str, action: str, actor: str = "user") -> bool:
        """Manually send a running item to the workflow's state that runs `action` (e.g. open_pr),
        skipping the steps in between. Whatever agent is running is cancelled."""
        async with self.db.session() as s:
            item = await _get(s, item_id)
            wf = self.workflow(item.workflow)
            if item.state is None or wf.states[item.state].terminal:
                raise EngineError(f"item is not in progress (state {item.state!r})")
            target = next((n for n, st in wf.states.items() if st.action == action), None)
            if target is None:
                raise EngineError(f"workflow {wf.name!r} has no {action!r} step")
            if item.state == target:
                raise EngineError(f"item is already in {target!r}")
            if not item.branch:
                raise EngineError("nothing to publish yet: the story has no branch")
            running = await s.scalars(select(Run.task_id).where(Run.item_id == item_id, Run.status == "running"))
            task_ids = list(running)
            ok = await self._enter(s, item, wf, target, "manual", actor)
        if ok:
            for task_id in task_ids:
                await self.store.request_cancel(task_id)
        return ok

    async def reconcile(self) -> list[str]:
        """Re-enqueue worker steps that made no progress: the enqueue was lost, or the worker
        died after its agent task finished but before it reported the verdict. Safe to
        repeat: `run_step` resumes from the step's existing run."""
        cutoff = utcnow() - timedelta(seconds=settings.step_stale_seconds)
        requeued = []
        async with self.db.session() as s:
            active = self.states_where(lambda st: not st.terminal and st.runs_on_worker)
            if active is None:
                return requeued
            items = (await s.scalars(select(WorkItem).where(active))).all()
            for item in items:
                wf = self.workflows.get(item.workflow)
                st = wf.states[item.state] if wf else None
                if st is None or not st.runs_on_worker:
                    continue
                if st.agent and self.scheduler and await self.scheduler.is_pending(item.id, item.version):
                    continue  # waiting for a slot, or holding one: the scheduler accounts for it
                run = await s.scalar(select(Run).where(Run.item_id == item.id, Run.version == item.version))
                if run is None:
                    stale = aware(item.updated_at) < cutoff
                else:
                    raw = await self.store.get_raw(run.task_id)
                    stale = raw is None or raw["status"] != "running" or not await self.store.lease_alive(run.task_id)
                # At most one re-enqueue per stale period, so a step that merely waits
                # behind busy workers doesn't pile up duplicates in the queue.
                key = f"harness:requeue:{item.id}:{item.version}"
                if stale and await self.store.r.set(key, "1", nx=True, ex=settings.step_stale_seconds):
                    requeued.append(item.id)
                    await self._schedule(item, st, item.version)
        return requeued

    async def _enter(self, s, item: WorkItem, wf: Workflow, target: str, verdict: str | None, actor: str) -> bool:
        st = wf.states[target]
        if st.max_visits is not None:
            # An answer in the state an exhausted step escalates to (or a reopen) starts the count
            # over, otherwise a retry would bounce straight back there. Other human states
            # (clarify, awaiting_merge) keep counting, so a loop through a person stays bounded.
            escalations = {other.on_exhausted for other in wf.states.values() if other.max_visits is not None}
            reset = select(func.coalesce(func.max(ItemEvent.id), 0)).where(
                ItemEvent.item_id == item.id,
                or_(ItemEvent.to_state == BACKLOG, ItemEvent.from_state.in_(escalations)),
            ).scalar_subquery()
            visits = 0 if item.state in escalations else await s.scalar(
                select(func.count()).select_from(ItemEvent).where(
                    ItemEvent.item_id == item.id, ItemEvent.to_state == target, ItemEvent.id >= reset
                )
            )
            if visits >= st.max_visits:
                log.info("item %s visited %s %d times, going to %s", item.id, target, visits, st.on_exhausted)
                target, st = st.on_exhausted, wf.states[st.on_exhausted]

        version, from_state = item.version, item.state
        res = await s.execute(
            update(WorkItem)
            .where(WorkItem.id == item.id, WorkItem.version == version)
            .values(state=target, version=version + 1, updated_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            await s.rollback()
            return False
        s.add(ItemEvent(item_id=item.id, from_state=from_state, to_state=target, verdict=verdict, actor=actor))
        # Whatever the item was waiting for in its previous state is moot now.
        await s.execute(
            update(HumanRequest)
            .where(HumanRequest.item_id == item.id, HumanRequest.answer.is_(None))
            .values(answer="superseded", answered_by=actor, answered_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        if st.human:
            s.add(HumanRequest(item_id=item.id, version=version + 1, state=target, question=st.human, options=st.options))
        await s.commit()
        item.state, item.version = target, version + 1
        log.info("item %s: %s -[%s]-> %s", item.id, from_state, verdict, target)

        if self.scheduler:
            await self.scheduler.forget(item.id, version)
        if st.runs_on_worker:
            await self._schedule(item, st, version + 1)
        elif st.terminal == "succeeded":
            # The work is merged. Failed and cancelled items keep their workspace: reopen reuses it.
            await self._cleanup(item.id)
        return True

    async def is_succeeded(self, item_id: str) -> bool:
        async with self.db.session() as s:
            item = await s.get(WorkItem, item_id)
        wf = self.workflows.get(item.workflow) if item else None
        st = wf.states.get(item.state) if wf and item.state else None
        return st is not None and st.terminal == "succeeded"

    async def _cleanup(self, item_id: str) -> None:
        try:
            await self.cleanup(item_id)
        except Exception:
            log.exception("could not enqueue the workspace cleanup of item %s", item_id)

    async def _schedule(self, item: WorkItem, st, version: int) -> None:
        if st.agent and self.scheduler:
            await self.scheduler.submit(item.id, version, item.project_id, item.priority)
        else:
            await self.enqueue(item.id, version)


async def _get(s, item_id: str) -> WorkItem:
    item = await s.get(WorkItem, item_id)
    if item is None:
        raise EngineError("item not found")
    return item
