from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.config import settings
from app.db import Artifact, HumanRequest, ItemEvent, Project, Run, WorkItem, utcnow
from app.engine import Engine, EngineError
from app.runner import AgentOutcome
from app.steps import run_step
from app.workflow import load_workflows
from tests.test_issue_flow import FakeGitHub, git, origin  # noqa: F401  (origin is a fixture)


class Factory:
    """Engine with an in-process queue: `drain()` runs enqueued steps like a worker would."""

    def __init__(self, db, store, gh, runner):
        self.queue: list[tuple[str, int]] = []
        self.gh, self.runner, self.store = gh, runner, store

        async def enqueue(item_id, version):
            self.queue.append((item_id, version))

        self.engine = Engine(db, load_workflows(settings.workflows_dir), store, enqueue)
        self.db = db

    async def step(self, item_id, version):
        await run_step(
            item_id, version, engine=self.engine, store=self.store, worker_id="w1",
            runner=self.runner, github_factory=lambda: self.gh,
        )

    async def drain(self):
        while self.queue:
            await self.step(*self.queue.pop(0))

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

    async def new_item(self, **kw) -> str:
        async with self.db.session() as s:
            project = Project(slug="app", repo="acme/app", verify_command="test -f hello.txt")
            s.add(project)
            await s.flush()
            item = WorkItem(project_id=project.id, kind="story", title="Add greeting",
                            body="Create hello.txt saying hi.", workflow="story", **kw)
            s.add(item)
            await s.commit()
            return item.id


class ScriptedRunner:
    """Implementer commits a file; reviewer returns the next verdict from `reviews`."""

    def __init__(self, reviews=("approved",), implement_ok=True):
        self.reviews = list(reviews)
        self.implement_ok = implement_ok
        self.calls: list[dict] = []

    async def __call__(self, spec, *, cwd, resume, emit):
        role = spec.metadata["state"]
        n = len(self.calls)
        self.calls.append({"role": role, "prompt": spec.prompt, "resume": resume, "spec": spec})
        await emit("session", {"session_id": f"{role}-{n}"})
        if role == "implement":
            if not self.implement_ok:
                return AgentOutcome(f"{role}-{n}", None, True, error="max turns")
            (cwd / "hello.txt").write_text(f"hi {n}\n")
            git(cwd, "add", ".")
            git(cwd, "commit", "-m", f"greeting {n}")
            return AgentOutcome(f"{role}-{n}", f"Implemented greeting (call {n}).", False, cost_usd=1.0)
        verdict = self.reviews.pop(0) if len(self.reviews) > 1 else self.reviews[0]
        return AgentOutcome(
            f"{role}-{n}", "ignored", False, cost_usd=0.5,
            structured_output={"verdict": verdict, "summary": f"Review {n}: {verdict}, fix hello.txt:1"},
        )


@pytest.fixture
def make_factory(db, store, origin):  # noqa: F811
    def make(runner):
        return Factory(db, store, FakeGitHub(origin), runner)

    return make


async def test_story_happy_path(make_factory, origin):  # noqa: F811
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item(source={"issue": 7})

    assert await f.engine.start(item_id)
    await f.drain()

    assert await f.path(item_id) == ["implement", "review", "publish", "awaiting_merge"]
    item = await f.item(item_id)
    assert item.branch == f"agent/story-{item_id[:8]}"
    assert item.pr_url == "https://gh/pr/1"
    assert set(item.sessions) == {"implementer", "reviewer"}
    pr = f.gh.prs[0]
    assert pr["head"] == item.branch and pr["base"] == "main"
    assert "Closes #7" in pr["body"] and "Implemented greeting" in pr["body"] and "Review 1: approved" in pr["body"]
    assert "hi 0" in git(origin, "show", f"{item.branch}:hello.txt")

    review = f.runner.calls[1]
    assert review["spec"].output_schema["properties"]["verdict"]["enum"] == ["approved", "changes_requested", "needs_human"]
    assert review["spec"].disallowed_tools == ["Write", "Edit"]
    assert review["resume"] is None  # fresh context: the reviewer did not write the code
    assert '<artifact name="implementer">' in review["prompt"]

    req = await f.open_request(item_id)
    assert req.options == ["merged", "rework", "abandon"]
    assert await f.engine.answer(req.id, "merged")
    assert (await f.item(item_id)).state == "done"
    async with f.db.session() as s:
        runs = (await s.scalars(select(Run).where(Run.item_id == item_id).order_by(Run.created_at))).all()
    assert [(r.state, r.verdict, r.status) for r in runs] == [
        ("implement", "done", "succeeded"),
        ("review", "approved", "succeeded"),
    ]


async def test_review_feedback_loops_back_to_the_same_implementer_session(make_factory):
    f = make_factory(ScriptedRunner(reviews=["changes_requested", "approved"]))
    item_id = await f.new_item()
    await f.engine.start(item_id)
    await f.drain()

    assert await f.path(item_id) == ["implement", "review", "implement", "review", "publish", "awaiting_merge"]
    second_impl = f.runner.calls[2]
    assert second_impl["resume"] == "implement-0"
    assert "Review 1: changes_requested" in second_impl["prompt"]


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
    assert "try a smaller change" in f.runner.calls[-2]["prompt"]


async def test_duplicate_and_stale_steps_are_no_ops(make_factory):
    f = make_factory(ScriptedRunner())
    item_id = await f.new_item()
    await f.engine.start(item_id)
    (step,) = f.queue
    f.queue.clear()

    await f.step(*step)
    await f.step(*step)  # redelivered message: the item has moved on
    assert len(f.runner.calls) == 1
    assert await f.path(item_id) == ["implement", "review"]

    assert not await f.engine.advance(item_id, step[1], "done")  # stale verdict


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
