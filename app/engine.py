"""Workflow engine: moves work items through their workflow's states.

All transitions go through `_enter`, which is a compare-and-set on `work_items.version`.
Every step, human request and answer is tied to the version it was created for, so
duplicates (Celery redelivery, double clicks, a reconciler racing a slow worker) and
stale ones (the item was cancelled meanwhile) are no-ops instead of double moves.

Agent and action states are executed by workers (`steps.run_step`), which report the
verdict back through `advance`. Human states open a request in the inbox; `answer`
closes it and advances with the chosen option.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from .config import settings
from .db import Artifact, Database, HumanRequest, ItemEvent, Run, WorkItem, utcnow
from .store import TaskStore
from .workflow import Workflow

log = logging.getLogger(__name__)

Enqueue = Callable[[str, int], Awaitable[None]]


class EngineError(ValueError):
    pass


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
    ):
        self.db = db
        self.workflows = workflows
        self.store = store
        self.enqueue = enqueue

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
            return await self._enter(s, item, wf, wf.initial, None, actor)

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

    async def reconcile(self) -> list[str]:
        """Re-enqueue worker steps that made no progress: the enqueue was lost, or the worker
        died after its agent task finished but before it reported the verdict. Safe to
        repeat: `run_step` resumes from the step's existing run."""
        cutoff = utcnow() - timedelta(seconds=settings.step_stale_seconds)
        requeued = []
        async with self.db.session() as s:
            items = (await s.scalars(select(WorkItem).where(WorkItem.state.is_not(None)))).all()
            for item in items:
                wf = self.workflows.get(item.workflow)
                if wf is None or not wf.states[item.state].runs_on_worker:
                    continue
                run = await s.scalar(select(Run).where(Run.item_id == item.id, Run.version == item.version))
                if run is None:
                    stale = _aware(item.updated_at) < cutoff
                else:
                    raw = await self.store.get_raw(run.task_id)
                    stale = raw is None or raw["status"] != "running" or not await self.store.lease_alive(run.task_id)
                # At most one re-enqueue per stale period, so a step that merely waits
                # behind busy workers doesn't pile up duplicates in the queue.
                key = f"harness:requeue:{item.id}:{item.version}"
                if stale and await self.store.r.set(key, "1", nx=True, ex=settings.step_stale_seconds):
                    requeued.append(item.id)
                    await self.enqueue(item.id, item.version)
        return requeued

    async def _enter(self, s, item: WorkItem, wf: Workflow, target: str, verdict: str | None, actor: str) -> bool:
        st = wf.states[target]
        if st.max_visits is not None:
            visits = await s.scalar(
                select(func.count()).select_from(ItemEvent).where(
                    ItemEvent.item_id == item.id, ItemEvent.to_state == target
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

        if st.runs_on_worker:
            await self.enqueue(item.id, version + 1)
        return True


async def _get(s, item_id: str) -> WorkItem:
    item = await s.get(WorkItem, item_id)
    if item is None:
        raise EngineError("item not found")
    return item


def _aware(dt: datetime) -> datetime:
    # SQLite hands timestamps back naive; Postgres keeps the tz.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
