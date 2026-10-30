import asyncio
import json
from pathlib import Path

from app.config import ROOT, settings
from app.execution import WORKER_LOST, execute_task, reap_orphans
from app.models import FailureReason, TaskCreate, TaskStatus
from app.runner import AgentOutcome
from app.store import lease_key


class FakeRunner:
    """Stands in for run_claude; `script` receives (call_index, spec, cwd, resume).

    Like the SDK, a resumed session keeps its id and reports its cost as a running total."""

    def __init__(self, script=None, delay: float = 0, session_costs: dict[str, float] | None = None):
        self.calls: list[dict] = []
        self.script = script
        self.delay = delay
        self.session_costs = session_costs if session_costs is not None else {}

    async def __call__(self, spec, *, cwd: Path, resume, emit):
        n = len(self.calls)
        self.calls.append({"prompt": spec.prompt, "cwd": cwd, "resume": resume})
        session = resume or f"sess-{n}"
        self.session_costs[session] = self.session_costs.get(session, 0.0) + 0.5
        await emit("session", {"session_id": session})
        await emit("text", {"text": f"working {n}"})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.script:
            self.script(n, spec, cwd, resume)
        return AgentOutcome(
            session_id=session, result=f"done {n}", is_error=False, cost_usd=self.session_costs[session], num_turns=3
        )


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
    assert "timed out" in info.error and info.failure_reason == FailureReason.timeout


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
    assert "verify command" in info.error and info.failure_reason == FailureReason.verify_failed
    assert len(runner.calls) == 2
    (*_, (_, _, status)) = await store.read_events(task_id)
    assert status["failure_reason"] == "verify_failed"


async def test_agent_error_keeps_the_runners_failure_reason(store):
    async def out_of_turns(spec, *, cwd, resume, emit):
        return AgentOutcome("s", None, True, error="error_max_turns", failure=FailureReason.turn_limit)

    task_id = await store.create(TaskCreate(prompt="x"))
    await execute_task(task_id, store, "w1", runner=out_of_turns)
    assert (await store.get(task_id)).failure_reason == FailureReason.turn_limit


async def test_follow_up_reuses_workspace_and_session(store):
    costs: dict[str, float] = {}
    first = await store.create(TaskCreate(prompt="step 1"))
    await execute_task(first, store, "w1", runner=FakeRunner(session_costs=costs))
    second = await store.create(TaskCreate(prompt="step 2", parent_task_id=first))
    runner = FakeRunner(session_costs=costs)
    await execute_task(second, store, "w1", runner=runner)
    assert runner.calls[0]["resume"] == "sess-0"
    assert runner.calls[0]["cwd"] == Path((await store.get(first)).workspace)
    # The SDK reports $1.00 for the resumed session; the follow-up only spent the second half.
    assert (await store.get(second)).cost_usd == 0.5


async def test_redelivery_after_worker_death_fails_instead_of_rerunning(store, redis):
    task_id = await store.create(TaskCreate(prompt="hello"))
    assert await store.claim(task_id, "dead-worker")
    await redis.delete(lease_key(task_id))  # lease expired: the worker is gone

    runner = FakeRunner()
    await execute_task(task_id, store, "w2", runner=runner)
    assert runner.calls == []
    info = await store.get(task_id)
    assert info.status == TaskStatus.failed and info.error == WORKER_LOST
    assert info.failure_reason == FailureReason.worker_lost


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
        todos = [
            {"content": "Add endpoint", "status": "completed", "activeForm": "x"},
            {"content": "Add tests", "status": "in_progress"},
        ]
        await emit("tool_use", {"id": "t2", "name": "TodoWrite", "input": {"todos": todos}})
        seen.update(await store.progress(task_id))
        return AgentOutcome("s", "ok", False)

    task_id = await store.create(TaskCreate(prompt="x", max_turns=30))
    await execute_task(task_id, store, "w1", runner=runner)

    assert seen["status"] == "running" and seen["turn"] == 2 and seen["max_turns"] == 30
    assert seen["activity"] == "TodoWrite"
    assert seen["todos"] == [
        {"content": "Add endpoint", "status": "completed"},
        {"content": "Add tests", "status": "in_progress"},
    ]


async def test_progress_falls_back_to_the_event_log(store):
    task_id = await store.create(TaskCreate(prompt="x"))
    await store.append_event(task_id, "tool_use", {"name": "Read", "input": {"file_path": "app/api.py"}})
    await store.append_event(task_id, "text", {"text": "thinking out loud"})
    progress = await store.progress(task_id)
    assert progress["activity"] == "Read: app/api.py" and progress["turn"] is None
    assert progress["max_turns"] == settings.default_max_turns


