import subprocess
from pathlib import Path

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
    monkeypatch.setattr(settings, "lean_context", False)  # a local .env may turn it on
    monkeypatch.setattr(execution, "WATCHDOG_INTERVAL", 0.05)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def origin(tmp_path) -> Path:
    """Bare repo with one commit on `main`, standing in for github.com/acme/app."""
    bare = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    git(tmp_path, "init", "-b", "main", str(seed))
    (seed / "README.md").write_text("# app\n")
    git(seed, "add", ".")
    git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    git(seed, "push", str(bare), "main")
    return bare


class FakeGitHub:
    def __init__(self, origin: Path):
        self.origin = origin
        self.token = "test-token"
        self.labels: set[str] = {"agent"}
        self.comments: list[str] = []
        self.prs: list[dict] = []

    def clone_url(self, repo):
        return str(self.origin)

    async def aclose(self):
        pass

    async def git_token(self):
        return self.token

    async def get_repo(self, repo):
        return {"default_branch": "main"}

    async def get_issue(self, repo, number):
        return {"title": "Add greeting", "body": "Create hello.txt saying hi.", "comments": 1}

    async def get_issue_comments(self, repo, number):
        return [{"user": {"login": "alice"}, "body": "Lowercase please."}]

    async def set_labels(self, repo, number, add=(), remove=()):
        self.labels = (self.labels - set(remove)) | set(add)

    async def comment(self, repo, number, body):
        self.comments.append(body)

    async def find_open_pr(self, repo, branch):
        return next((p for p in self.prs if p["head"] == branch), None)

    async def create_pr(self, repo, *, head, base, title, body):
        number = len(self.prs) + 1
        pr = {"head": head, "base": base, "title": title, "body": body, "html_url": f"https://gh/pull/{number}"}
        self.prs.append(pr)
        return pr


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
def client(redis, db):
    # `db` creates the schema: the API's startup no longer does.
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
        c.removals = []

        async def workspaces_enqueue(ids, include_failed):
            c.removals.append((ids, include_failed))

        app.state.workspaces_enqueue = workspaces_enqueue
        c.checks_enqueued = []

        async def checks_enqueue(item_ids):
            c.checks_enqueued.append(item_ids)

        app.state.checks_enqueue = checks_enqueue
        yield c
