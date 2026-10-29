"""Copies a finished harness task from Redis into Postgres, so a run's transcript
outlives `result_ttl_seconds`.

Redis is the live source: the task API reads it first and falls back to the archive
only once Redis has expired the task. Archiving is idempotent: a step re-run after a
crash archives what an earlier delivery may already have stored, and nothing breaks.
"""

from typing import Any

from sqlalchemy.exc import IntegrityError

from .db import Database, TaskTranscript
from .store import TaskStore

PAGE = 1000


async def archive_task(db: Database, store: TaskStore, task_id: str, run_id: str | None = None) -> bool:
    """Store the task and its whole event stream; False if already archived or not finished."""
    async with db.session() as s:
        if await s.get(TaskTranscript, task_id) is not None:
            return False
    info = await store.get(task_id)
    if info is None or not info.status.is_terminal:
        return False
    events: list[dict[str, Any]] = []
    after = "0"
    while page := await store.read_events(task_id, after, count=PAGE):
        events += [{"id": i, "type": t, "data": d} for i, t, d in page]
        after = page[-1][0]
    async with db.session() as s:
        s.add(TaskTranscript(task_id=task_id, run_id=run_id, task=info.model_dump(mode="json"), events=events))
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            return False  # a concurrent delivery archived it first
    return True


async def load_transcript(db: Database, task_id: str) -> TaskTranscript | None:
    async with db.session() as s:
        return await s.get(TaskTranscript, task_id)


def _stream_id(event_id: str) -> tuple[int, int]:
    ms, _, seq = event_id.partition("-")
    return int(ms), int(seq or 0)


def events_after(events: list[dict[str, Any]], after: str = "0", limit: int | None = None) -> list[dict[str, Any]]:
    """Archived events with a stream id greater than `after`, as XREAD would return them.
    Raises ValueError for an id that is not `ms` or `ms-seq`."""
    cursor = _stream_id(after)
    out = [e for e in events if _stream_id(e["id"]) > cursor]
    return out if limit is None else out[:limit]
