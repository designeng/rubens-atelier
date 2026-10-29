from app import board_api
from app.api import app
from app.config import settings


def test_projects_items_board_and_inbox(client):
    r = client.post("/projects", json={"slug": "app", "repo": "acme/app", "verify_command": "make test"})
    assert r.status_code == 201
    assert client.post("/projects", json={"slug": "app", "repo": "acme/app"}).status_code == 409
    assert [p["slug"] for p in client.get("/projects").json()] == ["app"]

    backlog = client.post("/projects/app/items", json={"title": "Later"}).json()
    started = client.post("/projects/app/items", json={"title": "Now", "body": "do it", "start": True}).json()
    assert backlog["column"] == "backlog" and backlog["state"] is None
    assert started["state"] == "plan" and started["column"] == "working"
    assert client.enqueued == [(started["id"], 1)]

    board = client.get("/projects/app/board").json()
    assert [i["title"] for i in board["backlog"]] == ["Later"]
    assert [i["title"] for i in board["working"]] == ["Now"]

    # Simulate the worker getting the story to the merge gate.
    engine = app.state.engine
    for verdict in ("ready", "done", "done", "approved", "done", "passed"):
        item = client.get(f"/items/{started['id']}").json()
        client.portal.call(engine.advance, started["id"], item["version"], verdict)

    (req,) = client.get("/projects/app/inbox").json()
    assert req["state"] == "awaiting_merge"
    detail = client.get(f"/items/{started['id']}").json()
    assert detail["column"] == "needs_human" and detail["open_request"]["id"] == req["id"]
    assert [e["to_state"] for e in detail["events"]] == ["plan", "implement", "sync_base", "review", "publish", "ci", "awaiting_merge"]

    assert client.post(f"/requests/{req['id']}/answer", json={"option": "nope"}).status_code == 409
    r = client.post(f"/requests/{req['id']}/answer", json={"option": "merged", "comment": "thanks"})
    assert r.json() == {"advanced": True}
    assert client.get("/projects/app/inbox").json() == []
    assert [i["title"] for i in client.get("/projects/app/board").json()["done"]] == ["Now"]

    r = client.post(f"/items/{backlog['id']}/cancel")
    assert r.json()["column"] == "failed"
    assert client.post(f"/items/{backlog['id']}/start").status_code == 409

    r = client.patch(f"/items/{backlog['id']}", json={"title": "Renamed", "body": "new text"})
    assert (r.json()["title"], client.get(f"/items/{backlog['id']}").json()["body"]) == ("Renamed", "new text")
    assert client.patch(f"/items/{backlog['id']}", json={"title": ""}).status_code == 422
    assert client.patch(f"/items/{backlog['id']}", json={"title": None}).status_code == 422
    r = client.patch(f"/items/{backlog['id']}", json={"priority": 7, "model": "claude-sonnet-5-5"})
    assert (r.json()["priority"], r.json()["model"]) == (7, "claude-sonnet-5-5")
    assert client.patch(f"/items/{backlog['id']}", json={"priority": 101}).status_code == 422

    # A cancelled story can go back to the backlog and be started again; a finished one cannot.
    r = client.post(f"/items/{backlog['id']}/reopen")
    assert r.json()["column"] == "backlog" and r.json()["state"] is None
    assert client.post(f"/items/{backlog['id']}/reopen").status_code == 409
    assert client.post(f"/items/{backlog['id']}/start").json()["column"] == "working"
    assert client.post(f"/items/{started['id']}/reopen").status_code == 409


def test_validation_and_scoping(client):
    assert client.post("/projects", json={"slug": "Bad Slug", "repo": "acme/app"}).status_code == 422
    assert client.get("/projects/missing/board").status_code == 404
    client.post("/projects", json={"slug": "a", "repo": "acme/a"})
    client.post("/projects", json={"slug": "b", "repo": "acme/b"})
    item = client.post("/projects/a/items", json={"title": "x"}).json()
    assert client.post("/projects/b/items", json={"title": "y", "parent_id": item["id"]}).status_code == 422
    assert client.post("/projects/a/items", json={"title": "z", "workflow": "nope"}).status_code == 422
    assert client.get("/projects/b/board").json()["backlog"] == []
    assert client.get("/items/nope").status_code == 404


def test_board_page_without_build(client, tmp_path, monkeypatch):
    monkeypatch.setattr(board_api, "DIST", tmp_path)
    r = client.get("/board")
    assert r.status_code == 200 and "make web-build" in r.text
    assert client.get("/board/assets/app.js").status_code == 404


