"""The board stays cheap as finished stories pile up: a capped done column, no per-item queries."""

from datetime import datetime, timedelta

from sqlalchemy import event, update

from app import board_api
from app.api import app
from app.db import WorkItem, utcnow


def _set_states(client, states, updated: dict[str, datetime] | None = None):
    async def go():
        async with app.state.db.session() as s:
            for item_id, state in states.items():
                values = {"state": state} | ({"updated_at": updated[item_id]} if updated and item_id in updated else {})
                await s.execute(update(WorkItem).where(WorkItem.id == item_id).values(**values))
            await s.commit()

    client.portal.call(go)


def _statements(client, path):
    """The response and the SQL statements the request ran."""
    seen = []
    sync_engine = app.state.db.engine.sync_engine

    def listener(conn, cursor, statement, *rest):
        seen.append(statement)

    event.listen(sync_engine, "before_cursor_execute", listener)
    try:
        r = client.get(path)
    finally:
        event.remove(sync_engine, "before_cursor_execute", listener)
    return r, seen


def test_done_column_is_limited_and_newest_first(client):
    client.post("/projects", json={"slug": "app", "repo": "acme/app"})
    ids = [client.post("/projects/app/items", json={"title": f"Story {n}"}).json()["id"] for n in range(5)]
    # The first story finished last, so it is the most recently updated one.
    later = {ids[0]: utcnow() + timedelta(hours=1)}
    _set_states(client, {i: "done" for i in ids[:4]} | {ids[4]: "failed"}, later)

    r = client.get("/projects/app/board?done_limit=2")
    assert r.headers["X-Done-Total"] == "4"
    done = r.json()["done"]
    assert len(done) == 2 and done[0]["title"] == "Story 0"
    assert done[0]["updated_at"] >= done[1]["updated_at"]
    assert [i["title"] for i in r.json()["failed"]] == ["Story 4"]

    r = client.get("/projects/app/board")
    assert len(r.json()["done"]) == 4 and r.headers["X-Done-Total"] == "4"


def test_board_rejects_a_done_limit_out_of_range(client):
    client.post("/projects", json={"slug": "app", "repo": "acme/app"})
    for bad in (0, -1, board_api.DONE_LIMIT_MAX + 1, "x"):
        assert client.get(f"/projects/app/board?done_limit={bad}").status_code == 422
    assert client.get(f"/projects/app/board?done_limit={board_api.DONE_LIMIT_MAX}").status_code == 200


def test_board_query_count_does_not_grow_with_the_items(client):
    client.post("/projects", json={"slug": "app", "repo": "acme/app"})
    blocker = client.post("/projects/app/items", json={"title": "Blocker"}).json()["id"]

    def grow(n):
        for k in range(n):
            client.post("/projects/app/items", json={"title": f"Waiting {k}", "blocked_by": [blocker]})
            client.post("/projects/app/items", json={"title": f"Working {k}", "start": True})
            done = client.post("/projects/app/items", json={"title": f"Done {k}"}).json()["id"]
            _set_states(client, {done: "done"})

    grow(1)
    _, few = _statements(client, "/projects/app/board")
    grow(5)
    r, many = _statements(client, "/projects/app/board")
    assert len(r.json()["working"]) == 6 and len(r.json()["done"]) == 6
    assert len(many) == len(few)
