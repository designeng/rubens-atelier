import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import api, service
from app.api import app
from app.celery_app import celery_app
from app.config import settings
from app.db import Run, utcnow
from app.store import TaskStore


@pytest.fixture
def client(redis, db, monkeypatch):
    monkeypatch.setattr(service.run_agent, "apply_async", lambda args, task_id: None)
    with TestClient(app) as c:
        app.state.redis, app.state.store = redis, TaskStore(redis)
        yield c


def test_healthz_checks_redis_and_postgres(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "redis": True, "postgres": True}


def test_healthz_fails_when_postgres_is_down(client, monkeypatch):
    async def down():
        raise ConnectionError("postgres is down")

    monkeypatch.setattr(app.state.db, "ping", down)
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json() == {"ok": False, "redis": True, "postgres": False}


def test_healthz_fails_when_redis_is_down(client, monkeypatch):
    async def down():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(app.state.redis, "ping", down)
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json() == {"ok": False, "redis": False, "postgres": True}


def test_healthz_times_out_a_hanging_dependency(client, monkeypatch):
    async def hang():
        await asyncio.sleep(60)

    monkeypatch.setattr(api, "HEALTH_TIMEOUT", 0.05)
    monkeypatch.setattr(app.state.db, "ping", hang)
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json()["postgres"] is False


def test_metrics_requires_token(client, monkeypatch):
    monkeypatch.setattr(settings, "api_token", "s3cret")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def _metrics(client) -> dict[str, float]:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    samples = (line.rsplit(" ", 1) for line in r.text.splitlines() if not line.startswith("#"))
    return {name: float(value) for name, value in samples}


def test_metrics_empty(client):
    m = _metrics(client)
    assert m["harness_queue_depth"] == 0
    assert m["harness_run_cost_usd_sum"] == 0
    assert m["harness_run_duration_seconds_count"] == 0
    assert m["harness_run_success_ratio"] == 0


async def test_metrics_aggregates_runs_and_queue(client, redis, db):
    now = utcnow()

    def run(version, status, cost, seconds, age=timedelta()):
        created = now - age
        return Run(
            item_id="i",
            version=version,
            state="implement",
            role="dev",
            task_id=f"t{version}",
            status=status,
            cost_usd=cost,
            created_at=created,
            finished_at=created + timedelta(seconds=seconds) if seconds is not None else None,
        )

    async with db.session() as s:
        s.add_all(
            [
                run(1, "succeeded", 1.5, 60),
                run(2, "succeeded", 0.5, 30),
                run(3, "failed", 1.0, 10),
                run(4, "running", None, None),
                run(5, "succeeded", 99.0, 5, age=timedelta(days=3)),  # outside the window
            ]
        )
        await s.commit()
    await redis.rpush(celery_app.conf.task_default_queue, "a", "b")
    await service.submit(TaskStore(redis), service.TaskCreate(prompt="x"))

    m = await asyncio.to_thread(_metrics, client)
    assert m["harness_queue_depth"] == 2
    assert m['harness_tasks{status="queued"}'] == 1
    assert m['harness_runs{status="succeeded"}'] == 2
    assert m['harness_runs{status="failed"}'] == 1
    assert m['harness_runs{status="running"}'] == 1
    assert m["harness_run_cost_usd_sum"] == 3.0
    assert m["harness_run_duration_seconds_sum"] == 100
    assert m["harness_run_duration_seconds_count"] == 3
    assert m["harness_run_success_ratio"] == pytest.approx(2 / 3, abs=1e-5)
