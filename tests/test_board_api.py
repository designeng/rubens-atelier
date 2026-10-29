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
    for verdict in ("ready", "done", "approved", "done", "passed"):
        item = client.get(f"/items/{started['id']}").json()
        client.portal.call(engine.advance, started["id"], item["version"], verdict)

    (req,) = client.get("/projects/app/inbox").json()
    assert req["state"] == "awaiting_merge"
    detail = client.get(f"/items/{started['id']}").json()
    assert detail["column"] == "needs_human" and detail["open_request"]["id"] == req["id"]
    assert [e["to_state"] for e in detail["events"]] == ["plan", "implement", "review", "publish", "ci", "awaiting_merge"]

    assert client.post(f"/requests/{req['id']}/answer", json={"option": "nope"}).status_code == 409
    r = client.post(f"/requests/{req['id']}/answer", json={"option": "merged", "comment": "thanks"})
    assert r.json() == {"advanced": True}
    assert client.get("/projects/app/inbox").json() == []
    assert [i["title"] for i in client.get("/projects/app/board").json()["done"]] == ["Now"]

    r = client.post(f"/items/{backlog['id']}/cancel")
    assert r.json()["column"] == "failed"
    assert client.post(f"/items/{backlog['id']}/start").status_code == 409


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
    assert client.get("/board").status_code == 200


def test_scheduler_pause_and_resume(client):
    status = client.get("/scheduler").json()
    assert status["capacity"] == settings.max_agents and status["reason"] == "ok"
    paused = client.post("/scheduler/pause", json={"reason": "release freeze"}).json()
    assert paused["capacity"] == 0 and paused["reason"] == "paused: release freeze"
    assert client.post("/scheduler/resume").json()["reason"] == "ok"
