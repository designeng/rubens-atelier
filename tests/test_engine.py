import subprocess
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import event, select, update

from app import steps
from app.archive import archive_task
from app.checks import poll_checks
from app.config import settings
from app.db import Artifact, HumanRequest, ItemEvent, Project, Run, TaskTranscript, WorkItem, utcnow
from app.engine import Engine, EngineError
from app.execution import item_workspace, remove_item_workspace
from app.models import FailureReason
from app.runner import AgentOutcome
from app.steps import run_step
from app.workflow import load_workflows
from tests.conftest import FakeGitHub, git

HAPPY_PATH = ["plan", "implement", "sync_base", "review", "publish", "ci", "merge", "awaiting_merge"]


class CIGitHub(FakeGitHub):
    """FakeGitHub plus the Checks API; `checks` is what CI reports for any commit."""

    def __init__(self, origin):
        super().__init__(origin)
        self.checks = [
            {"id": 1, "name": "test", "status": "completed", "conclusion": "success", "app": {"slug": "github-actions"}}
        ]
        self.statuses: list[dict] = []
        self.pr_state: dict = {"state": "open", "merged_at": None}
        self.logs = {1: "collected 3 items\nFAILED test_hello.py::test_says_hi - AssertionError\n"}
        self.merges: list[dict] = []
        self.merge_refusal: int | None = None  # HTTP status GitHub answers the merge with

    async def check_runs(self, repo, ref):
        return self.checks

    async def commit_status(self, repo, ref):
        return {"statuses": self.statuses}

    async def job_log(self, repo, job_id):
        return self.logs[job_id]

    async def get_pr(self, repo, number):
        return {"number": number, **self.pr_state}

    async def merge_pr(self, repo, number, *, method, sha=None):
        self.merges.append({"number": number, "method": method, "sha": sha})
        if self.merge_refusal:
            request = httpx.Request("PUT", f"https://api.github.com/repos/{repo}/pulls/{number}/merge")
            body = {"message": "Pull Request is not mergeable"}
            response = httpx.Response(self.merge_refusal, json=body, request=request)
            raise httpx.HTTPStatusError("refused", request=request, response=response)
        self.pr_state = {"state": "closed", "merged_at": "2026-10-30T00:00:00Z"}
        return {"merged": True}


class Factory:
    """Engine with an in-process queue: `drain()` runs enqueued steps like a worker would
    and polls check states like beat would, until nothing moves any more."""

    def __init__(self, db, store, gh, runner, scheduler=None):
        self.queue: list[tuple[str, int]] = []
        self.gh, self.runner, self.store = gh, runner, store

        async def enqueue(item_id, version):
            self.queue.append((item_id, version))

        self.enqueue = enqueue
        self.cleaned: list[str] = []

        async def cleanup(item_id):
            self.cleaned.append(item_id)
            remove_item_workspace(item_id)

        self.engine = Engine(db, load_workflows(settings.workflows_dir), store, enqueue, scheduler, cleanup)
        self.db = db

    async def step(self, item_id, version):
        await run_step(
            item_id, version, engine=self.engine, store=self.store, worker_id="w1",
            runner=self.runner, github_factory=lambda _project: self.gh,
        )

    async def drain(self):
        while True:
            while self.queue:
                await self.step(*self.queue.pop(0))
            if not await poll_checks(self.engine, github_factory=lambda _project: self.gh) and not self.queue:
                return

    async def item(self, item_id) -> WorkItem:
        async with self.db.session() as s:
            return await s.get(WorkItem, item_id)

    async def open_request(self, item_id) -> HumanRequest | None:
        async with self.db.session() as s:
            return await s.scalar(
                select(HumanRequest).where(HumanRequest.item_id == item_id, HumanRequest.answer.is_(None))
            )

    async def path(self, item_id) -> list[str]:
        async with self.db.session() as s:
            rows = await s.scalars(select(ItemEvent).where(ItemEvent.item_id == item_id).order_by(ItemEvent.id))
            return [e.to_state for e in rows]

    async def new_item(self, auto_merge=False, **kw) -> str:
        async with self.db.session() as s:
            project = await s.scalar(select(Project).where(Project.slug == "app"))
            if project is None:
                project = Project(
                    slug="app", repo="acme/app", verify_command="test -f hello.txt", auto_merge=auto_merge
                )
                s.add(project)
                await s.flush()
            item = WorkItem(project_id=project.id, kind="story", title="Add greeting",
                            body="Create hello.txt saying hi.", workflow="story", **kw)
            s.add(item)
            await s.commit()
            return item.id


