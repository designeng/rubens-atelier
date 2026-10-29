"""Scheduler: decides when a ready agent step may start, so the factory paces itself
under its Claude subscription limits instead of running into them.

Agent steps wait in a priority queue (higher item priority first, then FIFO) and are
dispatched to Celery only when a slot is free:
  - at most `max_agents` at once and `max_agents_per_project` per project (fairness);
  - fewer, or none, as subscription windows fill up. Claude Code reports each window's
    status and utilization (rate_limit events); a rejected window pauses dispatch until
    it resets, a nearly full one drops to a single agent, and weekly windows may not be
    used faster than the week elapses.
Action and check steps don't use Claude and bypass the scheduler.

Layout (members are "<item_id>:<version>"):
  harness:sched:ready            zset   member → score (priority, then enqueue time)
  harness:sched:ready:project    hash   member → project id
  harness:sched:active           zset   member → slot expiry (unix time)
  harness:sched:active:project   hash   member → project id
  harness:sched:active:task      hash   member → harness task id, once the step started it
  harness:sched:quota            hash   window type → latest rate-limit info (json)
  harness:sched:paused           string operator pause reason
"""

import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis

from .config import settings
from .models import TaskStatus
from .store import lease_key, task_key

READY = "harness:sched:ready"
READY_PROJECT = "harness:sched:ready:project"
ACTIVE = "harness:sched:active"
ACTIVE_PROJECT = "harness:sched:active:project"
ACTIVE_TASK = "harness:sched:active:task"
QUOTA = "harness:sched:quota"
PAUSED = "harness:sched:paused"
LOCK = "harness:sched:lock"

WEEK = 7 * 24 * 3600
# Priority dominates the score; enqueue time breaks ties (FIFO).
PRIORITY_WEIGHT = 1e10


def member(item_id: str, version: int) -> str:
    return f"{item_id}:{version}"


async def record_rate_limit(redis: Redis, info: dict[str, Any]) -> None:
    """Remember the latest status of a subscription window, as reported by Claude Code."""
    window = info.get("rate_limit_type") or "unknown"
    if window == "overage":
        return  # pay-as-you-go state; the subscription windows themselves drive pacing
    state = {k: info.get(k) for k in ("status", "utilization", "resets_at")} | {"at": time.time()}
    await redis.hset(QUOTA, window, json.dumps(state))


