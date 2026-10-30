import os
import time

import pytest

from app import service, workspaces
from app.api import app
from app.archive import archive_task
from app.config import settings
from app.db import WorkItem
from app.models import TaskStatus


def make_dir(path, size=0):
    path.mkdir(parents=True)
    if size:
        (path / "data.bin").write_bytes(b"x" * size)
    return path


def age(path, hours):
    t = time.time() - hours * 3600
    os.utime(path, (t, t))


def set_state(client, item_id, state):
    async def run():
        async with app.state.db.session() as s:
            (await s.get(WorkItem, item_id)).state = state
            await s.commit()

    client.portal.call(run)


@pytest.fixture
def volume(client, monkeypatch):
    """One workspace of every kind; returns {name: id} and creates the directories."""
    monkeypatch.setattr(service.run_agent, "apply_async", lambda args, task_id: None)
    root = settings.workspaces_root
    client.post("/projects", json={"slug": "app", "repo": "acme/app"})
    client.post("/projects", json={"slug": "other", "repo": "acme/other"})

    def item(title, project="app", **kw):
        return client.post(f"/projects/{project}/items", json={"title": title, **kw}).json()["id"]

    ids = {
        "backlog": item("Later"),
        "working": item("Now", start=True),
        "done": item("Merged"),
        "failed": item("Cancelled"),
        "other": item("Elsewhere", "other"),
    }
    set_state(client, ids["done"], "done")
    client.post(f"/items/{ids['failed']}/cancel")
    set_state(client, ids["other"], "done")
    for name, item_id in ids.items():
        make_dir(root / "items" / item_id, size=100)

    store = app.state.store
    queued = client.post("/tasks", json={"prompt": "queued task"}).json()["id"]
    ok = client.post("/tasks", json={"prompt": "good task"}).json()["id"]
    bad = client.post("/tasks", json={"prompt": "bad task"}).json()["id"]
    for task_id, status, fields in ((ok, TaskStatus.succeeded, {}), (bad, TaskStatus.failed, {"error": "boom"})):

        async def finish(task_id=task_id, status=status, fields=fields):
            await store.claim(task_id, "w1")
            await store.finish(task_id, status, **fields)

        client.portal.call(finish)
    ids |= {"queued_task": queued, "ok_task": ok, "bad_task": bad, "orphan": "a" * 32}
    for name in ("queued_task", "ok_task", "bad_task", "orphan"):
        make_dir(root / ids[name], size=10)
    return ids


def listing(client, **params):
    r = client.get("/workspaces", params=params)
    assert r.status_code == 200
    return {w["id"]: w for w in r.json()}


def test_list_binds_each_copy_and_decides_whether_it_can_go(client, volume):
    root = settings.workspaces_root
    (root / ".cache").mkdir()
    (root / "items" / "not-an-id").mkdir()
    (root / ("b" * 32)).write_text("a file, not a directory")
    (root / ("c" * 32)).symlink_to(root / "items")

    rows = listing(client)
    assert set(rows) == set(volume.values())
    phases = {name: rows[i]["phase"] for name, i in volume.items()}
    assert phases == {
        "backlog": "idle", "working": "active", "done": "finished", "failed": "failed", "other": "finished",
        "queued_task": "active", "ok_task": "finished", "bad_task": "failed", "orphan": "orphan",
    }
    assert {name for name, i in volume.items() if rows[i]["removable"]} == {
        "done", "failed", "other", "ok_task", "bad_task", "orphan",
    }

    done = rows[volume["done"]]
    assert done["item"]["project"] == "app" and done["item"]["title"] == "Merged"
    assert done["item"]["column"] == "done" and done["task"] is None
    assert done["path"] == str(root.resolve() / "items" / volume["done"])
    assert done["size_bytes"] == 100 and done["modified_at"]
    assert "running here" in rows[volume["working"]]["reason"]
    assert rows[volume["backlog"]]["item"]["column"] == "backlog"
    bad = rows[volume["bad_task"]]
    assert bad["task"] == {"id": volume["bad_task"], "status": "failed", "prompt": "bad task"} and bad["item"] is None
    orphan = rows[volume["orphan"]]
    assert orphan["item"] is None and orphan["task"] is None and orphan["size_bytes"] == 10


def test_list_reads_an_expired_task_from_the_archive(client, volume):
    task_id = volume["bad_task"]
    assert client.portal.call(archive_task, app.state.db, app.state.store, task_id)
    client.portal.call(app.state.redis.flushall)
    row = listing(client)[task_id]
    assert row["phase"] == "failed" and row["task"]["status"] == "failed"
    assert listing(client)[volume["ok_task"]]["phase"] == "orphan"  # expired and never archived


