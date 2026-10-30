from app.api import app
from app.db import Run


def _spend(client, item_id, version, cost):
    async def add():
        async with app.state.db.session() as s:
            s.add(Run(item_id=item_id, version=version, state="x", role="r", task_id=f"t{version}", cost_usd=cost))
            await s.commit()

    client.portal.call(add)


def test_budgets_are_set_cleared_and_validated(client):
    client.post("/projects", json={"slug": "app", "repo": "acme/app"})
    epic = client.post("/projects/app/items", json={"kind": "epic", "title": "E", "budget_usd": 10}).json()
    assert epic["budget_usd"] == 10 and epic["spent_usd"] == 0

    assert client.patch(f"/items/{epic['id']}", json={"budget_usd": 12.5}).json()["budget_usd"] == 12.5
    assert client.patch(f"/items/{epic['id']}", json={"title": "E2"}).json()["budget_usd"] == 12.5
    assert client.patch(f"/items/{epic['id']}", json={"budget_usd": None}).json()["budget_usd"] is None
    for bad in (0, -1):
        assert client.patch(f"/items/{epic['id']}", json={"budget_usd": bad}).status_code == 422
        assert client.post("/projects/app/items", json={"title": "x", "budget_usd": bad}).status_code == 422
        assert client.patch("/projects/app", json={"budget_usd": bad}).status_code == 422

    r = client.patch("/projects/app", json={"budget_usd": 50})
    assert (r.json()["budget_usd"], r.json()["auto_merge"]) == (50, False)
    assert client.patch("/projects/app", json={"auto_merge": True}).json()["budget_usd"] == 50
    assert client.patch("/projects/app", json={"budget_usd": None}).json()["budget_usd"] is None


def test_project_budget_can_be_set_on_creation(client):
    r = client.post("/projects", json={"slug": "app", "repo": "acme/app", "budget_usd": 20})
    assert (r.json()["budget_usd"], r.json()["spent_usd"]) == (20, 0)


def test_spent_money_is_shown_per_item_with_children_and_per_project(client):
    client.post("/projects", json={"slug": "app", "repo": "acme/app"})
    client.post("/projects", json={"slug": "other", "repo": "acme/other"})
    epic = client.post("/projects/app/items", json={"kind": "epic", "title": "E"}).json()
    story = client.post("/projects/app/items", json={"title": "S", "parent_id": epic["id"]}).json()
    lone = client.post("/projects/app/items", json={"title": "L"}).json()
    foreign = client.post("/projects/other/items", json={"title": "F"}).json()
    _spend(client, story["id"], 1, 1.25)
    _spend(client, story["id"], 2, None)
    _spend(client, epic["id"], 1, 0.5)
    _spend(client, lone["id"], 1, 3.0)
    _spend(client, foreign["id"], 1, 9.0)

    spent = {i["title"]: i["spent_usd"] for col in client.get("/projects/app/board").json().values() for i in col}
    assert spent == {"E": 1.75, "S": 1.25, "L": 3.0}
    assert client.get(f"/items/{epic['id']}").json()["spent_usd"] == 1.75
    assert {p["slug"]: p["spent_usd"] for p in client.get("/projects").json()} == {"app": 4.75, "other": 9.0}
    assert client.patch("/projects/app", json={"budget_usd": 5}).json()["spent_usd"] == 4.75
