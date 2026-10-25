import pytest
from fakeredis import FakeAsyncRedis

from app import execution
from app.config import settings
from app.store import TaskStore


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspaces_root", tmp_path / "workspaces")
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
