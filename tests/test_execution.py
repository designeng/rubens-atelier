import asyncio
from pathlib import Path

from app.config import settings
from app.execution import WORKER_LOST, execute_task, reap_orphans
from app.models import TaskCreate, TaskStatus
from app.runner import AgentOutcome
from app.store import lease_key


class FakeRunner:
    """Stands in for run_claude; `script` receives (call_index, spec, cwd, resume)."""

    def __init__(self, script=None, delay: float = 0):
        self.calls: list[dict] = []
        self.script = script
        self.delay = delay

    async def __call__(self, spec, *, cwd: Path, resume, emit):
        n = len(self.calls)
        self.calls.append({"prompt": spec.prompt, "cwd": cwd, "resume": resume})
        await emit("session", {"session_id": f"sess-{n}"})
        await emit("text", {"text": f"working {n}"})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.script:
            self.script(n, spec, cwd, resume)
        return AgentOutcome(session_id=f"sess-{n}", result=f"done {n}", is_error=False, cost_usd=0.5, num_turns=3)


async def test_happy_path(store):
    task_id = await store.create(TaskCreate(prompt="hello"))
    runner = FakeRunner()
    await execute_task(task_id, store, "w1", runner=runner)

    info = await store.get(task_id)
    assert info.status == TaskStatus.succeeded
    assert info.result == "done 0"
    assert info.session_id == "sess-0"
    assert info.cost_usd == 0.5
    assert Path(info.workspace).is_dir()
    types = [t for _, t, _ in await store.read_events(task_id)]
    assert types == ["status", "status", "session", "text", "status"]
    assert task_id not in await store.running_ids()


async def test_cancelled_while_queued_is_never_run(store):
    task_id = await store.create(TaskCreate(prompt="hello"))
    assert await store.request_cancel(task_id) == TaskStatus.cancelled
    runner = FakeRunner()
    await execute_task(task_id, store, "w1", runner=runner)
    assert runner.calls == []
    assert (await store.get(task_id)).status == TaskStatus.cancelled


async def test_cancel_running(store):
    task_id = await store.create(TaskCreate(prompt="hello"))
    run = asyncio.create_task(execute_task(task_id, store, "w1", runner=FakeRunner(delay=10)))
    await asyncio.sleep(0.1)
    assert await store.request_cancel(task_id) == TaskStatus.running
    await asyncio.wait_for(run, 2)
    info = await store.get(task_id)
    assert info.status == TaskStatus.cancelled
    assert info.session_id == "sess-0"  # still resumable


async def test_timeout(store):
    task_id = await store.create(TaskCreate(prompt="hello", timeout_seconds=1))
    await execute_task(task_id, store, "w1", runner=FakeRunner(delay=5))
    info = await store.get(task_id)
    assert info.status == TaskStatus.failed
    assert "timed out" in info.error


async def test_verify_loop_resumes_session_until_green(store):
    task_id = await store.create(
        TaskCreate(prompt="make it pass", verify_command="test -f ok.txt", verify_attempts=2)
    )

    def script(n, spec, cwd, resume):
        if n == 1:  # fixes the problem only after seeing the verify failure
            assert "test -f ok.txt" in spec.prompt
            assert resume == "sess-0"
            (cwd / "ok.txt").write_text("ok")

    runner = FakeRunner(script)
    await execute_task(task_id, store, "w1", runner=runner)
    info = await store.get(task_id)
    assert info.status == TaskStatus.succeeded
    assert len(runner.calls) == 2
    assert info.cost_usd == 1.0 and info.num_turns == 6
    verify = [d for _, t, d in await store.read_events(task_id) if t == "verify"]
    assert [v["exit_code"] for v in verify] == [1, 0]


