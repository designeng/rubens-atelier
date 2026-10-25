"""Task submission shared by the HTTP API and the issue poller."""

import asyncio
import logging

from . import issue_flow
from .celery_app import run_agent
from .config import settings
from .github import GitHub
from .models import IssueRef, TaskCreate, TaskInfo
from .store import TaskStore

log = logging.getLogger(__name__)


class SubmitError(ValueError):
    pass


async def submit(store: TaskStore, spec: TaskCreate) -> TaskInfo:
    if spec.parent_task_id:
        parent = await store.get(spec.parent_task_id)
        if parent is None:
            raise SubmitError(f"parent task {spec.parent_task_id} not found")
        if not parent.status.is_terminal:
            raise SubmitError("parent task is still running; it shares the workspace")
        if not parent.workspace:
            raise SubmitError("parent task has no workspace to continue in")
        parent_spec = await store.get_spec(spec.parent_task_id)
        if spec.issue is None and parent_spec.issue is not None:
            # Follow-ups on an issue task push to the same branch and update the same PR.
            spec = spec.model_copy(update={"issue": parent_spec.issue})
        if not spec.prompt.strip():
            raise SubmitError("follow-up tasks need a prompt")
    if spec.issue and not settings.github_token:
        raise SubmitError("issue tasks require HARNESS_GITHUB_TOKEN")

    task_id = await store.create(spec)
    await asyncio.to_thread(run_agent.apply_async, args=[task_id], task_id=task_id)
    return await store.get(task_id)  # type: ignore[return-value]


async def poll_issues(store: TaskStore) -> list[str]:
    """Enqueue a task for every open issue labelled `settings.issue_label`."""
    gh = GitHub()
    created: list[str] = []
    try:
        for repo in settings.issue_repos:
            try:
                issues = await gh.list_open_issues(repo, settings.issue_label)
            except Exception:
                log.exception("listing issues for %s failed", repo)
                continue
            for item in issues:
                lock = f"harness:issue-lock:{repo}#{item['number']}"
                # NX lock guards against overlapping polls; swapping the label right away
                # keeps the next poll from picking the issue up again.
                if not await store.r.set(lock, "1", nx=True, ex=600):
                    continue
                await gh.set_labels(
                    repo, item["number"], add=[issue_flow.IN_PROGRESS], remove=[settings.issue_label]
                )
                info = await submit(store, TaskCreate(issue=IssueRef(repo=repo, number=item["number"])))
                created.append(info.id)
                log.info("queued %s for %s#%s", info.id, repo, item["number"])
    finally:
        await gh.aclose()
    return created