class ScriptedRunner:
    """Planner and reviewer return the next verdict from `plans` / `reviews` (the last one
    repeats); implementer commits a file. Sessions and summaries are numbered per state."""

    def __init__(self, reviews=("approved",), implement_ok=True, plans=("ready",)):
        self.verdicts = {"plan": list(plans), "review": list(reviews)}
        self.implement_ok = implement_ok
        self.calls: list[dict] = []

    def of(self, state: str) -> list[dict]:
        return [c for c in self.calls if c["role"] == state]

    async def __call__(self, spec, *, cwd, resume, emit):
        role = spec.metadata["state"]
        n = len(self.of(role))
        self.calls.append({"role": role, "prompt": spec.prompt, "resume": resume, "spec": spec})
        session = f"{role}-{n}"
        await emit("session", {"session_id": session})
        if role == "implement":
            if not self.implement_ok:
                return AgentOutcome(session, None, True, error="max turns")
            (cwd / "hello.txt").write_text(f"hi {n}\n")
            git(cwd, "add", ".")
            git(cwd, "commit", "-m", f"greeting {n}")
            return AgentOutcome(session, f"Implemented greeting (call {n}).", False, cost_usd=1.0)
        queue = self.verdicts[role]
        verdict = queue.pop(0) if len(queue) > 1 else queue[0]
        return AgentOutcome(
            session, "ignored", False, cost_usd=0.5,
            structured_output={"verdict": verdict, "summary": f"{role.capitalize()} {n}: {verdict}, see hello.txt:1"},
        )


@pytest.fixture
def make_factory(db, store, origin):
    def make(runner):
        return Factory(db, store, CIGitHub(origin), runner)

    return make


async def test_story_happy_path(make_factory, origin):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item(source={"issue": 7})

    assert await f.engine.start(item_id)
    await f.drain()

    assert await f.path(item_id) == HAPPY_PATH
    item = await f.item(item_id)
    assert item.head_sha == git(origin, "rev-parse", item.branch).strip()
    assert item.branch == f"agent/story-{item_id[:8]}"
    assert item.pr_url == "https://gh/pull/1"
    assert set(item.sessions) == {"planner", "implementer", "reviewer"}
    pr = f.gh.prs[0]
    assert pr["head"] == item.branch and pr["base"] == "main"
    for part in ("Closes #7", "Plan 0: ready", "Implemented greeting", "Review 0: approved"):
        assert part in pr["body"]
    assert "hi 0" in git(origin, "show", f"{item.branch}:hello.txt")

    (plan,) = f.runner.of("plan")
    assert plan["spec"].disallowed_tools == ["Write", "Edit"]
    assert plan["spec"].output_schema["properties"]["verdict"]["enum"] == ["ready", "needs_clarification", "too_big"]
    assert '<artifact name="planner">' in f.runner.of("implement")[0]["prompt"]
    (review,) = f.runner.of("review")
    verdicts = review["spec"].output_schema["properties"]["verdict"]["enum"]
    assert verdicts == ["approved", "changes_requested", "needs_human"]
    assert review["spec"].disallowed_tools == ["Write", "Edit"]
    assert review["resume"] is None  # fresh context: the reviewer did not write the code
    assert '<artifact name="implementer">' in review["prompt"] and '<artifact name="planner">' in review["prompt"]

    req = await f.open_request(item_id)
    assert req.options == ["merged", "update", "rework", "abandon"]
    assert await f.engine.answer(req.id, "merged")
    assert (await f.item(item_id)).state == "done"
    async with f.db.session() as s:
        runs = (await s.scalars(select(Run).where(Run.item_id == item_id).order_by(Run.created_at))).all()
    assert [(r.state, r.verdict, r.status) for r in runs] == [
        ("plan", "ready", "succeeded"),
        ("implement", "done", "succeeded"),
        ("review", "approved", "succeeded"),
    ]


