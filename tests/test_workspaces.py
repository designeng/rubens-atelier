import os
import time
from functools import partial

from app import workspaces
from app.api import app
from app.config import settings
from app.execution import item_workspace
from app.models import TaskCreate


def _mkdir(path, size=10):
    path.mkdir(parents=True)
    (path / "f.txt").write_text("x" * size)


def _by_id(client, **params):
    return {w["id"]: w for w in client.get("/workspaces", params=params).json()["workspaces"]}


def _setup(client):
    """One workspace per phase: finished, failed, idle, busy, needs-human, orphan, task."""
    engine = app.state.engine
    client.post("/projects", json={"slug": "app", "repo": "acme/app"})

    def new(title, **kw):
        return client.post("/projects/app/items", json={"title": title, **kw}).json()["id"]

    def walk(iid, *verdicts):
        for v in verdicts:
            version = client.get(f"/items/{iid}").json()["version"]
            client.portal.call(engine.advance, iid, version, v)

    ids = {
        "finished": new("done", start=True),
        "failed": new("cancelled", start=True),
        "idle": new("backlog"),
        "busy": new("working", start=True),
        "human": new("waiting", start=True),
    }
    walk(ids["finished"], "ready", "done", "done", "approved", "done", "passed", "merged")
    client.post(f"/items/{ids['failed']}/cancel")
    walk(ids["human"], "ready", "done", "done", "approved", "done", "passed", "manual")
    for iid in ids.values():
        _mkdir(item_workspace(iid))
    ids["orphan"] = "a" * 32
    _mkdir(item_workspace(ids["orphan"]), size=100)

    ids["task_done"] = client.portal.call(app.state.store.create, TaskCreate(prompt="old"))
    client.portal.call(partial(app.state.store.set_fields, ids["task_done"], status="succeeded"))
    ids["task_running"] = client.portal.call(app.state.store.create, TaskCreate(prompt="now"))
    client.portal.call(partial(app.state.store.set_fields, ids["task_running"], status="running"))
    for key in ("task_done", "task_running"):
        _mkdir(settings.workspaces_root / ids[key])
    (settings.workspaces_root / ".cache" / "uv").mkdir(parents=True)
    return ids


def test_list_workspaces(client):
    ids = _setup(client)
    rows = _by_id(client)
    assert ".cache" not in rows and len(rows) == 8
    phases = {k: rows[v]["phase"] for k, v in ids.items()}
    assert phases == {
        "finished": "finished", "failed": "failed", "idle": "idle", "busy": "busy", "human": "busy",
        "orphan": "orphan", "task_done": "finished", "task_running": "busy",
    }
    done = rows[ids["finished"]]
    assert done["item"]["title"] == "done" and done["item"]["project"] == "app" and done["removable"]
    assert done["size_bytes"] == 10 and rows[ids["orphan"]]["size_bytes"] == 100
    assert rows[ids["task_done"]]["task"]["status"] == "succeeded"
    assert not rows[ids["busy"]]["removable"] and "step is running" in rows[ids["busy"]]["reason"]

    body = client.get("/workspaces", params={"size": False}).json()
    assert body["size_bytes"] is None and all(w["size_bytes"] is None for w in body["workspaces"])
    assert set(_by_id(client, phase="orphan")) == {ids["orphan"]}
    assert set(_by_id(client, project="app")) == {ids[k] for k in ("finished", "failed", "idle", "busy", "human")}


def test_exec_in_progress_blocks_removal(client):
    ids = _setup(client)
    client.portal.call(app.state.redis.set, f"harness:exec:item:{ids['finished']}", "e1")
    row = _by_id(client)[ids["finished"]]
    assert row["phase"] == "busy" and not row["removable"]


def test_prune(client):
    ids = _setup(client)
    r = client.post("/workspaces/prune", json={"dry_run": True})
    assert r.status_code == 202 and set(r.json()["would_remove"]) == {ids["finished"], ids["orphan"], ids["task_done"]}
    assert r.json()["queued"] == [] and client.removals == []

    r = client.post("/workspaces/prune", json={})
    assert set(client.removals[-1][0]) == set(r.json()["queued"]) == {ids["finished"], ids["orphan"], ids["task_done"]}
    assert client.removals[-1][1] is False

    r = client.post("/workspaces/prune", json={"include_failed": True, "dry_run": True})
    assert set(r.json()["would_remove"]) == {ids[k] for k in ("finished", "orphan", "task_done", "failed", "idle")}

    # Age counts from the owner's last change, or the directory's mtime for an orphan.
    assert client.post("/workspaces/prune", json={"older_than_hours": 1, "dry_run": True}).json()["would_remove"] == []
    old = time.time() - 7200
    os.utime(item_workspace(ids["orphan"]), (old, old))
    pruned = client.post("/workspaces/prune", json={"older_than_hours": 1, "dry_run": True}).json()
    assert pruned["would_remove"] == [ids["orphan"]]
    assert client.post("/workspaces/prune", json={"older_than_hours": -1}).status_code == 422


def test_delete_workspace(client):
    ids = _setup(client)
    assert client.delete(f"/workspaces/{'b' * 32}").status_code == 404
    assert client.delete(f"/workspaces/{ids['busy']}").status_code == 409
    assert client.delete(f"/workspaces/{ids['human']}").status_code == 409
    assert client.delete(f"/workspaces/{ids['task_running']}").status_code == 409
    r = client.delete(f"/workspaces/{ids['failed']}")
    assert r.status_code == 202 and client.removals[-1] == ([ids["failed"]], True)


def test_worker_removes_and_rechecks(client):
    ids = _setup(client)
    engine = app.state.engine
    wanted = [ids[k] for k in ("finished", "orphan", "task_done", "failed", "busy", "human")]

    result = client.portal.call(workspaces.remove, engine, wanted)
    assert [result[ids[k]] for k in ("finished", "orphan", "task_done")] == ["removed"] * 3
    assert result[ids["failed"]].startswith("kept") and result[ids["busy"]].startswith("kept")
    assert not item_workspace(ids["finished"]).exists() and item_workspace(ids["failed"]).exists()
    assert (settings.workspaces_root / ".cache" / "uv").exists()

    result = client.portal.call(partial(workspaces.remove, engine, wanted, include_failed=True))
    assert result[ids["failed"]] == "removed" and result[ids["busy"]].startswith("kept")
    assert result[ids["finished"]] == "not found"
    assert item_workspace(ids["busy"]).exists() and item_workspace(ids["human"]).exists()
