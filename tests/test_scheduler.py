import json
import time

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import Run
from app.models import TaskCreate, TaskStatus
from app.runner import AgentOutcome
from app.scheduler import ACTIVE, QUOTA, Scheduler, SchedulerError, record_rate_limit
from tests.test_engine import CIGitHub, Factory, ScriptedRunner
from tests.test_issue_flow import origin  # noqa: F401  (fixture)


@pytest.fixture
def dispatched():
    return []


@pytest.fixture
def scheduler(redis, dispatched, monkeypatch):
    monkeypatch.setattr(settings, "max_agents", 4)
    monkeypatch.setattr(settings, "max_agents_per_project", 2)

    async def enqueue(item_id, version):
        dispatched.append(item_id)

    return Scheduler(redis, enqueue)


async def quota(redis, window, **info):
    await record_rate_limit(redis, {"rate_limit_type": window, **info})


async def test_capacity_follows_subscription_windows(scheduler, redis):
    assert await scheduler.capacity() == (4, "ok")
    now = time.time()

    await quota(redis, "five_hour", status="allowed", utilization=0.85, resets_at=now + 3600)
    assert await scheduler.capacity() == (1, "five_hour at 85%")

    await quota(redis, "five_hour", status="allowed", utilization=0.3, resets_at=now + 3600)
    # 2 days into the week, 60% used: ahead of the 29% + 10% pace.
    await quota(redis, "seven_day", status="allowed", utilization=0.6, resets_at=now + 5 * 86400)
    cap, why = await scheduler.capacity()
    assert cap == 1 and "ahead of the weekly pace" in why

    await quota(redis, "seven_day", status="allowed", utilization=0.3, resets_at=now + 5 * 86400)
    assert await scheduler.capacity() == (4, "ok")

    await quota(redis, "five_hour", status="rejected", resets_at=now + 600)
    cap, why = await scheduler.capacity()
    assert cap == 0 and why.startswith("five_hour limit reached")

    await quota(redis, "five_hour", status="rejected", resets_at=now - 1)  # has reset since
    await quota(redis, "overage", status="rejected")  # pay-as-you-go state doesn't gate
    assert await scheduler.capacity() == (4, "ok")

    await scheduler.pause("deploy")
    assert await scheduler.capacity() == (0, "paused: deploy")



async def test_run_now_skips_the_pause_and_limits_but_not_a_rejected_window(scheduler, redis, dispatched):
    await scheduler.set_limits(max_agents=1)
    await scheduler.submit("busy", 1, "A")
    await scheduler.pause("stopped everything")
    await scheduler.submit("a", 1, "A")
    await scheduler.submit("b", 1, "A")
    assert dispatched == ["busy"]

    await scheduler.run_now("b", 1)
    assert dispatched == ["busy", "b"]
    assert await scheduler.is_pending("b", 1)  # holds a slot now
    assert (await scheduler.status())["ready"] == ["a:1"]
    with pytest.raises(SchedulerError, match="not waiting"):
        await scheduler.run_now("b", 1)

    await quota(redis, "five_hour", status="rejected", resets_at=time.time() + 600)
    with pytest.raises(SchedulerError, match="five_hour limit reached"):
        await scheduler.run_now("a", 1)
    assert (await scheduler.status())["ready"] == ["a:1"]

async def test_dispatch_by_priority_with_a_per_project_cap(scheduler, dispatched):
    await scheduler.pause("filling the queue")
    await scheduler.submit("a-low", 1, "A", priority=0)
    await scheduler.submit("a-high", 1, "A", priority=5)
    await scheduler.submit("a-mid", 1, "A", priority=1)
    await scheduler.submit("b", 1, "B", priority=0)
    await scheduler.submit("b", 1, "B", priority=0)  # duplicate submit is ignored
    assert dispatched == []

    await scheduler.resume()
    assert dispatched == ["a-high", "a-mid", "b"]  # A is capped at 2 per project
    assert await scheduler.is_pending("a-low", 1)

    await scheduler.release("a-high", 1)
    assert dispatched[-1] == "a-low"
    assert (await scheduler.status())["ready"] == []


async def test_waiting_step_moves_when_its_priority_changes(scheduler, dispatched):
    await scheduler.pause("filling the queue")
    for name in ("first", "second", "third"):
        await scheduler.submit(name, 1, name)
    await scheduler.reprioritize("third", 1, 0, 5)
    await scheduler.reprioritize("first", 1, 0, -1)
    await scheduler.reprioritize("gone", 1, 0, 9)  # not waiting: nothing to move
    assert (await scheduler.status())["ready"] == ["third:1", "second:1", "first:1"]


async def test_global_cap_and_forget(scheduler, dispatched, monkeypatch):
    monkeypatch.setattr(settings, "max_agents", 1)
    await scheduler.submit("x", 1, "A")
    await scheduler.submit("y", 1, "B")
    assert dispatched == ["x"]
    await scheduler.forget("y", 1)  # its item moved on while it waited
    await scheduler.release("x", 1)
    assert dispatched == ["x"]


