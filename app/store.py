"""Redis-backed task state and event log.

Celery owns the queue and delivery; this store owns what Celery doesn't model:
agent-specific task fields, a replayable event log for SSE clients, cooperative
cancellation and a lease used to detect redelivery after a worker crash.

Layout:
  harness:task:{id}          hash   — task spec + status + result fields
  harness:task:{id}:events   stream — agent events
  harness:task:{id}:lease    string — owning worker, TTL refreshed by the task's watchdog
  harness:tasks              zset   — all task ids scored by creation time
  harness:tasks:expiring     zset   — finished task ids scored by when Redis drops them; swept out of the index
  harness:running            set    — ids currently in `running`, scanned by the orphan reaper
  harness:session:{id}:cost  string — a Claude session's running cost total, to price a resumed run
"""

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from .config import settings
from .models import TaskCreate, TaskInfo, TaskStatus

TASKS_INDEX = "harness:tasks"
EXPIRING = "harness:tasks:expiring"
RUNNING = "harness:running"
# Sessions are resumed across steps of a long-lived item (rework after review, CI, merge).
SESSION_COST_TTL = 30 * 24 * 3600


def task_key(task_id: str) -> str:
    return f"harness:task:{task_id}"


def events_key(task_id: str) -> str:
    return f"harness:task:{task_id}:events"


def lease_key(task_id: str) -> str:
    return f"harness:task:{task_id}:lease"


def session_cost_key(session_id: str) -> str:
    return f"harness:session:{session_id}:cost"


def describe_tool(name: str, args: dict[str, Any]) -> str:
    """One line for the board: which tool, on what."""
    for key in ("command", "file_path", "pattern", "path", "url", "description"):
        if isinstance(args.get(key), str):
            return f"{name}: {args[key].splitlines()[0][:120]}" if args[key] else name
    return name


def _now() -> str:
    return datetime.now(UTC).isoformat()


# Atomically move a task to a new status only if it is currently in one of the
# allowed statuses. Guards against races between API cancel, worker claim and
# a redelivered duplicate of the same Celery message.
# ARGV: to_status, n_from, from_1..from_n, field_1, value_1, ...
_TRANSITION_LUA = """
local cur = redis.call('HGET', KEYS[1], 'status')
if not cur then return -1 end
local n = tonumber(ARGV[2])
for i = 3, 2 + n do
  if ARGV[i] == cur then
    redis.call('HSET', KEYS[1], 'status', ARGV[1])
    for j = 3 + n, #ARGV, 2 do
      redis.call('HSET', KEYS[1], ARGV[j], ARGV[j + 1])
    end
    return 1
  end
end
return 0
"""


