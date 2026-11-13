import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app import mcp_server
from app.api import app
from app.config import settings
from app.db import Artifact


@pytest.fixture
def tools(client, monkeypatch):
    """Call MCP tools by name; their HTTP requests go to the in-process app through `client`."""

    def forward(request: httpx.Request) -> httpx.Response:
        r = client.request(request.method, request.url.raw_path.decode(), content=request.content,
                           headers=request.headers)
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)

    monkeypatch.setattr(mcp_server, "transport", httpx.MockTransport(forward))
    monkeypatch.setattr(settings, "api_url", "http://localhost:8000")

    async def call(name, **arguments):
        result = await mcp_server.mcp.call_tool(name, arguments)
        # Lists come back as structured content; dicts only as one JSON text block.
        if result.structured_content is not None:
            return result.structured_content["result"]
        return json.loads(result.content[0].text)

    return call


async def test_story_from_creation_to_inbox_answer(tools, client):
    project = await tools("create_project", slug="app", repo="acme/app", verify_command="make test")
    assert (await tools("list_projects"))[0]["slug"] == project["slug"] == "app"

    later = await tools("create_item", project="app", title="Later", priority=0)
    now = await tools("create_item", project="app", title="Now", body="do it", priority=10, start=True)
    assert later["priority"] == 0 and "state" not in later
    assert now["state"] == "plan"

    board = await tools("get_board", project="app")
    assert [i["title"] for i in board["backlog"]] == ["Later"]
    assert [i["title"] for i in board["working"]] == ["Now"]
    assert board["done_total"] == 0

    for verdict in ("ready", "done", "done", "approved", "done", "passed", "manual"):
        item = await tools("get_item", item_id=now["id"])
        client.portal.call(app.state.engine.advance, now["id"], item["version"], verdict)

    (req,) = await tools("get_inbox", project="app")
    assert req["state"] == "awaiting_merge"
    assert await tools("answer_request", request_id=req["id"], option="merged", comment="thanks") == {
        "advanced": True
    }
    board = await tools("get_board", project="app", done_limit=5)
    assert [i["title"] for i in board["done"]] == ["Now"] and board["done_total"] == 1

    cancelled = await tools("item_action", item_id=later["id"], action="cancel")
    assert cancelled["state"] == "cancelled"
    reopened = await tools("item_action", item_id=later["id"], action="reopen")
    assert "state" not in reopened


async def test_create_plan(tools):
    await tools("create_project", slug="app", repo="acme/app")
    plan = await tools(
        "create_plan", project="app", epic={"title": "Login"},
        items=[{"key": "a", "title": "A", "auto_start": True},
               {"key": "b", "title": "B", "blocked_by": ["a"], "auto_start": True}],
    )
    assert plan["epic"]["kind"] == "epic"
    assert plan["items"]["a"]["state"] == "plan" and "state" not in plan["items"]["b"]
    assert plan["items"]["b"]["auto_start"] is True and plan["items"]["b"]["blocked"]


async def test_comment_item_reaches_the_story_and_shows_as_waiting(tools):
    await tools("create_project", slug="app", repo="acme/app")
    item = await tools("create_item", project="app", title="T", start=True)
    await tools("comment_item", item_id=item["id"], body="Also run the tests")
    (comment,) = (await tools("get_item", item_id=item["id"]))["artifacts"]
    assert comment["content"] == "Also run the tests" and comment["run_id"] is None


async def test_update_item_sends_only_given_fields_and_clears(tools):
    await tools("create_project", slug="app", repo="acme/app")
    item = await tools("create_item", project="app", title="T", model="claude-opus-5-5", budget_usd=3)

    updated = await tools("update_item", item_id=item["id"], title="Renamed")
    assert (updated["title"], updated["model"], updated["budget_usd"]) == ("Renamed", "claude-opus-5-5", 3)

    cleared = await tools("update_item", item_id=item["id"], clear=["model", "budget_usd"])
    assert "model" not in cleared and "budget_usd" not in cleared

    with pytest.raises(ToolError, match="nothing to change"):
        await tools("update_item", item_id=item["id"])


async def test_get_item_keeps_latest_artifacts(tools, client):
    await tools("create_project", slug="app", repo="acme/app")
    item = await tools("create_item", project="app", title="T", start=True)
    async def seed():
        async with app.state.db.session() as s:
            start = datetime.now(UTC)
            for i, content in enumerate(("v1", "v2")):
                s.add(Artifact(item_id=item["id"], name="planner", content=content,
                               created_at=start + timedelta(seconds=i)))
            await s.commit()

    client.portal.call(seed)

    latest = await tools("get_item", item_id=item["id"])
    assert [a["content"] for a in latest["artifacts"] if a["name"] == "planner"] == ["v2"]
    full = await tools("get_item", item_id=item["id"], all_artifacts=True)
    assert [a["content"] for a in full["artifacts"] if a["name"] == "planner"] == ["v1", "v2"]


async def test_api_errors_become_tool_errors(tools):
    with pytest.raises(ToolError, match="404: project not found"):
        await tools("get_board", project="missing")


async def test_scheduler_tools(tools):
    paused = await tools("pause_scheduler", reason="maintenance")
    assert paused["capacity"] == 0 and "maintenance" in paused["reason"]
    assert (await tools("resume_scheduler"))["capacity"] > 0

    limited = await tools("set_scheduler_limits", max_agents=3)
    assert limited["limits"]["max_agents"] == 3
    reset = await tools("set_scheduler_limits", reset=True)
    assert reset["limits"]["max_agents"] == settings.max_agents


