from types import SimpleNamespace

from sqlalchemy import select

from app.budget import project_spending, remaining
from app.config import settings
from app.db import Artifact, Project, Run, WorkItem
from app.execution import item_workspace
from app.models import FailureReason
from app.steps import _agent
from app.workflow import load_workflows
from tests.test_execution import FakeRunner


async def _seed(db, *, project_budget=None, epic_budget=None, story_budget=None, spent=()):
    """A project with an epic and a story under it; `spent` = [(item key, cost)] as finished runs."""
    async with db.session() as s:
        project = Project(slug="p", repo="o/r", budget_usd=project_budget)
        s.add(project)
        await s.flush()
        epic = WorkItem(project_id=project.id, kind="epic", title="E", workflow="story", budget_usd=epic_budget)
        s.add(epic)
        await s.flush()
        story = WorkItem(
            project_id=project.id, parent_id=epic.id, kind="story", title="S", workflow="story",
            state="implement", version=5, budget_usd=story_budget,
        )
        s.add(story)
        await s.flush()
        items = {"epic": epic, "story": story}
        for i, (key, cost) in enumerate(spent):
            s.add(Run(item_id=items[key].id, version=i, state="x", role="r", task_id=f"t{i}", cost_usd=cost))
        await s.commit()
        return project, epic, story


async def _step(db, store, project, story, runner):
    engine = SimpleNamespace(db=db, scheduler=None)
    item_workspace(story.id).mkdir(parents=True)  # what ensure_workspace does for a real step
    st = load_workflows(settings.workflows_dir)["story"].states["implement"]
    return await _agent(story, project, st, engine, store, "w1", runner)


async def test_spending_adds_up_descendants_and_ignores_missing_costs(db):
    project, epic, story = await _seed(db, spent=[("story", 1.5), ("epic", 0.5), ("story", None)])
    async with db.session() as s:
        spending = await project_spending(s, project.id)
    assert spending.item(story.id) == 1.5
    assert spending.item(epic.id) == 2.0
    assert spending.project == 2.0


async def test_remaining_is_the_tightest_limit_on_the_way_up(db):
    project, epic, story = await _seed(db, project_budget=10, epic_budget=4, story_budget=3, spent=[("story", 1.0)])
    async with db.session() as s:
        assert await remaining(s, story, project) == (2.0, "story 'S'")
        epic.budget_usd = 1.5
        s.add(epic)
        await s.commit()
        assert await remaining(s, story, project) == (0.5, "epic 'E'")


async def test_remaining_without_limits(db):
    project, _, story = await _seed(db, spent=[("story", 100.0)])
    async with db.session() as s:
        assert await remaining(s, story, project) == (None, None)


async def test_exhausted_budget_stops_the_run_before_the_agent_starts(db, store):
    project, _, story = await _seed(db, story_budget=2.0, spent=[("story", 2.0)])
    runner = FakeRunner()

    verdict = await _step(db, store, project, story, runner)

    assert verdict == FailureReason.budget.value
    assert runner.calls == []
    async with db.session() as s:
        assert await s.scalar(select(Run).where(Run.item_id == story.id, Run.version == story.version)) is None
        (error,) = await s.scalars(select(Artifact).where(Artifact.item_id == story.id))
    assert error.name == "error" and "budget of the story 'S' is used up" in error.content


async def test_project_or_epic_limit_blocks_the_child(db, store):
    for kwargs in ({"project_budget": 1.0}, {"epic_budget": 1.0}):
        project, _, story = await _seed(db, spent=[("story", 1.0)], **kwargs)
        assert await _step(db, store, project, story, FakeRunner()) == "budget"
        async with db.session() as s:
            for model in (Artifact, Run, WorkItem, Project):
                for row in await s.scalars(select(model)):
                    await s.delete(row)
            await s.commit()


async def test_run_is_capped_by_what_is_left(db, store):
    project, _, story = await _seed(db, story_budget=3.0, spent=[("story", 2.5)])
    seen = []
    runner = FakeRunner(script=lambda n, spec, cwd, resume: seen.append(spec.max_budget_usd))

    await _step(db, store, project, story, runner)

    assert seen == [0.5]


async def test_run_keeps_the_default_cap_when_the_limit_is_generous_or_missing(db, store):
    project, _, story = await _seed(db, story_budget=100.0)
    seen = []
    runner = FakeRunner(script=lambda n, spec, cwd, resume: seen.append(spec.max_budget_usd))
    await _step(db, store, project, story, runner)
    assert seen == [settings.default_max_budget_usd]
