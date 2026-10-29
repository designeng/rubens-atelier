"""One-off shell commands in a story's workspace, started from the board.

The API only records the request in Redis and enqueues `harness.exec`; the worker (which owns
the writable workspace) runs it and stores the output for the board to poll. A command at a
time per item. This is remote code execution by design: it is off unless `terminal_enabled`
and the API is token-protected. The command runs without the harness's own settings in its
environment (database, GitHub credentials, API token).

Layout:
  harness:exec:{id}        hash   — item_id, command, status (running|done|timeout), exit_code, output
  harness:exec:item:{iid}  string — id of the item's running command (the per-item lock)
"""

import asyncio
import os
import signal
import uuid
from pathlib import Path

from redis.asyncio import Redis

from .config import settings
from .execution import item_workspace

KEEP_SECONDS = 3600
OUTPUT_LIMIT = 100_000  # the tail is kept
COMMAND_LIMIT = 4000


def exec_key(exec_id: str) -> str:
    return f"harness:exec:{exec_id}"


def lock_key(item_id: str) -> str:
    return f"harness:exec:item:{item_id}"


async def create(r: Redis, item_id: str, command: str) -> str | None:
    """Record a command and take the item's lock. None if the item is busy with another one."""
    exec_id = uuid.uuid4().hex
    if not await r.set(lock_key(item_id), exec_id, nx=True, ex=settings.terminal_timeout_seconds + 30):
        return None
    await r.hset(exec_key(exec_id), mapping={"item_id": item_id, "command": command, "status": "running", "output": ""})
    await r.expire(exec_key(exec_id), KEEP_SECONDS)
    return exec_id


async def get(r: Redis, exec_id: str) -> dict | None:
    row = await r.hgetall(exec_key(exec_id))
    if not row:
        return None
    row["exit_code"] = int(row["exit_code"]) if row.get("exit_code") else None
    return row


async def celery_enqueue(exec_id: str) -> None:
    from .celery_app import exec_command

    await asyncio.to_thread(exec_command.apply_async, args=[exec_id])


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")} | {"GIT_TERMINAL_PROMPT": "0"}


async def run(r: Redis, exec_id: str) -> None:
    row = await get(r, exec_id)
    if row is None or row["status"] != "running":
        return
    item_id, cwd = row["item_id"], item_workspace(row["item_id"])
    status, code, out = "done", None, b""
    try:
        if not (cwd / ".git").exists():
            raise FileNotFoundError(f"no workspace at {cwd}")
        out, status, code = await _spawn(row["command"], cwd, settings.terminal_timeout_seconds)
    except Exception as e:
        out = f"{type(e).__name__}: {e}".encode()
    await r.hset(exec_key(exec_id), mapping={
        "status": status, "exit_code": "" if code is None else str(code), "output": out.decode(errors="replace"),
    })
    await r.expire(exec_key(exec_id), KEEP_SECONDS)
    if await r.get(lock_key(item_id)) == exec_id:
        await r.delete(lock_key(item_id))


async def _spawn(command: str, cwd: Path, timeout: float) -> tuple[bytes, str, int | None]:
    proc = await asyncio.create_subprocess_shell(
        command, cwd=cwd, env=_env(), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,  # so a timeout kills the whole process tree
    )
    out = bytearray()

    async def drain():
        while chunk := await proc.stdout.read(8192):
            out.extend(chunk)
            del out[:-OUTPUT_LIMIT]

    try:
        await asyncio.wait_for(asyncio.gather(drain(), proc.wait()), timeout)
    except TimeoutError:
        os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        return bytes(out) + f"\n[killed: timed out after {timeout:g}s]".encode(), "timeout", None
    return bytes(out), "done", proc.returncode
