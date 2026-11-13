"""Git plumbing run by the harness (not the agent)."""

import asyncio
import base64
import os
import shutil
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


async def clone(
    url: str,
    dest: Path,
    ref: str | None = None,
    token: str | None = None,
    seed_identity: tuple[str, str] | None = None,
) -> None:
    """Clone `url` into `dest`, checked out at branch `ref`.

    With `seed_identity` (name, email), a repository without a single commit is seeded instead
    of failing: an empty initial commit on `ref` is pushed, so stories have a base branch to
    start from and open PRs against."""
    args = ["git", *_auth_args(token), "clone"]
    if ref:
        args += ["--branch", ref]
    try:
        await run(*args, "--", url, str(dest))
    except GitError:
        if not (ref and seed_identity) or not await remote_is_empty(url, token):
            raise
        await _seed(url, dest, ref, token, seed_identity)


async def remote_is_empty(url: str, token: str | None = None) -> bool:
    """Whether the remote has no refs at all, i.e. no commits yet."""
    _, out = await run("git", *_auth_args(token), "ls-remote", "--", url, timeout=60)
    return not out.strip()


async def _seed(url: str, dest: Path, branch: str, token: str | None, identity: tuple[str, str]) -> None:
    name, email = identity
    if dest.exists():
        shutil.rmtree(dest)
    await run("git", *_auth_args(token), "clone", "--", url, str(dest))  # empty clones succeed
    await run("git", "checkout", "-B", branch, cwd=dest)
    await run(
        "git", "-c", f"user.name={name}", "-c", f"user.email={email}",
        "commit", "--allow-empty", "-m", "Initial commit", cwd=dest,
    )
    ref = f"refs/heads/{branch}"
    # The empty lease means "only if the branch does not exist yet", so a seed never lands on top of another.
    code, _ = await run(
        "git", *_auth_args(token), "push", f"--force-with-lease={ref}:", "origin", f"HEAD:{ref}",
        cwd=dest, check=False,
    )
    if code != 0:
        # Another story seeded it first: take that commit rather than our own.
        shutil.rmtree(dest)
        await run("git", *_auth_args(token), "clone", "--branch", branch, "--", url, str(dest))


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


async def head_sha(cwd: Path) -> str:
    _, out = await run("git", "rev-parse", "HEAD", cwd=cwd)
    return out.strip()


async def fetch(cwd: Path, ref: str, token: str | None = None) -> None:
    await run("git", *_auth_args(token), "fetch", "origin", ref, cwd=cwd)


def merge_in_progress(cwd: Path) -> bool:
    return (cwd / ".git" / "MERGE_HEAD").exists()


async def unmerged_files(cwd: Path) -> list[str]:
    _, out = await run("git", "diff", "--name-only", "--diff-filter=U", cwd=cwd)
    return out.split()


async def is_ancestor(cwd: Path, ancestor: str, ref: str = "HEAD") -> bool:
    code, _ = await run("git", "merge-base", "--is-ancestor", ancestor, ref, cwd=cwd, check=False)
    return code == 0


async def merge(cwd: Path, ref: str, message: str) -> list[str]:
    """Merge `ref` into the current branch. Returns the conflicted files ([] = merged cleanly);
    on conflict the merge is left in progress for someone to resolve."""
    code, out = await run("git", "merge", "--no-ff", "-m", message, ref, cwd=cwd, check=False)
    if code == 0:
        return []
    if conflicts := await unmerged_files(cwd):
        return conflicts
    raise GitError(f"git merge {ref} failed ({code}): {out[-2000:]}")


async def change_summary(cwd: Path, base: str, limit: int) -> str:
    """The branch's commits, diffstat and diff against `origin/<base>` (from the merge base, as
    a PR shows them), the diff cut at `limit` characters with a note on how to read the rest."""
    ref = f"origin/{base}...HEAD"

    async def git(*args: str) -> str:
        _, out = await run("git", "--no-optional-locks", *args, cwd=cwd, timeout=60)
        return out.strip()

    commits = await git("log", "--format=%h %s", f"origin/{base}..HEAD")
    stat = await git("diff", "--stat", ref)
    diff = await git("diff", ref)
    if len(diff) > limit:
        diff = (
            diff[:limit]
            + f"\n… [diff cut at {limit} of {len(diff)} characters: run `git diff {ref} -- <file>` for the rest]"
        )
    return f"Commits:\n{commits or '(none)'}\n\nFiles:\n{stat or '(no changes)'}\n\n{diff}"


async def snapshot(cwd: Path, base: str | None, diff: bool = False, limit: int = 60_000) -> dict[str, str | bool]:
    """Read-only picture of a workspace for the board: branch, uncommitted changes, commits and
    changes against the base branch, optionally the full diff. Takes no locks, writes nothing."""

    async def git(*args: str) -> str:
        _, out = await run("git", "--no-optional-locks", *args, cwd=cwd, check=False, timeout=30)
        return out.strip()

    ref = f"origin/{base}" if base else "HEAD"
    snap: dict[str, str | bool] = {
        "branch": await git("rev-parse", "--abbrev-ref", "HEAD"),
        "head": await git("log", "-1", "--format=%h %s"),
        "merge_in_progress": merge_in_progress(cwd),
        "status": await git("status", "--short", "--branch"),
        "commits": await git("log", "--format=%h %an %ar  %s", f"{ref}..HEAD"),
        "stat": await git("diff", "--stat", ref),  # committed and uncommitted, against the base
    }
    if diff:
        snap["diff"] = (await git("diff", ref))[:limit]
    return snap
