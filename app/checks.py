"""Check states: external conditions the workflow waits for without holding a worker.

`poll_checks` evaluates items sitting in a check state, and in a human state that `watch`es
a check (the person may answer first; whoever does wins). GitHub webhooks (app.webhooks)
trigger it for just the items an event concerns; celery beat runs it over all of them as a
fallback for lost deliveries and for time-based verdicts (no_checks, timeout). A check
returns a verdict once it knows the outcome, or None to be asked again next poll;
details (e.g. which CI jobs failed and their log tails) become an artifact that the
next agent sees.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass

from sqlalchemy import select

from .db import Artifact, Project, WorkItem, aware, utcnow
from .engine import Engine
from .github import GitHub
from .workflow import ERROR, State

log = logging.getLogger(__name__)

# Conclusions that fail the gate. neutral/skipped/success pass.
FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
MAX_LOGS = 3
LOG_TAIL = 5_000


@dataclass
class CheckContext:
    gh: GitHub
    project: Project
    item: WorkItem
    state: State
    waited: float  # seconds since the item entered the state


@dataclass
class Result:
    name: str
    done: bool
    conclusion: str | None
    url: str | None = None
    summary: str | None = None
    job_id: int | None = None  # GitHub Actions job, whose log we can fetch


Check = Callable[[CheckContext], Awaitable[tuple[str | None, str]]]


async def ci(ctx: CheckContext) -> tuple[str | None, str]:
    """GitHub checks and commit statuses on the PR's head commit."""
    repo, sha = ctx.project.repo, ctx.item.head_sha
    if not sha:
        return ERROR, "no pushed commit is recorded for this item"
    results = [
        Result(
            name=r["name"],
            done=r["status"] == "completed",
            conclusion=r.get("conclusion"),
            url=r.get("details_url") or r.get("html_url"),
            summary=(r.get("output") or {}).get("summary"),
            job_id=r["id"] if (r.get("app") or {}).get("slug") == "github-actions" else None,
        )
        for r in await ctx.gh.check_runs(repo, sha)
    ]
    for st in (await ctx.gh.commit_status(repo, sha)).get("statuses", []):
        state = st["state"]  # pending | success | failure | error
        results.append(
            Result(st["context"], state != "pending", "success" if state == "success" else "failure",
                   st.get("target_url"), st.get("description"))
        )

    failed = [r for r in results if r.done and r.conclusion in FAILED_CONCLUSIONS]
    if failed:
        return "failed", await _describe(ctx.gh, repo, sha, failed)
    # Passing outcomes get an artifact too, superseding an earlier failure report.
    if not results:
        if ctx.waited >= ctx.state.grace_seconds:
            return "no_checks", f"No CI checks reported on {sha[:12]}."
        return None, ""
    if all(r.done for r in results):
        return "passed", f"CI passed on {sha[:12]}: " + ", ".join(r.name for r in results)
    return None, ""


async def _describe(gh: GitHub, repo: str, sha: str, failed: list[Result]) -> str:
    parts = [f"CI failed on {sha[:12]}. Find the root cause, fix it and commit; do not disable checks."]
    for i, r in enumerate(failed):
        parts.append(f"### {r.name}: {r.conclusion}\n{r.url or ''}\n{r.summary or ''}".rstrip())
        if r.job_id and i < MAX_LOGS:
            try:
                log_text = await gh.job_log(repo, r.job_id)
                parts.append(f"```\n{log_text[-LOG_TAIL:]}\n```")
            except Exception as e:
                parts.append(f"(log unavailable: {e})")
    return "\n\n".join(parts)


async def merge(ctx: CheckContext) -> tuple[str | None, str]:
    """Has the item's PR been merged (`merged`) or closed without merging (`abandon`)?"""
    match = re.search(r"/pull/(\d+)", ctx.item.pr_url or "")
    if not match:
        return None, ""
    pr = await ctx.gh.get_pr(ctx.project.repo, int(match[1]))
    if pr.get("merged_at") or pr.get("merged"):
        return "merged", f"PR #{pr['number']} was merged on GitHub."
    if pr.get("state") == "closed":
        return "abandon", f"PR #{pr['number']} was closed on GitHub without merging."
    return None, ""


CHECKS: dict[str, Check] = {"ci": ci, "merge": merge}


async def celery_enqueue(item_ids: list[str]) -> None:
    """Evaluate just these items on a worker (see app.webhooks)."""
    from .celery_app import poll_checks as task

    await asyncio.to_thread(task.apply_async, args=[item_ids])


def watched_states(engine: Engine) -> set[str]:
    """Names of the states whose items `poll_checks` evaluates."""
    return {n for wf in engine.workflows.values() for n, st in wf.states.items() if st.check or st.watch}


async def poll_checks(
    engine: Engine,
    github_factory: Callable[[Project], GitHub] = GitHub.for_project,
    item_ids: Collection[str] | None = None,
) -> list[tuple[str, str]]:
    """Evaluate every item in a check state (only `item_ids`, if given); returns
    (item_id, verdict) for those that moved."""
    check_states = watched_states(engine)
    if not check_states or item_ids is not None and not item_ids:
        return []
    query = select(WorkItem).where(WorkItem.state.in_(check_states))
    if item_ids is not None:
        query = query.where(WorkItem.id.in_(item_ids))
    async with engine.db.session() as s:
        items = (await s.scalars(query)).all()
        projects = {p.id: p for p in await s.scalars(select(Project).where(Project.id.in_({i.project_id for i in items})))}
    if not items:
        return []

    moved = []
    clients: dict[int | None, GitHub] = {}
    try:
        for item in items:
            wf = engine.workflows.get(item.workflow)
            st = wf.states.get(item.state) if wf else None
            name = st.check or st.watch if st else None
            if name is None:
                continue
            waited = (utcnow() - aware(item.updated_at)).total_seconds()
            check = CHECKS.get(name)
            if check is None:
                verdict, details = ERROR, f"unknown check {name!r}"
            else:
                try:
                    project = projects[item.project_id]
                    if project.installation_id not in clients:
                        clients[project.installation_id] = github_factory(project)
                    verdict, details = await check(CheckContext(clients[project.installation_id], project, item, st, waited))
                except Exception:
                    # Transient (GitHub hiccup) until proven otherwise: try again next poll.
                    log.exception("check %s for item %s failed", name, item.id)
                    verdict, details = None, ""
            if verdict is None and st.check and waited >= st.timeout_seconds:
                verdict, details = "timeout", f"{name} gave no result within {st.timeout_seconds}s"
            if verdict is None:
                continue
            if details:
                async with engine.db.session() as s:
                    s.add(Artifact(item_id=item.id, name=name, content=details))
                    await s.commit()
            if await engine.advance(item.id, item.version, verdict, actor=f"check:{name}"):
                moved.append((item.id, verdict))
    finally:
        for gh in clients.values():
            await gh.aclose()
    return moved