async def test_verify_command_changed_mid_story_is_used_by_the_next_step(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    async with f.db.session() as s:
        await s.execute(update(Project).values(verify_command=None))
        await s.commit()
    assert await f.engine.start(item_id)
    await f.step(*f.queue.pop(0))  # plan

    # What PATCH /projects/{slug} does: the implement step reads the project afresh.
    async with f.db.session() as s:
        await s.execute(update(Project).values(verify_command="test -f hello.txt"))
        await s.commit()
    await f.drain()
    assert f.runner.of("implement")[0]["spec"].verify_command == "test -f hello.txt"


async def test_review_feedback_loops_back_to_the_same_implementer_session(make_factory):
    f = make_factory(ScriptedRunner(reviews=["changes_requested", "approved"]))
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    assert await f.path(item_id) == [*HAPPY_PATH[:4], "implement", "sync_base", "review", *HAPPY_PATH[4:]]
    second_impl = f.runner.of("implement")[1]
    assert second_impl["resume"] == "implement-0"
    assert "Review 0: changes_requested" in second_impl["prompt"]
    # The resumed session already has the plan and its own summary: only the review is new.
    assert '<artifact name="planner">' not in second_impl["prompt"]
    assert '<artifact name="implementer">' not in second_impl["prompt"]
    assert second_impl["spec"].lean_context is False


async def test_lean_story_starts_each_visit_afresh(make_factory):
    f = make_factory(ScriptedRunner(reviews=["changes_requested", "approved"]))
    item_id = await f.new_item(lean_context=True)
    await f.engine.start(item_id)
    await f.drain()

    first, second = f.runner.of("implement")
    assert first["spec"].lean_context and second["spec"].lean_context
    assert second["resume"] is None  # a new session, carried by the artifacts instead
    for part in ('<artifact name="planner">', '<artifact name="implementer">', "Review 0: changes_requested"):
        assert part in second["prompt"]


async def test_endless_review_ping_pong_is_cut_off_and_escalated(make_factory):
    f = make_factory(ScriptedRunner(reviews=["changes_requested"]))
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    path = await f.path(item_id)
    assert path.count("implement") == 4 and path[-1] == "needs_human"
    req = await f.open_request(item_id)
    assert req.state == "needs_human"

    await f.engine.answer(req.id, "abandon", comment="not worth it")
    assert (await f.item(item_id)).state == "cancelled"


async def test_retry_after_the_loop_limit_gives_the_step_a_fresh_budget(make_factory):
    f = make_factory(ScriptedRunner(reviews=["changes_requested"]))
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()
    assert (await f.item(item_id)).state == "needs_human"

    await f.engine.answer((await f.open_request(item_id)).id, "retry", comment="lock refreshed")
    assert (await f.item(item_id)).state == "implement"
    await f.drain()
    path = await f.path(item_id)
    assert path.count("implement") == 8 and path[-1] == "needs_human"


async def test_agent_failure_escalates_with_the_error(make_factory):
    f = make_factory(ScriptedRunner(implement_ok=False))
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    assert (await f.item(item_id)).state == "needs_human"
    async with f.db.session() as s:
        errors = (await s.scalars(select(Artifact.content).where(Artifact.name == "error"))).all()
    assert any("max turns" in e for e in errors)

    req = await f.open_request(item_id)
    f.runner.implement_ok = True
    await f.engine.answer(req.id, "retry", comment="try a smaller change")
    await f.drain()
    assert (await f.item(item_id)).state == "awaiting_merge"
    assert "try a smaller change" in f.runner.of("implement")[-1]["prompt"]


class WorkerDies(BaseException):
    """Kills the step like an OOM would: nothing in the harness catches it."""


class DiesMidImplement(ScriptedRunner):
    """The first `deaths` implement runs start a session and then lose their worker."""

    def __init__(self, deaths=1, **kw):
        super().__init__(**kw)
        self.deaths = deaths

    async def __call__(self, spec, *, cwd, resume, emit):
        if spec.metadata["state"] == "implement" and self.deaths:
            self.deaths -= 1
            n = len(self.of("implement"))
            self.calls.append({"role": "implement", "prompt": spec.prompt, "resume": resume, "spec": spec})
            await emit("session", {"session_id": f"implement-cut-{n}"})
            raise WorkerDies
        return await super().__call__(spec, cwd=cwd, resume=resume, emit=emit)


async def die_and_redeliver(f, item_id):
    with pytest.raises(WorkerDies):
        await f.drain()
    for key in await f.store.r.keys("harness:task:*:lease"):
        await f.store.r.delete(key)  # the lease runs out
    await f.step(item_id, (await f.item(item_id)).version)  # Celery redelivers the step


async def test_a_lost_worker_restarts_the_step_resuming_its_session(make_factory):
    f = make_factory(DiesMidImplement())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await die_and_redeliver(f, item_id)
    await f.drain()

    assert (await f.item(item_id)).state == "awaiting_merge"
    assert await f.path(item_id) == HAPPY_PATH
    cut, resumed = f.runner.of("implement")
    assert resumed["resume"] == "implement-cut-0" and resumed["prompt"] == steps.CONTINUE
    async with f.db.session() as s:
        runs = (await s.scalars(select(Run).where(Run.item_id == item_id))).all()
    assert [r.state for r in runs] == ["plan", "implement", "review"]  # the cut run doesn't count


async def test_workers_dying_too_often_end_the_step_with_restart_limit(make_factory, monkeypatch):
    monkeypatch.setattr(settings, "restart_limit", 1)
    f = make_factory(DiesMidImplement(deaths=2))
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await die_and_redeliver(f, item_id)
    await die_and_redeliver(f, item_id)
    await f.drain()

    assert (await f.item(item_id)).state == "needs_human"
    assert [c["resume"] for c in f.runner.of("implement")] == [None, "implement-cut-0"]
    async with f.db.session() as s:
        run = await s.scalar(select(Run).where(Run.item_id == item_id, Run.state == "implement"))
        error = await s.scalar(select(Artifact.content).where(Artifact.name == "error"))
    assert run.verdict == "restart_limit" and "worker died 2 times" in error


class OutOfTurnsOnce(ScriptedRunner):
    async def __call__(self, spec, *, cwd, resume, emit):
        if spec.metadata["state"] == "implement" and not self.of("implement"):
            self.calls.append({"role": "implement", "prompt": spec.prompt, "resume": resume, "spec": spec})
            await emit("session", {"session_id": "implement-long"})
            return AgentOutcome("implement-long", None, True, error="error_max_turns", failure=FailureReason.turn_limit)
        return await super().__call__(spec, cwd=cwd, resume=resume, emit=emit)


async def test_implementer_out_of_turns_continues_its_session(make_factory):
    f = make_factory(OutOfTurnsOnce())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    assert (await f.item(item_id)).state == "awaiting_merge"
    assert (await f.path(item_id))[:3] == ["plan", "implement", "implement"]
    assert f.runner.of("implement")[1]["resume"] == "implement-long"
    async with f.db.session() as s:
        verdicts = (await s.scalars(select(Run.verdict).where(Run.state == "implement").order_by(Run.version))).all()
    assert verdicts == ["turn_limit", "done"]


async def test_lean_story_still_continues_a_session_cut_by_the_turn_limit(make_factory):
    f = make_factory(OutOfTurnsOnce())
    item_id = await f.new_item(lean_context=True)
    await f.engine.start(item_id)
    await f.drain()

    assert f.runner.of("implement")[1]["resume"] == "implement-long"


async def test_duplicate_and_stale_steps_are_no_ops(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    (step,) = f.queue
    f.queue.clear()

    await f.step(*step)
    await f.step(*step)  # redelivered message: the item has moved on
    assert len(f.runner.calls) == 1
    assert await f.path(item_id) == ["plan", "implement"]

    assert not await f.engine.advance(item_id, step[1], "done")  # stale verdict


async def test_finished_runs_are_archived_once(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    async with f.db.session() as s:
        runs = (await s.scalars(select(Run).where(Run.item_id == item_id))).all()
        transcripts = {t.task_id: t for t in await s.scalars(select(TaskTranscript))}
    assert len(runs) == 3 and set(transcripts) == {r.task_id for r in runs}
    for run in runs:
        t = transcripts[run.task_id]
        assert t.run_id == run.id and t.task["status"] == run.status == "succeeded"
        assert t.events[0] == {"id": t.events[0]["id"], "type": "status", "data": {"status": "queued"}}
        assert t.events[-1]["data"] == {"status": "succeeded"}
        assert any(e["type"] == "session" for e in t.events)

    # A redelivered step after a crash between finish and archive: same run, no second row.
    assert not await archive_task(f.db, f.store, runs[0].task_id, runs[0].id)
    async with f.db.session() as s:
        assert len((await s.scalars(select(TaskTranscript))).all()) == 3


async def test_a_failed_archive_does_not_hold_up_the_item(make_factory, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(steps, "archive_task", broken)
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()
    assert (await f.item(item_id)).state == "awaiting_merge"
    async with f.db.session() as s:
        assert (await s.scalars(select(TaskTranscript))).all() == []


async def test_cancel_supersedes_the_open_request(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()
    req = await f.open_request(item_id)

    assert await f.engine.cancel(item_id)
    assert (await f.item(item_id)).state == "cancelled"
    with pytest.raises(EngineError, match="already answered"):
        await f.engine.answer(req.id, "merged")
    assert not await f.engine.cancel(item_id)


async def test_delete_removes_a_cancelled_item_with_its_history(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    child_id = await f.new_item()
    async with f.db.session() as s:
        await s.execute(update(WorkItem).where(WorkItem.id == child_id).values(parent_id=item_id))
        await s.commit()
    await f.engine.start(item_id)
    await f.drain()
    await f.open_request(item_id)
    with pytest.raises(EngineError, match="only failed or cancelled"):
        await f.engine.delete(item_id)
    async with f.db.session() as s:
        run = (await s.scalars(select(Run).where(Run.item_id == item_id))).first()
        s.add(TaskTranscript(task_id="t1", run_id=run.id, task={}, events=[]))
        await s.commit()

    assert await f.engine.cancel(item_id)
    await f.engine.delete(item_id)

    async with f.db.session() as s:
        assert await s.get(WorkItem, item_id) is None
        for model in (Run, Artifact, HumanRequest, ItemEvent):
            assert (await s.scalars(select(model).where(model.item_id == item_id))).all() == []
        assert (await s.scalars(select(TaskTranscript))).all() == []
        assert (await s.get(WorkItem, child_id)).parent_id is None
    with pytest.raises(EngineError, match="not found"):
        await f.engine.delete(item_id)


async def test_start_twice_and_bad_answers(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    with pytest.raises(EngineError, match="already in state"):
        await f.engine.start(item_id)
    await f.drain()
    req = await f.open_request(item_id)
    with pytest.raises(EngineError, match="one of"):
        await f.engine.answer(req.id, "yolo")


async def test_reconcile_requeues_a_lost_step_once(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    f.queue.clear()  # the enqueue was lost

    assert await f.engine.reconcile() == []  # too fresh to call it lost
    async with f.db.session() as s:
        old = utcnow() - timedelta(seconds=settings.step_stale_seconds + 1)
        await s.execute(update(WorkItem).where(WorkItem.id == item_id).values(updated_at=old))
        await s.commit()
    assert await f.engine.reconcile() == [item_id]
    assert await f.engine.reconcile() == []  # not again within the stale period
    await f.drain()
    assert (await f.item(item_id)).state == "awaiting_merge"


async def test_red_ci_sends_the_story_back_with_the_failing_log(make_factory, origin):
    f = make_factory(ScriptedRunner())
    f.gh.checks[0]["conclusion"] = "failure"
    item_id = await f.new_item()
    await f.engine.start(item_id)
    # Run until CI has sent the story back, but not the fix itself yet.
    while (await f.path(item_id))[-2:] != ["ci", "implement"]:
        if f.queue:
            await f.step(*f.queue.pop(0))
        else:
            assert await poll_checks(f.engine, github_factory=lambda _project: f.gh)

    assert await f.path(item_id) == ["plan", "implement", "sync_base", "review", "publish", "ci", "implement"]
    f.gh.checks[0]["conclusion"] = "success"  # the fix works
    await f.drain()

    assert (await f.item(item_id)).state == "awaiting_merge"
    fix = f.runner.of("implement")[1]
    assert fix["resume"] == "implement-0"
    assert "FAILED test_hello.py::test_says_hi" in fix["prompt"] and "test: failure" in fix["prompt"]
    assert len(f.gh.prs) == 1  # the fix updated the same PR
    later = f.runner.of("review")[1]["prompt"]
    assert "CI failed" in later  # the reviewer of the fix still sees why it was needed


async def test_ci_waits_for_pending_checks_and_times_out(make_factory):
    f = make_factory(ScriptedRunner())
    f.gh.checks[0].update(status="in_progress", conclusion=None)
    f.gh.statuses = [{"context": "legacy-ci", "state": "success"}]
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()
    assert (await f.item(item_id)).state == "ci"  # still pending

    async with f.db.session() as s:
        old = utcnow() - timedelta(seconds=3601)
        await s.execute(update(WorkItem).where(WorkItem.id == item_id).values(updated_at=old))
        await s.commit()
    await f.drain()
    assert (await f.item(item_id)).state == "needs_human"


async def test_legacy_status_failure_and_no_checks(make_factory):
    f = make_factory(ScriptedRunner())
    f.gh.checks = []
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()
    assert (await f.item(item_id)).state == "ci"  # nothing reported yet, within grace

    f.gh.statuses = [{"context": "jenkins", "state": "failure", "description": "build broke"}]
    await f.drain()
    async with f.db.session() as s:
        ci = (await s.scalars(select(Artifact.content).where(Artifact.name == "ci"))).all()
    assert "jenkins: failure" in ci[0] and "build broke" in ci[0]


async def test_planner_questions_go_to_a_person_and_back(make_factory):
    f = make_factory(ScriptedRunner(plans=["needs_clarification", "ready"]))
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    assert await f.path(item_id) == ["plan", "clarify"]
    req = await f.open_request(item_id)
    assert req.options == ["answered", "abandon"]
    await f.engine.answer(req.id, "answered", comment="Lowercase, no punctuation.")
    await f.drain()

    assert (await f.item(item_id)).state == "awaiting_merge"
    replan = f.runner.of("plan")[1]
    assert replan["resume"] == "plan-0"  # the planner continues with its own questions in context
    assert "Lowercase, no punctuation." in replan["prompt"]
    assert "Plan 1: ready" in f.runner.of("implement")[0]["prompt"]


async def test_too_big_story_is_escalated_and_can_be_replanned(make_factory):
    f = make_factory(ScriptedRunner(plans=["too_big", "ready"]))
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    assert await f.path(item_id) == ["plan", "needs_human"]
    assert f.runner.of("implement") == []
    req = await f.open_request(item_id)
    await f.engine.answer(req.id, "replan", comment="Only the greeting file, skip the rest.")
    await f.drain()
    assert (await f.item(item_id)).state == "awaiting_merge"


def land_on_main(origin, tmp_path, filename, content, name="other"):
    """Another story's PR gets merged: a commit lands on origin/main."""
    clone = tmp_path / f"land-{name}"
    if not clone.exists():
        git(tmp_path, "clone", "-q", str(origin), str(clone))
    git(clone, "pull", "-q", "origin", "main")
    (clone / filename).write_text(content)
    git(clone, "add", ".")
    git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", f"{name}: {filename}")
    git(clone, "push", "-q", "origin", "main")


class ConflictingMerge(ScriptedRunner):
    """While the first implement run works, another story changes the same file on main."""

    def __init__(self, origin, tmp_path, **kw):
        super().__init__(**kw)
        self.origin, self.tmp_path = origin, tmp_path

    async def __call__(self, spec, *, cwd, resume, emit):
        out = await super().__call__(spec, cwd=cwd, resume=resume, emit=emit)
        if spec.metadata["state"] == "implement" and len(self.of("implement")) == 1:
            land_on_main(self.origin, self.tmp_path, "hello.txt", "HELLO from main\n")
            land_on_main(self.origin, self.tmp_path, "other.txt", "unrelated\n")
        return out


async def test_sync_base_hands_merge_conflicts_to_the_implementer(make_factory, origin, tmp_path):
    f = make_factory(ConflictingMerge(origin, tmp_path))
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    assert await f.path(item_id) == [*HAPPY_PATH[:3], "implement", "sync_base", *HAPPY_PATH[3:]]
    resolve = f.runner.of("implement")[1]
    assert resolve["resume"] == "implement-0"
    assert "conflicts in:\n- hello.txt" in resolve["prompt"] and "Do not abort the merge" in resolve["prompt"]

    branch = (await f.item(item_id)).branch
    assert git(origin, "show", f"{branch}:hello.txt") == "hi 1\n"  # the implementer's resolution
    assert git(origin, "show", f"{branch}:other.txt") == "unrelated\n"  # main's work came along
    assert git(origin, "log", "--merges", "--format=%s", branch).strip()  # concluded as a merge commit
    # The PR now merges cleanly: main is an ancestor of the branch.
    assert subprocess.run(["git", "merge-base", "--is-ancestor", "main", branch], cwd=origin).returncode == 0


async def test_update_brings_an_open_pr_up_to_date(make_factory, origin, tmp_path):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()
    land_on_main(origin, tmp_path, "docs.md", "# docs\n")

    req = await f.open_request(item_id)
    await f.engine.answer(req.id, "update")
    await f.drain()

    tail = ["awaiting_merge", "sync_base", "review", "publish", "ci", "merge", "awaiting_merge"]
    assert (await f.path(item_id))[-7:] == tail
    assert len(f.runner.of("implement")) == 1  # a clean merge needs no agent to fix anything
    branch = (await f.item(item_id)).branch
    assert git(origin, "show", f"{branch}:docs.md") == "# docs\n"
    async with f.db.session() as s:
        report = (await s.scalars(select(Artifact.content).where(Artifact.name == "sync_base"))).all()
    assert report[0] == "Up to date with origin/main." and report[-1].startswith("Merged origin/main cleanly")


async def test_sync_base_does_not_commit_unresolved_conflicts(make_factory, origin, tmp_path):
    class LeavesMarkers(ConflictingMerge):
        async def __call__(self, spec, *, cwd, resume, emit):
            if spec.metadata["state"] == "implement" and len(self.of("implement")) == 1:
                self.calls.append({"role": "implement", "prompt": spec.prompt, "resume": resume, "spec": spec})
                return AgentOutcome("implement-1", "gave up", False)  # merge still in progress
            return await super().__call__(spec, cwd=cwd, resume=resume, emit=emit)

    f = make_factory(LeavesMarkers(origin, tmp_path))
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    path = await f.path(item_id)
    assert path[:6] == ["plan", "implement", "sync_base", "implement", "sync_base", "implement"]
    async with f.db.session() as s:
        reports = (await s.scalars(select(Artifact.content).where(Artifact.name == "sync_base"))).all()
    assert reports[1].startswith("The merge of origin/main still has conflicts")
    assert "<<<<<<<" not in git(origin, "show", f"{(await f.item(item_id)).branch}:hello.txt")


async def test_merged_pr_moves_the_story_to_done_by_itself(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()
    assert (await f.item(item_id)).state == "awaiting_merge"  # PR still open: keeps waiting

    f.gh.pr_state = {"state": "closed", "merged_at": "2026-10-29T10:00:00Z"}
    await f.drain()
    assert (await f.item(item_id)).state == "done"
    assert await f.open_request(item_id) is None


async def test_pr_closed_without_merge_abandons_the_story(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    f.gh.pr_state = {"state": "closed", "merged_at": None}
    await f.drain()
    assert (await f.item(item_id)).state == "cancelled"


async def test_workspace_is_removed_once_the_story_is_merged(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()
    assert item_workspace(item_id).is_dir()
    assert f.cleaned == []

    f.gh.pr_state = {"state": "closed", "merged_at": "2026-10-29T10:00:00Z"}
    await f.drain()
    assert (await f.item(item_id)).state == "done"
    assert f.cleaned == [item_id]
    assert not item_workspace(item_id).exists()


async def test_workspace_is_kept_when_the_story_is_cancelled_so_it_can_be_reopened(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    assert await f.engine.cancel(item_id)
    assert f.cleaned == []
    assert item_workspace(item_id).is_dir()


async def test_failing_cleanup_does_not_block_the_transition(make_factory):
    f = make_factory(ScriptedRunner())

    async def broken(item_id):
        raise ConnectionError("broker is down")

    f.engine.cleanup = broken
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()
    f.gh.pr_state = {"state": "closed", "merged_at": "2026-10-29T10:00:00Z"}
    await f.drain()
    assert (await f.item(item_id)).state == "done"


async def test_is_succeeded_tells_merged_stories_from_the_rest(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    assert not await f.engine.is_succeeded(item_id)
    await f.engine.start(item_id)
    await f.drain()
    assert not await f.engine.is_succeeded(item_id)
    f.gh.pr_state = {"state": "closed", "merged_at": "2026-10-29T10:00:00Z"}
    await f.drain()
    assert await f.engine.is_succeeded(item_id)
    assert not await f.engine.is_succeeded("missing")


def test_removing_a_missing_workspace_is_a_no_op():
    assert remove_item_workspace("nope") is False


async def test_poll_checks_can_be_limited_to_some_items(make_factory):
    f = make_factory(ScriptedRunner())
    f.gh.checks[0].update(status="in_progress", conclusion=None)
    a = await f.new_item()
    async with f.db.session() as s:
        b = WorkItem(project_id=(await s.get(WorkItem, a)).project_id, kind="story", title="Other",
                     body="Create hello.txt saying hi.", workflow="story")
        s.add(b)
        await s.commit()
        b = b.id
    for item_id in (a, b):
        await f.engine.start(item_id)
    await f.drain()
    assert [(await f.item(i)).state for i in (a, b)] == ["ci", "ci"]

    f.gh.checks[0].update(status="completed", conclusion="success")
    assert await poll_checks(f.engine, github_factory=lambda _project: f.gh, item_ids=[a]) == [(a, "passed")]
    assert [(await f.item(i)).state for i in (a, b)] == ["merge", "ci"]
    assert await poll_checks(f.engine, github_factory=lambda _project: f.gh, item_ids=[]) == []


async def test_start_waits_for_blockers_to_succeed(make_factory):
    f = make_factory(ScriptedRunner())
    blocker_id = await f.new_item()
    async with f.db.session() as s:
        project_id = (await s.get(WorkItem, blocker_id)).project_id
        dependent = WorkItem(project_id=project_id, kind="story", title="Next", workflow="story",
                             blocked_by=[blocker_id, "gone"])
        s.add(dependent)
        await s.commit()
        dependent_id = dependent.id

    with pytest.raises(EngineError, match=rf"blocked by: Add greeting \({blocker_id}\), gone"):
        await f.engine.start(dependent_id)
    assert (await f.item(dependent_id)).state is None

    await f.engine.start(blocker_id)
    await f.engine.cancel(blocker_id)  # a failed or cancelled blocker still blocks
    with pytest.raises(EngineError, match="blocked by"):
        await f.engine.start(dependent_id)

    async with f.db.session() as s:
        await s.execute(update(WorkItem).where(WorkItem.id == blocker_id).values(state="done"))
        (await s.get(WorkItem, dependent_id)).blocked_by = [blocker_id]
        await s.commit()
    assert await f.engine.start(dependent_id)


async def test_automerge_merges_the_reviewed_green_pr(make_factory, origin):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item(auto_merge=True)

    assert await f.engine.start(item_id)
    await f.drain()

    assert await f.path(item_id) == ["plan", "implement", "sync_base", "review", "publish", "ci", "merge", "done"]
    item = await f.item(item_id)
    assert f.gh.merges == [{"number": 1, "method": "squash", "sha": item.head_sha}]
    assert await f.open_request(item_id) is None
    async with f.db.session() as s:
        report = await s.scalar(select(Artifact.content).where(Artifact.name == "merge"))
    assert report == "Automerge: merged PR #1 (squash)."


async def test_refused_automerge_leaves_the_pr_to_a_human(make_factory, origin):
    f = make_factory(ScriptedRunner())
    f.gh.merge_refusal = 405
    item_id = await f.new_item(auto_merge=True)

    assert await f.engine.start(item_id)
    await f.drain()

    assert (await f.path(item_id))[-2:] == ["merge", "awaiting_merge"]
    assert (await f.open_request(item_id)).options == ["merged", "update", "rework", "abandon"]
    async with f.db.session() as s:
        report = await s.scalar(select(Artifact.content).where(Artifact.name == "merge"))
    assert report == "Automerge: GitHub refused to merge PR #1 (405): Pull Request is not mergeable"


async def test_without_automerge_the_pr_is_not_merged(make_factory, origin):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()

    assert await f.engine.start(item_id)
    await f.drain()

    assert (await f.path(item_id))[-2:] == ["merge", "awaiting_merge"]
    assert f.gh.merges == []


async def test_reconcile_loads_only_steps_a_worker_can_be_running(make_factory):
    f = make_factory(ScriptedRunner())
    working = await f.new_item()
    await f.engine.start(working)
    await f.new_item()  # stays in the backlog
    finished = [await f.new_item() for _ in range(3)]
    async with f.db.session() as s:
        for item_id, state in zip(finished, ("awaiting_merge", "done", "failed")):
            await s.execute(update(WorkItem).where(WorkItem.id == item_id).values(state=state))
        await s.execute(update(WorkItem).where(WorkItem.id == finished[0]).values(workflow="gone"))
        await s.commit()

    loaded = []

    def on_load(target, _ctx):
        loaded.append(target.id)

    event.listen(WorkItem, "load", on_load)
    try:
        await f.engine.reconcile()
    finally:
        event.remove(WorkItem, "load", on_load)
    assert loaded == [working]
