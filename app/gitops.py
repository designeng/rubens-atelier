"""Git plumbing run by the harness (not the agent)."""

import asyncio
import base64
import os
from pathlib import Path


class GitError(RuntimeError):
    pass


async def run(
    *args: str, cwd: Path | None = None, check: bool = True, timeout: float = 600
) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=os.environ | {"GIT_TERMINAL_PROMPT": "0"},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise GitError(f"{' '.join(args[:2])} timed out after {timeout}s") from None
    text = out.decode(errors="replace")
    if check and proc.returncode != 0:
        raise GitError(f"{' '.join(args[:3])} failed ({proc.returncode}): {text[-2000:]}")
    return proc.returncode or 0, text


async def run_shell(cmd: str, cwd: Path, timeout: float) -> tuple[int, str]:
    """Run a user-provided verify command. Inherits the worker env minus secrets."""
    proc = await asyncio.create_subprocess_shell(
        cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return 124, f"verify command timed out after {timeout}s"
    return proc.returncode or 0, out.decode(errors="replace")


def _auth_args(token: str | None) -> list[str]:
    if not token:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    # Passed per command so the token is never written to .git/config where the agent could read it.
    return ["-c", f"http.extraheader=AUTHORIZATION: basic {basic}"]


async def clone(url: str, dest: Path, ref: str | None = None, token: str | None = None) -> None:
    args = ["git", *_auth_args(token), "clone"]
    if ref:
        args += ["--branch", ref]
    await run(*args, "--", url, str(dest))


async def push(cwd: Path, branch: str, token: str | None = None) -> None:
    await run("git", *_auth_args(token), "push", "--force-with-lease", "-u", "origin", branch, cwd=cwd)


async def configure_identity(cwd: Path, name: str, email: str) -> None:
    await run("git", "config", "user.name", name, cwd=cwd)
    await run("git", "config", "user.email", email, cwd=cwd)


async def checkout_new_branch(cwd: Path, branch: str) -> None:
    await run("git", "checkout", "-B", branch, cwd=cwd)


async def commit_all_if_dirty(cwd: Path, message: str) -> bool:
    _, status = await run("git", "status", "--porcelain", cwd=cwd)
    if not status.strip():
        return False
    await run("git", "add", "-A", cwd=cwd)
    await run("git", "commit", "-m", message, cwd=cwd)
    return True


async def commits_ahead(cwd: Path, base: str) -> int:
    _, out = await run("git", "rev-list", "--count", f"origin/{base}..HEAD", cwd=cwd)
    return int(out.strip() or 0)
