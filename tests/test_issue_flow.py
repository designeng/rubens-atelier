from pathlib import Path

from app import issue_flow
from app.execution import execute_task
from app.models import IssueRef, TaskCreate, TaskStatus
from app.runner import AgentOutcome
from tests.conftest import FakeGitHub, git


def committing_runner(filename: str, content: str, calls: list):
    async def runner(spec, *, cwd, resume, emit):
        calls.append({"prompt": spec.prompt, "resume": resume})
        await emit("session", {"session_id": "sess-1"})
        (cwd / filename).write_text(content)
        git(cwd, "add", ".")
        git(cwd, "commit", "-m", f"{filename} for #7")
        return AgentOutcome("sess-1", "Added hello.txt. Verified with test -f.", False, cost_usd=1.0, num_turns=4)

    return runner


async def test_issue_end_to_end(store, origin):
    gh = FakeGitHub(origin)
    calls: list = []
    task_id = await store.create(
        TaskCreate(issue=IssueRef(repo="acme/app", number=7), verify_command="test -f hello.txt")
    )
    runner = committing_runner("hello.txt", "hi\n", calls)
    await execute_task(task_id, store, "w1", runner=runner, github_factory=lambda: gh)

    info = await store.get(task_id)
    assert info.status == TaskStatus.succeeded, info.error
    assert info.pr_url == "https://gh/pull/1"
    assert info.branch == "agent/issue-7"

    prompt = calls[0]["prompt"]
    assert "Create hello.txt saying hi." in prompt and "Lowercase please." in prompt
    assert "agent/issue-7" in prompt

    # Branch landed on the remote with the agent's commit; token never persisted in .git/config.
    assert "hello.txt for #7" in git(origin, "log", "--oneline", "agent/issue-7")
    assert "test-token" not in (Path(info.workspace) / ".git" / "config").read_text()

    pr = gh.prs[0]
    assert pr["base"] == "main" and pr["title"] == "Add greeting"
    assert pr["body"].startswith("Closes #7") and "Verified with test -f" in pr["body"]
    assert gh.labels == {issue_flow.PR_OPEN}


async def test_issue_without_commits_fails_and_reports(store, origin):
    gh = FakeGitHub(origin)

    async def lazy_runner(spec, *, cwd, resume, emit):
        return AgentOutcome("s", "nothing to do", False)

    task_id = await store.create(TaskCreate(issue=IssueRef(repo="acme/app", number=7)))
    await execute_task(task_id, store, "w1", runner=lazy_runner, github_factory=lambda: gh)

    info = await store.get(task_id)
    assert info.status == TaskStatus.failed
    assert "without producing any commits" in info.error
    assert gh.labels == {issue_flow.FAILED}
    assert gh.prs == [] and "did not complete" in gh.comments[-1]


async def test_follow_up_updates_same_pr(store, origin):
    gh = FakeGitHub(origin)
    calls: list = []
    first = await store.create(TaskCreate(issue=IssueRef(repo="acme/app", number=7)))
    runner = committing_runner("hello.txt", "hi\n", calls)
    await execute_task(first, store, "w1", runner=runner, github_factory=lambda: gh)

    issue = (await store.get_spec(first)).issue
    second = await store.create(TaskCreate(prompt="Address review: add a newline", parent_task_id=first, issue=issue))
    runner = committing_runner("hello.txt", "hi\n\n", calls)
    await execute_task(second, store, "w1", runner=runner, github_factory=lambda: gh)

    assert (await store.get(second)).status == TaskStatus.succeeded
    assert calls[1]["resume"] == "sess-1" and calls[1]["prompt"].startswith("Address review")
    assert len(gh.prs) == 1  # reused, not duplicated
    assert len(git(origin, "log", "--oneline", "agent/issue-7").splitlines()) == 3
