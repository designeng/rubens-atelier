"""MCP server for the factory: projects, stories, the inbox, the scheduler and workspaces as tools.

A thin client of the HTTP API (HARNESS_API_URL, bearer HARNESS_API_TOKEN), so it drives a local
`make dev` and a deployed compose stack alike and goes through the same checks as the board.

    uv run python -m app.mcp_server        # stdio transport
    claude mcp add factory -- uv run --directory /path/to/agent-harness python -m app.mcp_server
"""

from typing import Any, Literal, Required, TypedDict

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .config import settings

INSTRUCTIONS = """\
Manage a software factory where Claude Code agents work on stories: plan, implement, review,
open a PR, pass CI, merge. A project is a GitHub repo; its stories move through a workflow
(states plan → implement → sync_base → review → publish → ci → merge → awaiting_merge).
Start with list_projects, then get_board. Stories waiting for a person show up in get_inbox;
answer them with answer_request using one of the request's options. Each project keeps a memory of
lessons for its agents (list_notes); notes agents propose stay `proposed` until approved."""

# Fields of a board card worth showing a model; the full card is in get_item.
CARD_FIELDS = (
    "id", "kind", "title", "state", "priority", "model", "pr_url", "spent_usd", "budget_usd",
    "blocked", "ready", "auto_start", "attention", "progress",
)
EVENT_TEXT_LIMIT = 2000

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)

mcp = MCPServer("rubens-atelier", instructions=INSTRUCTIONS)

# Tests route requests to the app in-process; None = real network.
transport: httpx.AsyncBaseTransport | None = None