class TaskStore:
    def __init__(self, redis: Redis):
        self.r = redis
        self._transition = redis.register_script(_TRANSITION_LUA)

    async def transition(
        self, task_id: str, from_: list[TaskStatus], to: TaskStatus, **fields: Any
    ) -> bool:
        flat: list[str] = []
        for k, v in fields.items():
            if v is not None:
                flat += [k, v if isinstance(v, str) else json.dumps(v)]
        res = await self._transition(
            keys=[task_key(task_id)], args=[to.value, len(from_), *[s.value for s in from_], *flat]
        )
        return res == 1

    # --- create / read -------------------------------------------------------

    async def create(self, spec: TaskCreate) -> str:
        task_id = uuid.uuid4().hex
        async with self.r.pipeline(transaction=True) as p:
            p.hset(
                task_key(task_id),
                mapping={
                    "id": task_id,
                    "status": TaskStatus.queued.value,
                    "spec": spec.model_dump_json(),
                    "created_at": _now(),
                },
            )
            p.zadd(TASKS_INDEX, {task_id: time.time()})
            await p.execute()
        await self._sweep_index()
        await self.append_event(task_id, "status", {"status": TaskStatus.queued.value})
        return task_id

    async def get_raw(self, task_id: str) -> dict[str, str] | None:
        data = await self.r.hgetall(task_key(task_id))
        return data or None

    async def get(self, task_id: str) -> TaskInfo | None:
        raw = await self.get_raw(task_id)
        if raw is None:
            return None
        spec = TaskCreate.model_validate_json(raw["spec"])
        return TaskInfo(
            id=raw["id"],
            status=TaskStatus(raw["status"]),
            prompt=spec.prompt or (f"{spec.issue.repo}#{spec.issue.number}" if spec.issue else ""),
            created_at=raw["created_at"],
            started_at=raw.get("started_at"),
            finished_at=raw.get("finished_at"),
            workspace=raw.get("workspace"),
            session_id=raw.get("session_id"),
            parent_task_id=spec.parent_task_id,
            result=raw.get("result"),
            structured_output=json.loads(raw["structured_output"]) if raw.get("structured_output") else None,
            error=raw.get("error"),
            failure_reason=raw.get("failure_reason"),
            cost_usd=float(raw["cost_usd"]) if raw.get("cost_usd") else None,
            num_turns=int(raw["num_turns"]) if raw.get("num_turns") else None,
            branch=raw.get("branch"),
            pr_url=raw.get("pr_url"),
            metadata=spec.metadata,
        )

    async def get_spec(self, task_id: str) -> TaskCreate:
        raw = await self.r.hget(task_key(task_id), "spec")
        return TaskCreate.model_validate_json(raw)

    async def list_tasks(self, limit: int = 50, status: TaskStatus | None = None) -> list[TaskInfo]:
        # Newest first; status filter is applied client-side — fine at harness scale.
        ids = await self.r.zrevrange(TASKS_INDEX, 0, (limit * 5 if status else limit) - 1)
        out: list[TaskInfo] = []
        for task_id in ids:
            info = await self.get(task_id)
            if info and (status is None or info.status == status):
                out.append(info)
            if len(out) >= limit:
                break
        return out

    async def set_fields(self, task_id: str, **fields: str) -> None:
        await self.r.hset(task_key(task_id), mapping=fields)

    async def progress(self, task_id: str) -> dict[str, Any] | None:
        """Live view of a run for the board: turn, current activity, the agent's todo list."""
        raw = await self.get_raw(task_id)
        if raw is None:
            return None
        spec = TaskCreate.model_validate_json(raw["spec"])
        activity = raw.get("activity")
        if activity is None:
            # Runs started by a worker that doesn't record activity yet: read the event log.
            for _id, fields in await self.r.xrevrange(events_key(task_id), count=50):
                if fields["type"] == "tool_use":
                    data = json.loads(fields["data"])
                    activity = describe_tool(data["name"], data.get("input") or {})
                    break
        return {
            "status": raw["status"],
            "started_at": raw.get("started_at"),
            "turn": int(raw["turn"]) if raw.get("turn") else None,
            "max_turns": spec.max_turns or settings.default_max_turns,
            "activity": activity,
            "todos": json.loads(raw["todos"]) if raw.get("todos") else None,
        }

    # --- events --------------------------------------------------------------

    async def append_event(self, task_id: str, type_: str, data: dict[str, Any]) -> str:
        return await self.r.xadd(
            events_key(task_id),
            {"type": type_, "data": json.dumps(data, default=str)},
            maxlen=settings.events_maxlen,
            approximate=True,
        )

    async def read_events(
        self, task_id: str, after: str = "0", count: int = 500, block_ms: int | None = None
    ) -> list[tuple[str, str, dict[str, Any]]]:
        resp = await self.r.xread({events_key(task_id): after}, count=count, block=block_ms)
        events = []
        for _stream, entries in resp or []:
            for entry_id, fields in entries:
                events.append((entry_id, fields["type"], json.loads(fields["data"])))
        return events

    # --- lifecycle -----------------------------------------------------------

    async def claim(self, task_id: str, worker_id: str) -> bool:
        ok = await self.transition(
            task_id, [TaskStatus.queued], TaskStatus.running, started_at=_now(), worker=worker_id
        )
        if ok:
            # Lease before joining RUNNING so the reaper never sees a lease-less claim.
            await self.renew_lease(task_id, worker_id)
            await self.r.sadd(RUNNING, task_id)
            await self.append_event(task_id, "status", {"status": TaskStatus.running.value})
        return ok

    async def renew_lease(self, task_id: str, worker_id: str) -> None:
        await self.r.set(lease_key(task_id), worker_id, ex=settings.lease_ttl_seconds)

    async def lease_alive(self, task_id: str) -> bool:
        return bool(await self.r.exists(lease_key(task_id)))

    async def session_cost(self, session_id: str) -> float:
        """What the SDK last reported as the session's total cost; 0 for an unknown session."""
        value = await self.r.get(session_cost_key(session_id))
        return float(value) if value else 0.0

    async def set_session_cost(self, session_id: str, total_usd: float) -> None:
        await self.r.set(session_cost_key(session_id), total_usd, ex=SESSION_COST_TTL)

    async def finish(self, task_id: str, status: TaskStatus, **fields: Any) -> bool:
        assert status.is_terminal
        ok = await self.transition(
            task_id, [TaskStatus.running], status, finished_at=_now(), **fields
        )
        if ok:
            event = {"status": status.value} | {
                k: v for k, v in fields.items() if k in ("error", "failure_reason") and v is not None
            }
            await self.append_event(task_id, "status", event)
            await self._expire(task_id)
        return ok

    async def _expire(self, task_id: str) -> None:
        ttl = settings.result_ttl_seconds
        async with self.r.pipeline(transaction=False) as p:
            p.delete(lease_key(task_id))
            p.srem(RUNNING, task_id)
            p.expire(task_key(task_id), ttl)
            p.expire(events_key(task_id), ttl)
            p.zadd(EXPIRING, {task_id: time.time() + ttl})
            await p.execute()

    async def _sweep_index(self) -> None:
        """Drop ids from the index once Redis has expired their task (the keys go by TTL, the index doesn't)."""
        gone = await self.r.zrangebyscore(EXPIRING, "-inf", time.time())
        if gone:
            async with self.r.pipeline(transaction=True) as p:
                p.zrem(TASKS_INDEX, *gone)
                p.zrem(EXPIRING, *gone)
                await p.execute()

    async def prune_orphaned_index(self) -> int:
        """Drop index ids whose task hash is gone: the leftovers of expiries before `EXPIRING` existed."""
        removed = 0
        async for task_id in self.r.zscan_iter(TASKS_INDEX):
            if not await self.r.exists(task_key(task_id[0])):
                removed += await self.r.zrem(TASKS_INDEX, task_id[0])
        return removed

    async def request_cancel(self, task_id: str) -> TaskStatus | None:
        """Queued tasks are cancelled immediately (a worker that later receives the
        message fails to claim it). Running tasks get a flag the worker's watchdog polls."""
        if await self.transition(
            task_id, [TaskStatus.queued], TaskStatus.cancelled, finished_at=_now()
        ):
            await self.append_event(task_id, "status", {"status": TaskStatus.cancelled.value})
            await self._expire(task_id)
            return TaskStatus.cancelled
        raw = await self.get_raw(task_id)
        if raw is None:
            return None
        status = TaskStatus(raw["status"])
        if status == TaskStatus.running:
            await self.r.hset(task_key(task_id), "cancel_requested", "1")
        return status

    async def running_ids(self) -> set[str]:
        return await self.r.smembers(RUNNING)

    async def forget_running(self, task_id: str) -> None:
        await self.r.srem(RUNNING, task_id)

    async def cancel_requested(self, task_id: str) -> bool:
        return await self.r.hget(task_key(task_id), "cancel_requested") == "1"
