"""Task lifecycle around an agent run: claim, workspace, agent ⇄ verify loop,
issue finalization, watchdog, timeout, finish.

Kept independent of Celery so it can be tested (and reused) with any executor.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import gitops, issue_flow
from .config import settings
from .github import GitHub
from .models import TaskCreate, TaskStatus
from .runner import AgentOutcome, AgentRunner, Emit, run_claude
from .scheduler import record_rate_limit
from .store import TaskStore, describe_tool

log = logging.getLogger(__name__)

WATCHDOG_INTERVAL = 2.0
VERIFY_OUTPUT_TAIL = 6_000
WORKER_LOST = "worker lost while running; create a follow-up task with parent_task_id to resume"


@dataclass
class Workspace:
    cwd: Path
    prompt: str
    resume: str | None = None


def item_workspace(key: str) -> Path:
    return (settings.workspaces_root / "items" / key).resolve()


async def prepare_workspace(
    store: TaskStore, task_id: str, spec: TaskCreate, gh: GitHub | None
) -> Workspace:
    if spec.parent_task_id:
        parent = await store.get_raw(spec.parent_task_id)
        if parent is None or not parent.get("workspace"):
            raise RuntimeError(f"parent task {spec.parent_task_id} not found or has no workspace")
        return Workspace(Path(parent["workspace"]), spec.prompt, parent.get("session_id"))

    if spec.workspace_key:
        cwd = item_workspace(spec.workspace_key)
        if not cwd.is_dir():
            raise RuntimeError(f"workspace {spec.workspace_key} has not been prepared")
        return Workspace(cwd, spec.prompt, spec.resume_session)

    cwd = (settings.workspaces_root / task_id).resolve()
    cwd.mkdir(parents=True, exist_ok=True)
    if spec.issue:
        assert gh is not None
        prompt, _base = await issue_flow.prepare(gh, spec.issue, cwd, spec.prompt)
        return Workspace(cwd, prompt)
    if spec.repo_url:
        await gitops.clone(spec.repo_url, cwd, ref=spec.repo_ref)
    return Workspace(cwd, spec.prompt)


async def run_agent(
    spec: TaskCreate, prompt: str, cwd: Path, resume: str | None, runner: AgentRunner, emit: Emit, store: TaskStore
) -> AgentOutcome:
    """One agent call, priced on its own: for a resumed session the SDK reports the cost of
    the whole session so far, so subtract the total its previous call reported."""
    before = await store.session_cost(resume) if resume else 0.0
    outcome = await runner(spec.model_copy(update={"prompt": prompt}), cwd=cwd, resume=resume, emit=emit)
    if outcome.session_id and outcome.cost_usd is not None:
        await store.set_session_cost(outcome.session_id, outcome.cost_usd)
        outcome.cost_usd = max(outcome.cost_usd - before, 0.0)
    return outcome


async def run_with_verify(
    spec: TaskCreate, ws: Workspace, runner: AgentRunner, emit: Emit, store: TaskStore
) -> AgentOutcome:
    """Run the agent, then the verify command; on failure resume the same session
    with the output until it passes or attempts run out."""
    outcome = await run_agent(spec, ws.prompt, ws.cwd, ws.resume, runner, emit, store)
    cost, turns = outcome.cost_usd or 0.0, outcome.num_turns or 0

    command = spec.verify_command or (settings.issue_verify_command if spec.issue else None)
    attempts = spec.verify_attempts if spec.verify_attempts is not None else settings.default_verify_attempts
    while command and not outcome.is_error:
        code, output = await gitops.run_shell(command, ws.cwd, settings.verify_timeout_seconds)
        tail = output[-VERIFY_OUTPUT_TAIL:]
        await emit("verify", {"command": command, "exit_code": code, "output": tail})
        if code == 0:
            break
        if attempts <= 0:
            outcome.is_error = True
            outcome.error = f"verify command `{command}` failed with exit code {code}"
            break
        attempts -= 1
        fix_prompt = (
            f"The verification command `{command}` failed with exit code {code}. "
            "Find the root cause, fix it, re-run the checks and commit.\n\n"
            f"Output (tail):\n```\n{tail}\n```"
        )
        outcome = await run_agent(spec, fix_prompt, ws.cwd, outcome.session_id, runner, emit, store)
        cost, turns = cost + (outcome.cost_usd or 0.0), turns + (outcome.num_turns or 0)

    outcome.cost_usd, outcome.num_turns = cost, turns
    return outcome


async def execute_task(
    task_id: str,
    store: TaskStore,
    worker_id: str,
    runner: AgentRunner = run_claude,
    github_factory: Callable[[], GitHub] = GitHub,
) -> None:
    if not await store.claim(task_id, worker_id):
        # Either cancelled while queued, already finished, or this is a redelivered
        # message for a run whose worker died (status still `running`, lease gone).
        raw = await store.get_raw(task_id)
        if raw and raw["status"] == TaskStatus.running and not await store.lease_alive(task_id):
            await store.finish(task_id, TaskStatus.failed, error=WORKER_LOST)
        return

    spec = await store.get_spec(task_id)
    gh: GitHub | None = None

    async def fail(status: TaskStatus, error: str) -> None:
        if await store.finish(task_id, status, error=error) and spec.issue and gh:
            await issue_flow.mark_failed(gh, spec.issue, task_id, error)

    try:
        if spec.issue:
            gh = github_factory()
            await store.set_fields(task_id, branch=issue_flow.branch_name(spec.issue))
        try:
            ws = await prepare_workspace(store, task_id, spec, gh)
        except Exception as e:
            await fail(TaskStatus.failed, f"workspace setup failed: {e}")
            return
        await store.set_fields(task_id, workspace=str(ws.cwd))
        await _run(task_id, spec, ws, store, worker_id, runner, gh, fail)
    finally:
        if gh:
            await gh.aclose()


async def _run(task_id, spec, ws, store, worker_id, runner, gh, fail) -> None:
    async def emit(type_: str, data: dict[str, Any]) -> None:
        if type_ == "session" and data.get("session_id"):
            # Persist early so cancelled / timed-out runs can still be resumed.
            await store.set_fields(task_id, session_id=data["session_id"])
        if type_ == "turn":
            await store.set_fields(task_id, turn=str(data["turn"]))
        if type_ == "tool_use":
            fields = {"activity": describe_tool(data["name"], data.get("input") or {})}
            if data["name"] == "TodoWrite" and isinstance(data.get("input", {}).get("todos"), list):
                fields["todos"] = json.dumps(
                    [{"content": t.get("content", ""), "status": t.get("status", "")} for t in data["input"]["todos"]]
                )
            await store.set_fields(task_id, **fields)
        if type_ == "verify":
            await store.set_fields(task_id, activity=f"verify: {data['command']} → exit {data['exit_code']}")
        if type_ == "rate_limit":
            await record_rate_limit(store.r, data)
            if data.get("status") == "rejected":
                # The run failed (or will) for lack of quota, not because of the work itself.
                await store.set_fields(task_id, rate_limited="1")
        await store.append_event(task_id, type_, data)

    async def job() -> tuple[AgentOutcome, str | None]:
        outcome = await run_with_verify(spec, ws, runner, emit, store)
        pr_url = None
        if spec.issue and not outcome.is_error:
            await emit("publish", {"branch": issue_flow.branch_name(spec.issue)})
            pr_url = await issue_flow.finalize(gh, spec.issue, ws.cwd, outcome.result)
        return outcome, pr_url

    work = asyncio.create_task(job())
    user_cancelled = asyncio.Event()

    async def watchdog() -> None:
        while not work.done():
            await asyncio.sleep(WATCHDOG_INTERVAL)
            await store.renew_lease(task_id, worker_id)
            if await store.cancel_requested(task_id):
                user_cancelled.set()
                work.cancel()
                return

    watcher = asyncio.create_task(watchdog())
    timeout = min(spec.timeout_seconds or settings.task_timeout_seconds, settings.task_timeout_seconds)
    try:
        async with asyncio.timeout(timeout):
            outcome, pr_url = await work
    except asyncio.CancelledError:
        if not user_cancelled.is_set():
            raise
        await fail(TaskStatus.cancelled, "cancelled by user")
    except TimeoutError:
        await fail(TaskStatus.failed, f"timed out after {timeout}s")
    except Exception as e:
        log.exception("task %s crashed", task_id)
        await fail(TaskStatus.failed, f"{type(e).__name__}: {e}")
    else:
        fields = dict(
            session_id=outcome.session_id,
            result=outcome.result,
            cost_usd=outcome.cost_usd,
            num_turns=outcome.num_turns,
            pr_url=pr_url,
        )
        if outcome.is_error:
            await store.set_fields(task_id, **{k: str(v) for k, v in fields.items() if v is not None})
            await fail(TaskStatus.failed, outcome.error or "agent reported an error")
        else:
            await store.finish(task_id, TaskStatus.succeeded, structured_output=outcome.structured_output, **fields)
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher


async def reap_orphans(store: TaskStore) -> list[str]:
    """Fail `running` tasks whose lease expired (worker killed / OOM / host lost)."""
    reaped = []
    for task_id in await store.running_ids():
        if await store.lease_alive(task_id):
            continue
        if await store.finish(task_id, TaskStatus.failed, error=WORKER_LOST):
            reaped.append(task_id)
        else:
            await store.forget_running(task_id)  # finished or expired meanwhile
    return reaped