def test_list_filters_and_skips_sizes(client, volume):
    assert all(w["size_bytes"] is None for w in listing(client, size="false").values())
    assert set(listing(client, phase="finished")) == {volume[k] for k in ("done", "other", "ok_task")}
    assert set(listing(client, project="other")) == {volume["other"]}
    assert set(listing(client, project="app", phase="finished")) == {volume["done"]}
    assert listing(client, project="nope") == {}
    assert client.get("/workspaces", params={"phase": "bogus"}).status_code == 422


def test_delete_removes_one_copy_unless_it_is_in_use(client, volume):
    root = settings.workspaces_root
    for name in ("working", "backlog", "queued_task"):
        r = client.delete(f"/workspaces/{volume[name]}")
        assert r.status_code == 409 and r.json()["detail"]
    assert (root / "items" / volume["working"]).exists()

    assert client.delete(f"/workspaces/{volume['done']}").status_code == 204
    assert not (root / "items" / volume["done"]).exists()
    assert client.delete(f"/workspaces/{volume['bad_task']}").status_code == 204
    assert client.delete(f"/workspaces/{volume['orphan']}").status_code == 204
    assert client.delete(f"/workspaces/{volume['done']}").status_code == 404
    assert volume["done"] not in listing(client)


def test_delete_refuses_ids_that_are_not_workspaces(client, volume, tmp_path):
    root = settings.workspaces_root
    (root / ".cache").mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (root / ("d" * 32)).symlink_to(victim)
    for bad in (".cache", "items", "..", "d" * 32, "A" * 32, "x"):
        assert client.delete(f"/workspaces/{bad}").status_code in (404, 405)
    assert (root / ".cache").exists() and victim.exists()


def test_prune_dry_run_reports_without_deleting(client, volume):
    root = settings.workspaces_root
    r = client.post("/workspaces/prune", json={"dry_run": True})
    body = r.json()
    assert body["dry_run"] is True and body["skipped"] == []
    assert {x["id"] for x in body["removed"]} == {volume[k] for k in ("done", "other", "ok_task", "orphan")}
    assert body["freed_bytes"] == 100 + 100 + 10 + 10
    assert all((root / "items" / i).exists() or (root / i).exists() for i in volume.values())


def test_prune_removes_finished_and_orphans_and_failed_only_on_request(client, volume):
    root = settings.workspaces_root
    body = client.post("/workspaces/prune", json={}).json()
    assert body["dry_run"] is False and body["freed_bytes"] == 220
    assert set(listing(client)) == {volume[k] for k in ("backlog", "working", "queued_task", "failed", "bad_task")}

    body = client.post("/workspaces/prune", json={"include_failed": True}).json()
    assert {x["id"] for x in body["removed"]} == {volume["failed"], volume["bad_task"]}
    assert set(listing(client)) == {volume[k] for k in ("backlog", "working", "queued_task")}
    assert not (root / "items" / volume["failed"]).exists()


def test_prune_keeps_recent_copies(client, volume):
    root = settings.workspaces_root
    age(root / "items" / volume["done"], 48)
    age(root / volume["orphan"], 2)
    body = client.post("/workspaces/prune", json={"older_than_hours": 24}).json()
    assert [x["id"] for x in body["removed"]] == [volume["done"]]
    assert volume["orphan"] in listing(client)
    assert client.post("/workspaces/prune", json={"older_than_hours": -1}).status_code == 422


def test_prune_reports_a_copy_it_could_not_remove_and_goes_on(client, volume, monkeypatch):
    real = workspaces._remove

    def flaky(path, parent):
        if path.name == volume["done"]:
            raise PermissionError("busy")
        real(path, parent)

    monkeypatch.setattr(workspaces, "_remove", flaky)
    body = client.post("/workspaces/prune", json={}).json()
    assert body["skipped"] == [{"id": volume["done"], "error": "busy"}]
    assert {x["id"] for x in body["removed"]} == {volume[k] for k in ("other", "ok_task", "orphan")}
    assert body["freed_bytes"] == 120


def test_workspaces_need_the_token(client, monkeypatch):
    monkeypatch.setattr(settings, "api_token", "secret")
    assert client.get("/workspaces").status_code == 401
    assert client.delete("/workspaces/" + "a" * 32).status_code == 401
    assert client.post("/workspaces/prune", json={}).status_code == 401
    assert client.get("/workspaces", headers={"Authorization": "Bearer secret"}).status_code == 200