async def test_limits_change_on_the_fly(scheduler, dispatched):
    assert await scheduler.limits() == {"max_agents": 4, "max_agents_per_project": 2, "force": False}
    await scheduler.set_limits(max_agents=1)
    for name in ("a1", "a2", "b1"):
        await scheduler.submit(name, 1, name[0].upper())
    assert dispatched == ["a1"] and await scheduler.capacity() == (1, "ok")

    # Raising the limit hands the waiting steps out right away, within the per-project cap.
    await scheduler.set_limits(max_agents=3, max_agents_per_project=1)
    assert dispatched == ["a1", "b1"]
    await scheduler.set_limits(max_agents_per_project=2)
    assert dispatched == ["a1", "b1", "a2"]
    assert (await scheduler.status())["limits"] == {"max_agents": 3, "max_agents_per_project": 2, "force": False}

    await scheduler.reset_limits()
    assert await scheduler.limits() == {"max_agents": 4, "max_agents_per_project": 2, "force": False}


async def test_force_keeps_the_limit_until_a_window_is_rejected(scheduler, redis):
    now = time.time()
    await quota(redis, "five_hour", status="allowed", utilization=0.85, resets_at=now + 3600)
    assert await scheduler.capacity() == (1, "five_hour at 85%")
    await scheduler.set_limits(force=True)
    assert await scheduler.capacity() == (4, "forced: five_hour at 85%")
    await scheduler.set_limits(max_agents=2)
    assert await scheduler.capacity() == (2, "forced: five_hour at 85%")

    await quota(redis, "seven_day", status="allowed_warning", utilization=0.96, resets_at=now + 600)
    assert await scheduler.capacity() == (2, "forced: seven_day at 96%")
    await scheduler.set_limits(force=False)
    assert (await scheduler.capacity())[0] == 0
    await scheduler.set_limits(force=True)

    await quota(redis, "five_hour", status="rejected", resets_at=now + 600)
    assert (await scheduler.capacity())[0] == 0
    await scheduler.pause("deploy")
    assert await scheduler.capacity() == (0, "paused: deploy")


async def test_slots_of_dead_or_finished_steps_are_reclaimed(scheduler, dispatched, store, redis):
    for name in ("finished", "crashed", "never-started"):
        await scheduler.submit(name, 1, name)
    finished = await store.create(TaskCreate(prompt="x"))
    crashed = await store.create(TaskCreate(prompt="y"))
    await scheduler.started("finished", 1, finished)
    await scheduler.started("crashed", 1, crashed)
    await store.claim(finished, "w1")
    await store.finish(finished, TaskStatus.succeeded)
    await store.claim(crashed, "w1")
    await redis.delete(f"harness:task:{crashed}:lease")  # worker died
    await redis.zadd(ACTIVE, {"never-started:1": time.time() - 1})  # dispatch TTL passed

    await scheduler.dispatch()
    assert await redis.zrange(ACTIVE, 0, -1) == []


async def test_story_runs_through_scheduler_and_frees_its_slots(db, store, redis, origin):  # noqa: F811
    f = Factory(db, store, CIGitHub(origin), ScriptedRunner())
    f.engine.scheduler = Scheduler(redis, f.enqueue)
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    assert (await f.item(item_id)).state == "awaiting_merge"
    status = await f.engine.scheduler.status()
    assert status["active"] == [] and status["ready"] == []


class RateLimitedOnce(ScriptedRunner):
    """The first implement run hits the 5-hour limit halfway through."""

    async def __call__(self, spec, *, cwd, resume, emit):
        if spec.metadata["state"] == "implement" and not self.of("implement"):
            self.calls.append({"role": "implement", "prompt": spec.prompt, "resume": resume, "spec": spec})
            await emit("session", {"session_id": "implement-cut"})
            await emit("rate_limit", {"status": "rejected", "rate_limit_type": "five_hour", "resets_at": time.time() + 600})
            return AgentOutcome("implement-cut", None, True, error="usage limit reached")
        return await super().__call__(spec, cwd=cwd, resume=resume, emit=emit)


async def test_rate_limited_step_waits_for_the_reset_and_resumes(db, store, redis, origin):  # noqa: F811
    f = Factory(db, store, CIGitHub(origin), RateLimitedOnce())
    f.engine.scheduler = Scheduler(redis, f.enqueue)
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    # Not escalated: back in the queue, dispatch paused until the window resets.
    item = await f.item(item_id)
    assert item.state == "implement" and item.sessions["implementer"] == "implement-cut"
    status = await f.engine.scheduler.status()
    assert status["capacity"] == 0 and status["ready"] == [f"{item_id}:{item.version}"]
    async with db.session() as s:
        runs = (await s.scalars(select(Run))).all()
    assert [r.state for r in runs] == ["plan"]  # the cut implement run doesn't count

    info = json.loads(await redis.hget(QUOTA, "five_hour")) | {"resets_at": time.time() - 1}
    await redis.hset(QUOTA, "five_hour", json.dumps(info))
    await f.engine.scheduler.dispatch()
    await f.drain()

    assert (await f.item(item_id)).state == "awaiting_merge"
    assert f.runner.of("implement")[1]["resume"] == "implement-cut"