async def _request(method: str, path: str, *, json: Any = None, params: dict | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {settings.api_token}"} if settings.api_token else {}
    async with httpx.AsyncClient(base_url=settings.api_url, headers=headers, transport=transport, timeout=30) as c:
        try:
            r = await c.request(method, path, json=json, params=params)
        except httpx.TransportError as e:
            raise ToolError(f"factory API at {settings.api_url} is unreachable: {e}") from None
    if r.is_error:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ToolError(f"{method} {path} failed with {r.status_code}: {detail}")
    return r


async def _call(method: str, path: str, *, json: Any = None, params: dict | None = None) -> Any:
    r = await _request(method, path, json=json, params=params)
    return r.json() if r.content else None


def _patch(fields: dict[str, Any], clear: list[str]) -> dict[str, Any]:
    """The PATCH body: given fields, plus null for each cleared one (null = remove the value)."""
    body = {k: v for k, v in fields.items() if v is not None}
    body.update(dict.fromkeys(clear))
    if not body:
        raise ToolError("nothing to change")
    return body


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > EVENT_TEXT_LIMIT:
        return value[:EVENT_TEXT_LIMIT] + f"… [{len(value) - EVENT_TEXT_LIMIT} more chars]"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


# Projects


@mcp.tool(annotations=READ)
async def list_projects() -> list[dict]:
    """Every project (tenant) with its repo, verify command, auto-merge flag, budget and spend."""
    return await _call("GET", "/projects")


@mcp.tool(annotations=WRITE)
async def create_project(
    slug: str,
    repo: str,
    base_branch: str | None = None,
    verify_command: str | None = None,
    auto_merge: bool = False,
    budget_usd: float | None = None,
    installation_id: int | None = None,
) -> dict:
    """Create a project. `slug` is lowercase letters, digits and dashes; `repo` is "owner/name".
    `verify_command` (e.g. "make test") is run after each implementer step."""
    body = {
        "slug": slug, "repo": repo, "base_branch": base_branch, "verify_command": verify_command,
        "auto_merge": auto_merge, "budget_usd": budget_usd, "installation_id": installation_id,
    }
    return await _call("POST", "/projects", json=body)


@mcp.tool(annotations=WRITE)
async def update_project(
    slug: str,
    base_branch: str | None = None,
    verify_command: str | None = None,
    auto_merge: bool | None = None,
    budget_usd: float | None = None,
    clear: list[Literal["base_branch", "verify_command", "budget_usd"]] = [],
) -> dict:
    """Change project settings; omitted ones stay. List fields in `clear` to remove their value.
    verify_command applies from the next agent step of every story; base_branch only to stories
    that have not created their workspace yet."""
    fields = {"base_branch": base_branch, "verify_command": verify_command, "auto_merge": auto_merge,
              "budget_usd": budget_usd}
    return await _call("PATCH", f"/projects/{slug}", json=_patch(fields, clear))


@mcp.tool(annotations=WRITE)
async def import_issues(project: str) -> dict:
    """Put the repo's open GitHub issues into the backlog as stories (nothing starts). Issues that
    already have a story are skipped."""
    result = await _call("POST", f"/projects/{project}/import-issues")
    return {"imported": [_card(i) for i in result["imported"]], "skipped": result["skipped"]}


# Board and stories


def _card(item: dict) -> dict:
    # `in (None, [], False)` would also drop 0 (priority, spent_usd), since 0 == False.
    return {k: v for k in CARD_FIELDS if (v := item.get(k)) is not None and v is not False and v != []}


@mcp.tool(annotations=READ)
async def get_board(project: str, done_limit: int = 20) -> dict:
    """The project's stories by column: backlog, working, needs_human, done, failed. `done` holds
    only the `done_limit` most recent ones; `done_total` says how many there are. `proposed_notes`
    counts notes agents proposed for the project's memory that wait for a person (list_notes)."""
    r = await _request("GET", f"/projects/{project}/board", params={"done_limit": done_limit})
    board = {column: [_card(i) for i in items] for column, items in r.json().items()}
    return {
        **board,
        "done_total": int(r.headers.get("X-Done-Total", len(board.get("done", [])))),
        "proposed_notes": int(r.headers.get("X-Proposed-Notes", 0)),
    }


@mcp.tool(annotations=READ)
async def get_inbox(project: str) -> list[dict]:
    """Open requests to a person (clarifications, merge approvals, stuck stories), oldest first.
    Answer one with answer_request."""
    return await _call("GET", f"/projects/{project}/inbox")


@mcp.tool(annotations=READ)
async def get_item(item_id: str, all_artifacts: bool = False) -> dict:
    """A story in full: body, state, transition history, runs (with their task ids for
    get_run_log), artifacts (plan, review, CI results, errors) and the open request, if any.
    Only the latest version of each artifact is returned unless `all_artifacts`."""
    item = await _call("GET", f"/items/{item_id}")
    if not all_artifacts:
        latest = {a["name"]: a for a in item["artifacts"]}  # ordered by creation, so last wins
        item["artifacts"] = list(latest.values())
    return item


@mcp.tool(annotations=WRITE)
async def create_item(
    project: str,
    title: str,
    body: str = "",
    kind: Literal["epic", "story", "task"] = "story",
    priority: int = 0,
    parent_id: str | None = None,
    issue: int | None = None,
    model: str | None = None,
    lean_context: bool | None = None,
    budget_usd: float | None = None,
    blocked_by: list[str] = [],
    start: bool = False,
    auto_start: bool = False,
) -> dict:
    """Create a story in the backlog; `start=True` also starts it. `priority` is -100..100 (Low -10,
    Normal 0, High 10, Urgent 50). `issue` links a GitHub issue the PR will close. `blocked_by`
    lists story ids of the same project that must succeed first. `auto_start=True` starts the story
    by itself as soon as all of them have succeeded (right away if they already have).
    For several stories that depend on each other, use create_plan."""
    payload = {
        "title": title, "body": body, "kind": kind, "priority": priority, "parent_id": parent_id,
        "issue": issue, "model": model, "lean_context": lean_context, "budget_usd": budget_usd,
        "blocked_by": blocked_by, "start": start, "auto_start": auto_start,
    }
    return _card(await _call("POST", f"/projects/{project}/items", json=payload))


class PlanEpic(TypedDict, total=False):
    title: Required[str]
    body: str
    priority: int
    model: str
    lean_context: bool
    budget_usd: float


class PlanItem(TypedDict, total=False):
    key: Required[str]
    title: Required[str]
    body: str
    kind: Literal["story", "task"]
    priority: int
    parent_id: str
    issue: int
    model: str
    lean_context: bool
    budget_usd: float
    blocked_by: list[str]
    start: bool
    auto_start: bool


@mcp.tool(annotations=WRITE)
async def create_plan(project: str, items: list[PlanItem], epic: PlanEpic | None = None) -> dict:
    """Create an epic (optional) and its stories in one call, all or nothing. Each item has a `key`
    of your choosing (letters, digits, `_.-`); its `blocked_by` names other items of the plan by key,
    or existing stories by id. Items go under the epic unless they set `parent_id`. Other fields are as
    in create_item: `auto_start=True` makes an item start by itself once its blockers have succeeded,
    so a whole chain runs without further calls. Returns the epic and the stories by key."""
    out = await _call("POST", f"/projects/{project}/plan", json={"epic": epic, "items": items})
    return {"epic": _card(out["epic"]) if out["epic"] else None,
            "items": {k: _card(v) for k, v in out["items"].items()}}


@mcp.tool(annotations=WRITE)
async def update_item(
    item_id: str,
    title: str | None = None,
    body: str | None = None,
    priority: int | None = None,
    model: str | None = None,
    lean_context: bool | None = None,
    budget_usd: float | None = None,
    blocked_by: list[str] | None = None,
    auto_start: bool | None = None,
    clear: list[Literal["model", "lean_context", "budget_usd"]] = [],
) -> dict:
    """Edit a story; omitted fields stay. List fields in `clear` to go back to the default.
    A running story picks up the changes from its next agent step. `auto_start=True` on a backlog
    story starts it once its blockers have succeeded (right away if they already have)."""
    fields = {"title": title, "body": body, "priority": priority, "model": model, "lean_context": lean_context,
              "budget_usd": budget_usd, "blocked_by": blocked_by, "auto_start": auto_start}
    return _card(await _call("PATCH", f"/items/{item_id}", json=_patch(fields, clear)))


@mcp.tool(annotations=WRITE)
async def item_action(item_id: str, action: Literal["start", "run-now", "cancel", "reopen", "open-pr"]) -> dict:
    """Move a story:
    - start: begin the workflow of a backlog story (refused while its blockers have not succeeded, and
      for a story imported from an outside author's issue, which a person must confirm on the board;
      to start it once the blockers succeed, set `auto_start` with update_item);
    - run-now: hand its waiting agent step a slot now, even if the scheduler is paused or full;
    - cancel: stop it (a running agent stops within seconds);
    - reopen: return a failed or cancelled story to the backlog;
    - open-pr: push the branch and open or update the PR now, skipping review and CI."""
    return _card(await _call("POST", f"/items/{item_id}/{action}"))


@mcp.tool(annotations=WRITE)
async def comment_item(item_id: str, body: str) -> dict:
    """Comment on a story's agent step while it runs. The run finishes, then the same role resumes
    its session to read the comment before the story moves on: an extra run, with its cost. Refused
    while the story is in no agent step. Each new run counts against the budget but not max_visits."""
    return _card(await _call("POST", f"/items/{item_id}/comments", json={"body": body}))


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_item(item_id: str) -> str:
    """Delete a failed or cancelled story with its whole history. Cannot be undone."""
    await _call("DELETE", f"/items/{item_id}")
    return f"deleted {item_id}"


@mcp.tool(annotations=WRITE)
async def answer_request(request_id: str, option: str, comment: str = "") -> dict:
    """Answer an inbox request with one of its `options`. The comment becomes an artifact the next
    agent reads (e.g. the clarification the planner asked for, or what to change)."""
    return await _call("POST", f"/requests/{request_id}/answer", json={"option": option, "comment": comment})


@mcp.tool(annotations=READ)
async def get_item_git(item_id: str, diff: bool = False) -> dict:
    """What is in the story's workspace now: git status, its commits and the changed files against
    the base branch; `diff=True` adds the full diff."""
    return await _call("GET", f"/items/{item_id}/git", params={"diff": diff})


@mcp.tool(annotations=READ)
async def get_run_log(task_id: str, after: str = "0", limit: int = 100) -> dict:
    """An agent run's session events (text, tool calls and results, verify output, status), from
    a run's `task_id` in get_item. Long strings are clipped; pass `next_after` to read on."""
    events = await _call("GET", f"/tasks/{task_id}/events", params={"after": after, "limit": limit})
    task = await _call("GET", f"/tasks/{task_id}")
    return {
        "status": task.get("status"),
        "failure_reason": task.get("failure_reason"),
        "cost_usd": task.get("cost_usd"),
        "events": _clip(events),
        "next_after": events[-1]["id"] if events else after,
    }


# Scheduler


@mcp.tool(annotations=READ)
async def scheduler_status() -> dict:
    """Busy agent slots, the queue of steps waiting for one, capacity and why, and the state of
    the subscription windows the pace follows."""
    return await _call("GET", "/scheduler")


@mcp.tool(annotations=WRITE)
async def pause_scheduler(reason: str = "") -> dict:
    """Stop handing out new agent steps; running ones finish."""
    return await _call("POST", "/scheduler/pause", json={"reason": reason})


@mcp.tool(annotations=WRITE)
async def resume_scheduler() -> dict:
    """Resume handing out agent steps."""
    return await _call("POST", "/scheduler/resume")


@mcp.tool(annotations=WRITE)
async def set_scheduler_limits(
    max_agents: int | None = None,
    max_agents_per_project: int | None = None,
    force: bool | None = None,
    reset: bool = False,
) -> dict:
    """Change how many agents run at once (in total and per project) without a restart; omitted
    ones stay. `force` keeps max_agents while subscription windows would throttle dispatch.
    `reset=True` drops every override and returns to the configured limits."""
    if reset:
        return await _call("DELETE", "/scheduler/limits")
    body = _patch({"max_agents": max_agents, "max_agents_per_project": max_agents_per_project, "force": force}, [])
    return await _call("PATCH", "/scheduler/limits", json=body)


@mcp.tool(annotations=READ)
async def list_models() -> dict:
    """Models offered for a story, the default one, and whether lean context is the default."""
    return await _call("GET", "/models")


# Project memory


@mcp.tool(annotations=READ)
async def list_notes(
    project: str, status: Literal["proposed", "active", "archived"] | None = None, tag: str | None = None
) -> list[dict]:
    """A project's memory, newest first: lessons agents read in every fresh session (`active`),
    ones agents proposed that wait for a person (`proposed`), retired ones (`archived`)."""
    params = {k: v for k, v in {"status": status, "tag": tag}.items() if v is not None}
    return await _call("GET", f"/projects/{project}/notes", params=params)


@mcp.tool(annotations=WRITE)
async def create_note(project: str, title: str, content: str, tags: list[str] = []) -> dict:
    """Add a lesson to a project's memory (active at once): a convention, a pitfall, an approach
    that failed and why. Tags (language, area) let a fragment be copied into other projects."""
    return await _call("POST", f"/projects/{project}/notes", json={"title": title, "content": content, "tags": tags})


@mcp.tool(annotations=WRITE)
async def update_note(
    note_id: str,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    status: Literal["proposed", "active", "archived"] | None = None,
) -> dict:
    """Edit a note; omitted fields stay. `status="active"` approves a proposed note,
    `"archived"` retires one while keeping it."""
    fields = {"title": title, "content": content, "tags": tags, "status": status}
    return await _call("PATCH", f"/notes/{note_id}", json=_patch(fields, []))


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_note(note_id: str) -> str:
    """Reject a proposed note, or drop a note for good (archive it to keep the record)."""
    await _call("DELETE", f"/notes/{note_id}")
    return f"deleted {note_id}"


@mcp.tool(annotations=WRITE)
async def copy_notes(project: str, from_project: str, note_ids: list[str] = [], tags: list[str] = []) -> dict:
    """Copy a fragment of another project's memory into `project`: the listed active notes plus
    every active note with one of `tags`. Notes copied before are skipped."""
    body = {"from_project": from_project, "note_ids": note_ids, "tags": tags}
    return await _call("POST", f"/projects/{project}/notes/copy", json=body)


@mcp.tool(annotations=READ)
async def export_notes(
    project: str,
    note_ids: list[str] = [],
    tags: list[str] = [],
    format: Literal["json", "markdown"] = "json",
) -> dict | str:
    """A project's active notes as a file to keep in git or import on another factory: the listed
    ones plus every one with one of `tags`, or all of them when neither is given. `markdown` is
    for people to read; only JSON can be imported back."""
    params = {"note_ids": note_ids, "tags": tags, "format": format}
    r = await _request("GET", f"/projects/{project}/notes/export", params=params)
    return r.text if format == "markdown" else r.json()


@mcp.tool(annotations=WRITE)
async def import_notes(project: str, notes_file: dict) -> dict:
    """Import a JSON file from export_notes into `project`. The notes arrive `proposed`, for a
    person to approve; notes imported or copied before are skipped."""
    return await _call("POST", f"/projects/{project}/notes/import", json=notes_file)


# Workspaces


@mcp.tool(annotations=READ)
async def list_workspaces(
    project: str | None = None,
    phase: Literal["orphan", "finished", "failed", "idle", "busy"] | None = None,
    size: bool = True,
) -> dict:
    """Story and task workspaces on disk with their owner, size, phase and whether they can be
    removed. `size=False` skips the slow disk usage."""
    params = {k: v for k, v in {"project": project, "phase": phase, "size": size}.items() if v is not None}
    return await _call("GET", "/workspaces", params=params)


@mcp.tool(annotations=DESTRUCTIVE)
async def prune_workspaces(older_than_hours: float = 0, include_failed: bool = False, dry_run: bool = True) -> dict:
    """Remove workspaces of finished and deleted stories and tasks (never one in use).
    `include_failed` adds failed, cancelled and backlog ones, losing unpushed commits.
    Runs as a dry run unless `dry_run=False`: check `would_remove` first."""
    body = {"older_than_hours": older_than_hours, "include_failed": include_failed, "dry_run": dry_run}
    return await _call("POST", "/workspaces/prune", json=body)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
