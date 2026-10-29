import pytest
from fakeredis import FakeAsyncRedis

from fastapi.testclient import TestClient

from app import execution
from app.api import app
from app.config import settings
from app.db import Database
from app.engine import Engine
from app.scheduler import Scheduler
from app.store import TaskStore
from app.workflow import load_workflows


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspaces_root", tmp_path / "workspaces")
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp_path / 'factory.db'}")
    monkeypatch.setattr(settings, "github_token", "test-token")
    monkeypatch.setattr(settings, "issue_verify_command", "")
    monkeypatch.setattr(execution, "WATCHDOG_INTERVAL", 0.05)


@pytest.fixture
async def redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def store(redis):
    return TaskStore(redis)


@pytest.fixture
async def db():
    database = Database(settings.database_url)
    await database.create_all()
    yield database
    await database.dispose()


@pytest.fixture
def client(redis):
    enqueued: list[tuple[str, int]] = []

    async def enqueue(item_id, version):
        enqueued.append((item_id, version))

    with TestClient(app) as c:
        app.state.redis, app.state.store = redis, TaskStore(redis)
        app.state.scheduler = Scheduler(redis, enqueue)
        app.state.engine = Engine(app.state.db, load_workflows(settings.workflows_dir), app.state.store, enqueue)
        c.enqueued = enqueued
        c.execs = []

        async def exec_enqueue(exec_id):
            c.execs.append(exec_id)

        app.state.exec_enqueue = exec_enqueue
        yield c