def test_bash_allowlist_and_sandbox(monkeypatch):
    from pathlib import Path

    from app.config import settings
    from app.models import TaskCreate
    from app.runner import build_options

    spec = TaskCreate(prompt="x")
    monkeypatch.setattr(settings, "bash_allowed_commands", [])
    monkeypatch.setattr(settings, "sandbox", False)
    opts = build_options(spec, Path("."), None)
    assert "Bash" in opts.allowed_tools and not opts.sandbox

    monkeypatch.setattr(settings, "bash_allowed_commands", ["pytest", "git status"])
    monkeypatch.setattr(settings, "sandbox", True)
    opts = build_options(spec, Path("."), None)
    assert "Bash" not in opts.allowed_tools
    assert {"Bash(pytest:*)", "Bash(git status:*)", "Read"} <= set(opts.allowed_tools)
    assert opts.sandbox["enabled"] and not opts.sandbox["allowUnsandboxedCommands"]
    # Secrets are denied through settings, the only place the sandbox takes read rules from.
    deny = json.loads(opts.settings)["permissions"]["deny"]
    assert f"Read(/{ROOT / '.env'})" in deny


def test_sandbox_is_on_by_default(monkeypatch):
    from app.config import Settings, settings
    from app.models import TaskCreate
    from app.runner import build_options

    monkeypatch.delenv("HARNESS_SANDBOX", raising=False)
    assert Settings(_env_file=None).sandbox is True

    monkeypatch.setattr(settings, "sandbox", Settings(_env_file=None).sandbox)
    opts = build_options(TaskCreate(prompt="x"), Path("."), None)
    assert opts.sandbox["enabled"] and not opts.sandbox["allowUnsandboxedCommands"]
    assert opts.sandbox["failIfUnavailable"] is True


def test_lean_context(monkeypatch):
    from app.config import settings
    from app.models import TaskCreate
    from app.runner import LEAN_SYSTEM_PROMPT, build_options

    monkeypatch.setattr(settings, "lean_context", False)
    monkeypatch.setattr(settings, "bash_allowed_commands", ["pytest"])
    opts = build_options(TaskCreate(prompt="x"), Path("."), None)
    assert opts.system_prompt == {"type": "preset", "preset": "claude_code"}
    assert opts.tools is None and opts.setting_sources == ["project"]

    opts = build_options(TaskCreate(prompt="x", lean_context=True, system_prompt="extra"), Path("."), None)
    assert opts.system_prompt == LEAN_SYSTEM_PROMPT + "\n\nextra"
    assert opts.tools == ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
    assert "Bash(pytest:*)" in opts.allowed_tools and opts.setting_sources == []
    # Lean drops the context, not the safety: sandbox and secret deny rules stay.
    assert opts.sandbox["enabled"] and json.loads(opts.settings)["permissions"]["deny"]

    monkeypatch.setattr(settings, "lean_context", True)
    assert build_options(TaskCreate(prompt="x", lean_context=False), Path("."), None).tools is None


def test_agent_env_hides_harness_config(monkeypatch):
    from app.runner import agent_env, uv_cache_dir

    monkeypatch.setenv("HARNESS_GITHUB_TOKEN", "secret")
    monkeypatch.setenv("VIRTUAL_ENV", "/worker/.venv")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = agent_env()
    assert env["HARNESS_GITHUB_TOKEN"] == "" and env["VIRTUAL_ENV"] == ""
    assert "PATH" not in env  # inherited unchanged
    assert env["UV_CACHE_DIR"] == str(uv_cache_dir()) and uv_cache_dir().is_dir()


def test_error_names_the_last_failed_tool_call():
    from claude_agent_sdk import ResultMessage

    from app.runner import _failure, describe_error

    result = ResultMessage(
        subtype="error_during_execution", duration_ms=1, duration_api_ms=1, is_error=True,
        num_turns=28, session_id="s", errors=["[ede_diagnostic] stop_reason=tool_use"],
    )
    call = {"name": "Bash", "input": {"command": "until ! pgrep x; do sleep 2; done"}}
    msg = describe_error(result, _failure(call, "The user doesn't want to proceed"))
    assert "Bash until ! pgrep x" in msg and "doesn't want to proceed" in msg and "28 turns" in msg
    assert "last failed" not in describe_error(result, None)


def test_sdk_result_subtypes_map_to_failure_reasons():
    from types import SimpleNamespace

    from app.runner import failure_reason

    assert failure_reason(SimpleNamespace(subtype="error_max_turns")) == FailureReason.turn_limit
    assert failure_reason(SimpleNamespace(subtype="error_max_budget_usd")) == FailureReason.budget
    assert failure_reason(SimpleNamespace(subtype="error_during_execution")) == FailureReason.model_error


async def test_expired_task_leaves_the_index(store, redis):
    from app.store import EXPIRING, TASKS_INDEX

    task_id = await store.create(TaskCreate(prompt="hello"))
    await execute_task(task_id, store, "w1", runner=FakeRunner())
    assert await redis.zscore(TASKS_INDEX, task_id) is not None
    await redis.zadd(EXPIRING, {task_id: 1})  # its TTL has passed
    await store.create(TaskCreate(prompt="next"))
    assert await redis.zscore(TASKS_INDEX, task_id) is None
    assert await redis.zscore(EXPIRING, task_id) is None


async def test_prune_orphaned_index_drops_only_expired_tasks(store, redis):
    from app.store import TASKS_INDEX

    live = await store.create(TaskCreate(prompt="live"))
    await redis.zadd(TASKS_INDEX, {"expired-long-ago": 1})
    assert await store.prune_orphaned_index() == 1
    assert await redis.zrange(TASKS_INDEX, 0, -1) == [live]
