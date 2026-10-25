import pytest
from fastapi.testclient import TestClient

from app import service
from app.api import app
from app.config import settings
from app.models import TaskStatus
from app.store import TaskStore


@pytest.fixture
def client(redis, monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr(service.run_agent, "apply_async", lambda args, task_id: enqueued.append(task_id))
    with TestClient(app) as c:
        # Swap the real Redis created in lifespan for the fake one.
        app.state.redis, app.state.store = redis, TaskStore(redis)
        c.enqueued = enqueued
        yield c


def test_create_get_list_cancel(client):
    r = client.post("/tasks", json={"prompt": "fix the bug"})
    assert r.status_code == 202
    task = r.json()
    assert task["status"] == "queued"
    assert client.enqueued == [task["id"]]

    assert client.get(f"/tasks/{task['id']}").json()["prompt"] == "fix the bug"
    assert [t["id"] for t in client.get("/tasks").json()] == [task["id"]]

    r = client.post(f"/tasks/{task['id']}/cancel")
    assert r.json()["status"] == "cancelled"
    assert client.get("/tasks", params={"status": "queued"}).json() == []


def test_validation(client):
    assert client.post("/tasks", json={}).status_code == 422
    assert client.post("/tasks", json={"prompt": "x", "parent_task_id": "nope"}).status_code == 422
    assert client.get("/tasks/nope").status_code == 404


def test_issue_shortcut(client):
    r = client.post("/issues/run", json={"repo": "acme/app", "number": 3, "verify_command": "make test"})
    assert r.status_code == 202
    assert r.json()["prompt"] == "acme/app#3"


def test_auth(client, monkeypatch):
    monkeypatch.setattr(settings, "api_token", "s3cret")
    assert client.get("/tasks").status_code == 401
    assert client.get("/tasks", headers={"Authorization": "Bearer s3cret"}).status_code == 200


async def test_stream_replays_until_terminal(client, redis):
    task_id = client.post("/tasks", json={"prompt": "x"}).json()["id"]
    store = TaskStore(redis)
    assert await store.claim(task_id, "w1")
    await store.append_event(task_id, "text", {"text": "hi"})
    await store.finish(task_id, TaskStatus.succeeded, result="ok")

    with client.stream("GET", f"/tasks/{task_id}/stream") as r:
        body = "".join(r.iter_text())
    events = [line.removeprefix("event: ") for line in body.splitlines() if line.startswith("event: ")]
    assert events == ["status", "status", "text", "status"]

    page = client.get(f"/tasks/{task_id}/events", params={"after": "0", "limit": 2}).json()
    rest = client.get(f"/tasks/{task_id}/events", params={"after": page[-1]["id"]}).json()
    assert [e["type"] for e in page + rest] == ["status", "status", "text", "status"]