def test_board_page_serves_build(client, tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=app></div>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(board_api, "DIST", tmp_path)
    r = client.get("/board", params={"installation_id": 1, "setup_action": "install"})
    assert r.status_code == 200 and r.text == "<div id=app></div>"
    js = client.get("/board/assets/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert client.get("/board/assets/missing.js").status_code == 404
    assert client.get("/board/assets/..%2Findex.html").status_code == 404
    assert client.get("/board/assets/%2E%2E/index.html").status_code == 404


def test_scheduler_pause_and_resume(client):
    status = client.get("/scheduler").json()
    assert status["capacity"] == settings.max_agents and status["reason"] == "ok"
    paused = client.post("/scheduler/pause", json={"reason": "release freeze"}).json()
    assert paused["capacity"] == 0 and paused["reason"] == "paused: release freeze"
    assert client.post("/scheduler/resume").json()["reason"] == "ok"


def test_scheduler_limits(client):
    changed = client.patch("/scheduler/limits", json={"max_agents": 1}).json()
    assert changed["capacity"] == 1
    assert changed["limits"] == {"max_agents": 1, "max_agents_per_project": settings.max_agents_per_project}
    assert client.patch("/scheduler/limits", json={"max_agents": 0}).status_code == 422
    assert client.delete("/scheduler/limits").json()["limits"]["max_agents"] == settings.max_agents


def test_update_project_settings(client):
    client.post("/projects", json={"slug": "app", "repo": "acme/app", "verify_command": "make test"})

    r = client.patch("/projects/app", json={"base_branch": "develop"})
    assert r.status_code == 200
    assert r.json()["base_branch"] == "develop" and r.json()["verify_command"] == "make test"

    r = client.patch("/projects/app", json={"verify_command": None})
    assert r.status_code == 200
    assert r.json()["verify_command"] is None and r.json()["base_branch"] == "develop"

    r = client.patch("/projects/app", json={"base_branch": None, "verify_command": " uv run pytest -q "})
    assert (r.json()["base_branch"], r.json()["verify_command"]) == (None, "uv run pytest -q")
    (project,) = client.get("/projects").json()
    assert project["slug"] == "app" and project["repo"] == "acme/app"
    assert (project["base_branch"], project["verify_command"]) == (None, "uv run pytest -q")


def test_update_project_validation(client):
    assert client.patch("/projects/missing", json={"verify_command": "make test"}).status_code == 404
    before = client.post("/projects", json={"slug": "app", "repo": "acme/app", "base_branch": "main"}).json()
    for body in (
        {"slug": "x"},
        {"repo": "a/b"},
        {"installation_id": 1},
        {"base_branch": ""},
        {"base_branch": "-x"},
        {"base_branch": "a b"},
        {"base_branch": "b" * 201},
        {"verify_command": ""},
        {"verify_command": "   "},
    ):
        assert client.patch("/projects/app", json=body).status_code == 422, body
    assert client.get("/projects").json() == [before]


def test_working_items_report_progress(client):
    client.post("/projects", json={"slug": "app", "repo": "acme/app"})
    item = client.post("/projects/app/items", json={"title": "Now", "start": True}).json()
    # Not dispatched to a worker in this test: the agent step has no run yet.
    (card,) = client.get("/projects/app/board").json()["working"]
    assert card["progress"] == {"step": "planner", "kind": "agent", "queued": False}
    assert client.get(f"/items/{item['id']}").json()["progress"]["step"] == "planner"
    assert client.post("/projects/app/items", json={"title": "Later"}).json()["progress"] is None


def test_attention_and_manual_open_pr(client):
    client.post("/projects", json={"slug": "app", "repo": "acme/app"})
    item = client.post("/projects/app/items", json={"title": "Now", "start": True}).json()
    iid, engine = item["id"], app.state.engine
    assert client.post(f"/items/{iid}/open-pr").status_code == 409  # no branch yet

    async def seed():
        from app.db import Artifact, WorkItem

        async with app.state.db.session() as s:
            (await s.get(WorkItem, iid)).branch = "agent/story-x"
            s.add(Artifact(item_id=iid, name="implementer", content="Done.\n\n## Open questions\n- Should CI run?\n"))
            await s.commit()

    client.portal.call(seed)
    for verdict in ("ready", "error"):
        client.portal.call(engine.advance, iid, client.get(f"/items/{iid}").json()["version"], verdict)
    detail = client.get(f"/items/{iid}").json()
    assert detail["state"] == "needs_human" and detail["attention"]["kind"] == "questions"
    assert "Should CI run?" in detail["attention"]["text"]

    # a later failure outranks the older summary
    async def fail():
        from app.db import Artifact

        async with app.state.db.session() as s:
            s.add(Artifact(item_id=iid, name="error", content="implement: boom"))
            await s.commit()

    client.portal.call(fail)
    board = client.get("/projects/app/board").json()
    assert board["needs_human"][0]["attention"] == {"kind": "error", "text": "implement: boom"}

    r = client.post(f"/items/{iid}/open-pr")
    assert r.status_code == 202 and r.json()["state"] == "publish"
    assert client.enqueued[-1] == (iid, r.json()["version"])
    assert client.post(f"/items/{iid}/open-pr").status_code == 409  # already publishing


def test_item_git_view(client):
    import subprocess

    from app.execution import item_workspace

    client.post("/projects", json={"slug": "app", "repo": "acme/app"})
    item = client.post("/projects/app/items", json={"title": "Now", "start": True}).json()
    iid = item["id"]
    assert client.get(f"/items/{iid}/git").status_code == 409  # no workspace yet

    cwd = item_workspace(iid)
    cwd.mkdir(parents=True)

    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)

    git("init", "-b", "main")
    (cwd / "a.txt").write_text("one\n")
    git("add", "-A")
    git("commit", "-m", "base")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("checkout", "-b", "agent/story-x")
    (cwd / "a.txt").write_text("two\n")
    git("commit", "-am", "change")
    (cwd / "b.txt").write_text("new\n")

    async def seed():
        from app.db import WorkItem

        async with app.state.db.session() as s:
            it = await s.get(WorkItem, iid)
            it.branch, it.base_branch = "agent/story-x", "main"
            await s.commit()

    client.portal.call(seed)
    g = client.get(f"/items/{iid}/git").json()
    assert g["branch"] == "agent/story-x" and "change" in g["commits"] and "a.txt" in g["stat"]
    assert "?? b.txt" in g["status"] and "diff" not in g
    assert "+two" in client.get(f"/items/{iid}/git?diff=true").json()["diff"]


def test_terminal(client, monkeypatch):
    import subprocess

    from app import terminal
    from app.execution import item_workspace

    client.post("/projects", json={"slug": "app", "repo": "acme/app"})
    iid = client.post("/projects/app/items", json={"title": "Now", "start": True}).json()["id"]
    body = {"command": "echo hi; echo err >&2; echo $HARNESS_API_TOKEN; exit 3"}
    assert client.post(f"/items/{iid}/exec", json=body).status_code == 403  # disabled
    monkeypatch.setattr(settings, "terminal_enabled", True)
    assert client.post(f"/items/{iid}/exec", json=body).status_code == 403  # no API token
    monkeypatch.setattr(settings, "api_token", "secret")
    auth = {"authorization": "Bearer secret"}
    assert client.post(f"/items/{iid}/exec", json=body, headers=auth).status_code == 409  # no workspace

    cwd = item_workspace(iid)
    cwd.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)

    async def seed():
        from app.db import WorkItem

        async with app.state.db.session() as s:
            (await s.get(WorkItem, iid)).branch = "agent/story-x"
            await s.commit()

    client.portal.call(seed)
    assert client.post(f"/items/{iid}/exec", json=body, headers=auth).status_code == 409  # plan agent is working
    client.portal.call(app.state.engine.advance, iid, client.get(f"/items/{iid}", headers=auth).json()["version"], "error")

    monkeypatch.setenv("HARNESS_API_TOKEN", "secret")
    r = client.post(f"/items/{iid}/exec", json=body, headers=auth)
    assert r.status_code == 202 and client.execs == [r.json()["id"]]
    exec_id = r.json()["id"]
    assert client.post(f"/items/{iid}/exec", json=body, headers=auth).status_code == 409  # still running
    assert client.get(f"/items/{iid}/exec/{exec_id}", headers=auth).json()["status"] == "running"

    client.portal.call(terminal.run, app.state.redis, exec_id)
    res = client.get(f"/items/{iid}/exec/{exec_id}", headers=auth).json()
    assert res["status"] == "done" and res["exit_code"] == 3
    assert "hi" in res["output"] and "err" in res["output"] and "secret" not in res["output"]
    assert client.get(f"/items/other/exec/{exec_id}", headers=auth).status_code == 404

    monkeypatch.setattr(settings, "terminal_timeout_seconds", 1)
    exec_id = client.post(f"/items/{iid}/exec", json={"command": "sleep 30"}, headers=auth).json()["id"]
    client.portal.call(terminal.run, app.state.redis, exec_id)
    res = client.get(f"/items/{iid}/exec/{exec_id}", headers=auth).json()
    assert res["status"] == "timeout" and "timed out" in res["output"]