async def test_verify_gives_up(store):
    task_id = await store.create(TaskCreate(prompt="x", verify_command="false", verify_attempts=1))
    runner = FakeRunner()
    await execute_task(task_id, store, "w1", runner=runner)
    info = await store.get(task_id)
    assert info.status == TaskStatus.failed
    assert "verify command" in info.error
    assert len(runner.calls) == 2


async def test_follow_up_reuses_workspace_and_session(store):
    first = await store.create(TaskCreate(prompt="step 1"))
    await execute_task(first, store, "w1", runner=FakeRunner())
    second = await store.create(TaskCreate(prompt="step 2", parent_task_id=first))
    runner = FakeRunner()
    await execute_task(second, store, "w1", runner=runner)
    assert runner.calls[0]["resume"] == "sess-0"
    assert runner.calls[0]["cwd"] == Path((await store.get(first)).workspace)


async def test_redelivery_after_worker_death_fails_instead_of_rerunning(store, redis):
    task_id = await store.create(TaskCreate(prompt="hello"))
    assert await store.claim(task_id, "dead-worker")
    await redis.delete(lease_key(task_id))  # lease expired: the worker is gone

    runner = FakeRunner()
    await execute_task(task_id, store, "w2", runner=runner)
    assert runner.calls == []
    info = await store.get(task_id)
    assert info.status == TaskStatus.failed and info.error == WORKER_LOST


async def test_redelivered_duplicate_of_live_run_is_ignored(store):
    task_id = await store.create(TaskCreate(prompt="hello"))
    assert await store.claim(task_id, "w1")
    await execute_task(task_id, store, "w2", runner=FakeRunner())
    assert (await store.get(task_id)).status == TaskStatus.running


async def test_reaper(store, redis):
    alive = await store.create(TaskCreate(prompt="a"))
    dead = await store.create(TaskCreate(prompt="b"))
    await store.claim(alive, "w1")
    await store.claim(dead, "w2")
    await redis.delete(lease_key(dead))
    assert await reap_orphans(store) == [dead]
    assert (await store.get(dead)).status == TaskStatus.failed
    assert (await store.get(alive)).status == TaskStatus.running


async def test_finished_task_expires(store, redis):
    task_id = await store.create(TaskCreate(prompt="hello"))
    await execute_task(task_id, store, "w1", runner=FakeRunner())
    ttl = await redis.ttl(f"harness:task:{task_id}")
    assert 0 < ttl <= settings.result_ttl_seconds


async def test_progress_tracks_turn_activity_and_todos(store):
    seen = {}

    async def runner(spec, *, cwd, resume, emit):
        await emit("turn", {"turn": 1})
        await emit("tool_use", {"id": "t1", "name": "Bash", "input": {"command": "uv run pytest -q\necho done"}})
        await emit("turn", {"turn": 2})
        todos = [{"content": "Add endpoint", "status": "completed", "activeForm": "x"}, {"content": "Add tests", "status": "in_progress"}]
        await emit("tool_use", {"id": "t2", "name": "TodoWrite", "input": {"todos": todos}})
        seen.update(await store.progress(task_id))
        return AgentOutcome("s", "ok", False)

    task_id = await store.create(TaskCreate(prompt="x", max_turns=30))
    await execute_task(task_id, store, "w1", runner=runner)

    assert seen["status"] == "running" and seen["turn"] == 2 and seen["max_turns"] == 30
    assert seen["activity"] == "TodoWrite"
    assert seen["todos"] == [{"content": "Add endpoint", "status": "completed"}, {"content": "Add tests", "status": "in_progress"}]


async def test_progress_falls_back_to_the_event_log(store):
    task_id = await store.create(TaskCreate(prompt="x"))
    await store.append_event(task_id, "tool_use", {"name": "Read", "input": {"file_path": "app/api.py"}})
    await store.append_event(task_id, "text", {"text": "thinking out loud"})
    progress = await store.progress(task_id)
    assert progress["activity"] == "Read: app/api.py" and progress["turn"] is None
    assert progress["max_turns"] == settings.default_max_turns