class Scheduler:
    def __init__(self, redis: Redis, enqueue: Callable[[str, int], Awaitable[None]]):
        self.r = redis
        self.enqueue = enqueue

    async def submit(self, item_id: str, version: int, project_id: str, priority: int = 0) -> None:
        m = member(item_id, version)
        async with self.r.pipeline(transaction=True) as p:
            p.zadd(READY, {m: -priority * PRIORITY_WEIGHT + time.time()}, nx=True)
            p.hset(READY_PROJECT, m, project_id)
            await p.execute()
        await self.dispatch()

    async def forget(self, item_id: str, version: int) -> None:
        """Drop a step that is still waiting; its item has moved on."""
        m = member(item_id, version)
        async with self.r.pipeline(transaction=True) as p:
            p.zrem(READY, m)
            p.hdel(READY_PROJECT, m)
            await p.execute()

    async def is_pending(self, item_id: str, version: int) -> bool:
        m = member(item_id, version)
        return await self.r.zscore(READY, m) is not None or await self.r.zscore(ACTIVE, m) is not None

    async def started(self, item_id: str, version: int, task_id: str) -> None:
        """The step's agent task exists: from now on the slot lives as long as the task's lease."""
        m = member(item_id, version)
        async with self.r.pipeline(transaction=True) as p:
            p.zadd(ACTIVE, {m: time.time() + settings.task_timeout_seconds + 600}, xx=True)
            p.hset(ACTIVE_TASK, m, task_id)
            await p.execute()

    async def release(self, item_id: str, version: int, dispatch: bool = True) -> None:
        await self._drop_active(member(item_id, version))
        if dispatch:
            await self.dispatch()

    async def dispatch(self) -> list[str]:
        """Move ready steps into free slots and hand them to the workers."""
        if not await self.r.set(LOCK, "1", nx=True, ex=30):
            return []  # another dispatcher is at it; it sees the same queue
        dispatched: list[str] = []
        try:
            await self._cleanup()
            capacity, _ = await self.capacity()
            active = await self.r.zrange(ACTIVE, 0, -1)
            free = capacity - len(active)
            per_project = Counter(await self.r.hmget(ACTIVE_PROJECT, active)) if active else Counter()
            for m in await self.r.zrange(READY, 0, -1) if free > 0 else []:
                project = await self.r.hget(READY_PROJECT, m)
                if per_project[project] >= settings.max_agents_per_project:
                    continue
                async with self.r.pipeline(transaction=True) as p:
                    p.zrem(READY, m)
                    p.hdel(READY_PROJECT, m)
                    p.zadd(ACTIVE, {m: time.time() + settings.dispatch_ttl_seconds})
                    p.hset(ACTIVE_PROJECT, m, project)
                    await p.execute()
                per_project[project] += 1
                dispatched.append(m)
                free -= 1
                if free == 0:
                    break
        finally:
            await self.r.delete(LOCK)
        for m in dispatched:
            item_id, version = m.rsplit(":", 1)
            await self.enqueue(item_id, int(version))
        return dispatched

    async def capacity(self) -> tuple[int, str]:
        """How many agents may run right now, and why not more."""
        if reason := await self.r.get(PAUSED):
            return 0, f"paused: {reason}"
        now = time.time()
        limit, why = settings.max_agents, "ok"
        for window, raw in (await self.r.hgetall(QUOTA)).items():
            q = json.loads(raw)
            resets_at, util = q.get("resets_at"), q.get("utilization")
            if resets_at and resets_at <= now:
                continue  # the window has reset since we heard about it
            if q.get("status") == "rejected" or (util is not None and util >= settings.quota_pause_at):
                return 0, f"{window} limit reached" + (f", resets at {int(resets_at)}" if resets_at else "")
            if q.get("status") == "allowed_warning" and util is None:
                limit, why = 1, f"{window} limit is close"
            elif util is not None and util >= settings.quota_throttle_at:
                limit, why = 1, f"{window} at {util:.0%}"
            elif window.startswith("seven_day") and util is not None and resets_at:
                elapsed = 1 - (resets_at - now) / WEEK
                if util > elapsed + settings.quota_pace_slack:
                    limit, why = 1, f"{window} at {util:.0%}, ahead of the weekly pace ({elapsed:.0%} elapsed)"
        return min(limit, settings.max_agents), why

    async def pause(self, reason: str) -> None:
        await self.r.set(PAUSED, reason or "by operator")

    async def resume(self) -> None:
        await self.r.delete(PAUSED)
        await self.dispatch()

    async def status(self) -> dict[str, Any]:
        capacity, reason = await self.capacity()
        return {
            "capacity": capacity,
            "reason": reason,
            "active": await self.r.zrange(ACTIVE, 0, -1),
            "ready": await self.r.zrange(READY, 0, -1),
            "quota": {w: json.loads(v) for w, v in (await self.r.hgetall(QUOTA)).items()},
        }

    async def _drop_active(self, m: str) -> None:
        async with self.r.pipeline(transaction=True) as p:
            p.zrem(ACTIVE, m)
            p.hdel(ACTIVE_PROJECT, m)
            p.hdel(ACTIVE_TASK, m)
            await p.execute()

    async def _cleanup(self) -> None:
        """Free slots whose step can no longer release them: never started in time,
        or its agent task finished or lost its worker."""
        for m in await self.r.zrangebyscore(ACTIVE, "-inf", time.time()):
            await self._drop_active(m)
        for m, task_id in (await self.r.hgetall(ACTIVE_TASK)).items():
            status = await self.r.hget(task_key(task_id), "status")
            if status is None or TaskStatus(status).is_terminal:
                await self._drop_active(m)
            elif status == TaskStatus.running and not await self.r.exists(lease_key(task_id)):
                await self._drop_active(m)