async def test_unreachable_api(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(mcp_server, "transport", httpx.MockTransport(refuse))
    with pytest.raises(ToolError, match="unreachable"):
        await mcp_server.mcp.call_tool("list_projects", {})


async def test_token_is_sent(monkeypatch):
    seen = []

    def record(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"default": "m", "models": [], "lean_context": False})

    monkeypatch.setattr(mcp_server, "transport", httpx.MockTransport(record))
    monkeypatch.setattr(settings, "api_token", "secret")
    await mcp_server.mcp.call_tool("list_models", {})
    assert seen == ["Bearer secret"]


async def test_note_tools(tools):
    for slug in ("api", "web"):
        await tools("create_project", slug=slug, repo=f"acme/{slug}")
    note = await tools("create_note", project="api", title="Use uv", content="Never pip.", tags=["Python"])
    assert (note["status"], note["tags"]) == ("active", ["python"])
    archived = await tools("update_note", note_id=note["id"], status="archived")
    assert archived["status"] == "archived" and archived["content"] == "Never pip."
    with pytest.raises(ToolError, match="nothing to change"):
        await tools("update_note", note_id=note["id"])
    await tools("update_note", note_id=note["id"], status="active")

    copied = await tools("copy_notes", project="web", from_project="api", tags=["python"])
    assert [n["title"] for n in copied["copied"]] == ["Use uv"] and copied["skipped"] == 0
    (in_web,) = await tools("list_notes", project="web", status="active")
    assert await tools("delete_note", note_id=in_web["id"]) == f"deleted {in_web['id']}"
    assert await tools("list_notes", project="web") == []

    file = await tools("export_notes", project="api", tags=["python"])
    assert [n["title"] for n in file["notes"]] == ["Use uv"]
    assert "## Use uv" in await tools("export_notes", project="api", format="markdown")
    imported = await tools("import_notes", project="web", notes_file=file)
    assert imported["copied"][0]["status"] == "proposed"
    assert (await tools("get_board", project="web"))["proposed_notes"] == 1


async def test_wait_for_returns_transitions_after_the_board_cursor(tools, client):
    await tools("create_project", slug="app", repo="acme/app")
    other = await tools("create_item", project="app", title="Other", start=True)
    cursor = (await tools("get_board", project="app"))["events_cursor"]
    assert cursor > 0  # Other's start

    story = await tools("create_item", project="app", title="Story", start=True)
    for verdict in ("needs_clarification",):
        item = await tools("get_item", item_id=story["id"])
        client.portal.call(app.state.engine.advance, story["id"], item["version"], verdict)

    # Default: only transitions that need a person or finish a story.
    got = await tools("wait_for", project="app", after=cursor, timeout_seconds=0)
    assert [(e["title"], e["to_state"], e["kind"]) for e in got["events"]] == [("Story", "clarify", "human")]

    everything = await tools("wait_for", project="app", after=cursor, kinds=[], timeout_seconds=0)
    assert [(e["to_state"], e["kind"]) for e in everything["events"]] == [("plan", "agent"), ("clarify", "human")]
    assert everything["cursor"] == got["cursor"]

    # Nothing new after the cursor; without `after` nothing past is returned either.
    assert (await tools("wait_for", project="app", after=got["cursor"], kinds=[], timeout_seconds=0))["events"] == []
    now = await tools("wait_for", project="app", timeout_seconds=0)
    assert now == {"cursor": got["cursor"], "events": []}

    only_other = await tools("wait_for", project="app", after=0, item_ids=[other["id"]], kinds=[], timeout_seconds=0)
    assert {e["title"] for e in only_other["events"]} == {"Other"}


async def test_wait_for_reports_outcome_of_finished_stories(tools, client):
    await tools("create_project", slug="app", repo="acme/app")
    story = await tools("create_item", project="app", title="Story", start=True)
    cursor = (await tools("get_board", project="app"))["events_cursor"]
    await tools("item_action", item_id=story["id"], action="cancel")
    (event,) = (await tools("wait_for", project="app", after=cursor, timeout_seconds=0))["events"]
    assert (event["kind"], event["outcome"]) == ("terminal", "cancelled")


MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(client, method, params=None, id=1, headers=MCP_HEADERS):
    body = {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}
    return client.post("/mcp", headers=headers, json=body)


def test_http_transport_serves_the_tools_in_process(client):
    client.post("/projects", json={"slug": "app", "repo": "acme/app"})
    init = _rpc(client, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"},
    })
    assert init.status_code == 200, init.text
    assert "wait_for" in init.json()["result"]["instructions"]

    names = {t["name"] for t in _rpc(client, "tools/list", id=2).json()["result"]["tools"]}
    assert {"get_board", "wait_for", "forecast_cost", "create_plan"} <= names

    called = _rpc(client, "tools/call", {"name": "list_projects", "arguments": {}}, id=3).json()["result"]
    assert not called.get("isError"), called
    assert called["structuredContent"]["result"][0]["slug"] == "app"


def test_http_transport_needs_the_token(client, monkeypatch):
    monkeypatch.setattr(settings, "api_token", "s3cret")
    assert _rpc(client, "tools/list").status_code == 401
    bad = {**MCP_HEADERS, "Authorization": "Bearer nope"}
    assert _rpc(client, "tools/list", headers=bad).status_code == 401
    ok = _rpc(client, "tools/list", headers={**MCP_HEADERS, "Authorization": "Bearer s3cret"})
    assert ok.status_code == 200 and ok.json()["result"]["tools"]
    called = _rpc(client, "tools/call", {"name": "list_projects", "arguments": {}}, id=2,
                  headers={**MCP_HEADERS, "Authorization": "Bearer s3cret"}).json()["result"]
    assert not called.get("isError"), called  # its own calls to the API carry the token too
