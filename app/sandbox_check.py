"""Startup check of the agent's Bash sandbox (bubblewrap).

The worker container is the one that runs the agent, so the worker probes bwrap on start and
publishes the verdict in Redis; the API's /healthz only reads it.
"""

import json
import logging
import shutil
import subprocess
import sys
from datetime import UTC, datetime

from redis.asyncio import Redis

from .config import settings

log = logging.getLogger(__name__)

KEY = "harness:sandbox"
PROBE_TIMEOUT = 5
HINT = (
    "Bash will fail. Uncomment `security_opt: [\"seccomp=unconfined\"]` for the worker in "
    "docker-compose.yml, use a seccomp profile that allows unprivileged user namespaces, "
    "or set HARNESS_SANDBOX=false."
)


def probe() -> dict:
    """{"ok": bool, "detail": str}. The detail is a short fixed reason: /healthz exposes it."""
    if not settings.sandbox:
        return {"ok": True, "detail": "disabled"}
    if not sys.platform.startswith("linux"):
        return {"ok": True, "detail": "not applicable on this platform"}
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return {"ok": False, "detail": "bwrap is not installed"}
    try:
        proc = subprocess.run(
            [bwrap, "--unshare-user", "--unshare-pid", "--ro-bind", "/", "/", "true"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "bwrap timed out"}
    except OSError as e:
        log.error("sandbox probe could not run bwrap: %s", e)
        return {"ok": False, "detail": "bwrap could not be run"}
    if proc.returncode != 0:
        log.error("sandbox probe: bwrap exited %s: %s", proc.returncode, proc.stderr.strip())
        return {"ok": False, "detail": "bwrap cannot create a sandbox (user namespaces blocked?)"}
    return {"ok": True, "detail": "ok"}


async def report(redis: Redis, result: dict, worker_id: str) -> None:
    payload = {**result, "worker_id": worker_id, "checked_at": datetime.now(UTC).isoformat()}
    await redis.set(KEY, json.dumps(payload))


async def read(redis: Redis) -> dict | None:
    raw = await redis.get(KEY)
    return json.loads(raw) if raw else None
