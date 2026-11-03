import subprocess

from app import celery_app, sandbox_check
from app.config import settings


def _linux(monkeypatch, sandbox=True):
    monkeypatch.setattr(settings, "sandbox", sandbox)
    monkeypatch.setattr(sandbox_check.sys, "platform", "linux")


def test_probe_disabled(monkeypatch):
    _linux(monkeypatch, sandbox=False)
    assert sandbox_check.probe() == {"ok": True, "detail": "disabled"}


def test_probe_skips_non_linux(monkeypatch):
    monkeypatch.setattr(settings, "sandbox", True)
    monkeypatch.setattr(sandbox_check.sys, "platform", "darwin")
    assert sandbox_check.probe()["ok"] is True


def test_probe_missing_bwrap(monkeypatch):
    _linux(monkeypatch)
    monkeypatch.setattr(sandbox_check.shutil, "which", lambda _: None)
    assert sandbox_check.probe() == {"ok": False, "detail": "bwrap is not installed"}


def test_probe_nonzero_exit_hides_stderr(monkeypatch):
    _linux(monkeypatch)
    monkeypatch.setattr(sandbox_check.shutil, "which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(
        sandbox_check.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "clone failed: /secret/path"),
    )
    result = sandbox_check.probe()
    assert result["ok"] is False
    assert "/secret" not in result["detail"]


def test_probe_timeout(monkeypatch):
    _linux(monkeypatch)
    monkeypatch.setattr(sandbox_check.shutil, "which", lambda _: "/usr/bin/bwrap")

    def hang(*a, **k):
        raise subprocess.TimeoutExpired("bwrap", 5)

    monkeypatch.setattr(sandbox_check.subprocess, "run", hang)
    assert sandbox_check.probe() == {"ok": False, "detail": "bwrap timed out"}


def test_probe_success(monkeypatch):
    _linux(monkeypatch)
    monkeypatch.setattr(sandbox_check.shutil, "which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(
        sandbox_check.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")
    )
    assert sandbox_check.probe()["ok"] is True


async def test_report_read_round_trip(redis):
    assert await sandbox_check.read(redis) is None
    await sandbox_check.report(redis, {"ok": False, "detail": "x"}, "w1")
    got = await sandbox_check.read(redis)
    assert got["ok"] is False and got["detail"] == "x" and got["worker_id"] == "w1"
    assert got["checked_at"]
    assert await redis.ttl(sandbox_check.KEY) == -1  # no expiry: the verdict outlives the one probe


def test_check_sandbox_survives_redis_failure(monkeypatch):
    monkeypatch.setattr(sandbox_check, "probe", lambda: {"ok": False, "detail": "bwrap timed out"})

    def boom():
        raise ConnectionError("no redis")

    monkeypatch.setattr(celery_app, "_redis", boom)
    assert celery_app.check_sandbox()["ok"] is False
