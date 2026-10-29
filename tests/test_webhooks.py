import json

import pytest
from sqlalchemy import update

from app.api import app
from app.config import settings
from app.db import WorkItem
from app.webhooks import signature

SHA = "a" * 40


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "s3cret")
    return "s3cret"


def deliver(client, event, payload, secret="s3cret", sign=True):
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = signature(secret, body)
    return client.post("/github/webhook", content=body, headers=headers)


def item_in(client, state, repo="acme/app"):
    """A story of `repo` advanced to `state` (`ci` or `awaiting_merge`), PR #7 at SHA."""
    slug = repo.split("/")[1]
    client.post("/projects", json={"slug": slug, "repo": repo})
    item = client.post(f"/projects/{slug}/items", json={"title": "x", "start": True}).json()
    verdicts = ["ready", "done", "done", "approved", "done"] + (["passed"] if state == "awaiting_merge" else [])
    for verdict in verdicts:
        version = client.get(f"/items/{item['id']}").json()["version"]
        client.portal.call(app.state.engine.advance, item["id"], version, verdict)

    async def push():
        async with app.state.engine.db.session() as s:
            pr_url = f"https://github.com/{repo}/pull/7"
            await s.execute(update(WorkItem).where(WorkItem.id == item["id"]).values(head_sha=SHA, pr_url=pr_url))
            await s.commit()

    client.portal.call(push)
    assert client.get(f"/items/{item['id']}").json()["state"] == state
    return item["id"]


def check_run(action="completed", sha=SHA, repo="acme/app"):
    return {"action": action, "check_run": {"head_sha": sha}, "repository": {"full_name": repo}}


def test_webhook_is_off_without_a_secret(client):
    assert deliver(client, "ping", {"zen": "hi"}).status_code == 404


def test_signature_is_required(client, secret):
    assert deliver(client, "ping", {"zen": "hi"}, sign=False).status_code == 401
    assert deliver(client, "ping", {"zen": "hi"}, secret="wrong").status_code == 401
    r = deliver(client, "ping", {"zen": "hi"})
    assert r.status_code == 200 and r.json() == {"items": []}
    assert client.checks_enqueued == []


def test_finished_checks_trigger_the_items_on_that_commit(client, secret):
    item_id = item_in(client, "ci")
    other = item_in(client, "ci", repo="acme/other")

    assert deliver(client, "check_run", check_run(repo="Acme/App")).json() == {"items": [item_id]}
    suite = {"action": "completed", "check_suite": {"head_sha": SHA}, "repository": {"full_name": "acme/other"}}
    assert deliver(client, "check_suite", suite).json() == {"items": [other]}
    status = {"state": "failure", "sha": SHA, "repository": {"full_name": "acme/app"}}
    assert deliver(client, "status", status).json() == {"items": [item_id]}
    assert client.checks_enqueued == [[item_id], [other], [item_id]]

    # Not finished yet, another commit or repo, or not a check event at all: nothing to do.
    client.checks_enqueued.clear()
    for event, payload in [
        ("check_run", check_run(action="created")),
        ("status", {**status, "state": "pending"}),
        ("check_run", check_run(sha="b" * 40)),
        ("check_run", check_run(repo="acme/unknown")),
        ("pull_request", {"action": "closed", "pull_request": {"number": 8}, "repository": {"full_name": "acme/app"}}),
        ("push", {"after": SHA, "repository": {"full_name": "acme/app"}}),
    ]:
        assert deliver(client, event, payload).json() == {"items": []}, (event, payload)
    assert client.checks_enqueued == []


def test_closed_pr_triggers_the_item_awaiting_its_merge(client, secret):
    item_id = item_in(client, "awaiting_merge")

    def pr(action, number=7):
        return {"action": action, "pull_request": {"number": number}, "repository": {"full_name": "acme/app"}}

    assert deliver(client, "pull_request", pr("synchronize")).json() == {"items": []}
    assert deliver(client, "pull_request", pr("closed", number=70)).json() == {"items": []}
    assert deliver(client, "pull_request", pr("closed")).json() == {"items": [item_id]}
    assert client.checks_enqueued == [[item_id]]
