"""Runs one Codex CLI session (`codex exec --json`) and translates its JSONL stream into the
same harness events as the Claude runner, so the board shows both alike.

Codex has no turn or budget limit and no per-tool permissions. The turn limit is enforced here;
what can't be enforced at all is refused (see `unsupported`), not silently ignored.
"""

import asyncio
import json
import os
import shlex
import shutil
import signal
import tempfile
from pathlib import Path
from typing import Any

from .config import settings
from .models import FailureReason, TaskCreate
from .runner import HIDDEN_ENV, HIDDEN_ENV_PREFIXES, AgentOutcome, Emit, _clip, secret_paths, uv_cache_dir

# Credentials of the other runtime; Codex needs neither.
CODEX_HIDDEN_PREFIXES = (*HIDDEN_ENV_PREFIXES, "ANTHROPIC_", "CLAUDE_CODE_")
WRITE_TOOLS = {"Write", "Edit"}
EFFORTS = {"max": "xhigh"}  # Codex's scale tops out at xhigh; the other levels share their names
MAX_LINE = 64 * 1024 * 1024  # one event holds a command's whole output
ERROR_TAIL = 2_000


def toml(value: Any) -> str:
    """A value for `-c key=value`. TOML's basic strings accept JSON's escapes."""
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(k)}={toml(v)}" for k, v in value.items()) + "}"
    return json.dumps(value, ensure_ascii=False)


def strict_schema(schema: Any) -> Any:
    """OpenAI's structured output requires every property to be listed as required."""
    if isinstance(schema, list):
        return [strict_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    strict = {k: strict_schema(v) for k, v in schema.items()}
    if isinstance(schema.get("properties"), dict):
        strict["required"] = list(schema["properties"])
        strict["additionalProperties"] = False
    return strict


def unsupported(spec: TaskCreate) -> str | None:
    """Limits Codex can't honour. Running anyway would give the agent more than was asked for."""
    if settings.bash_allowed_commands:
        return "HARNESS_BASH_ALLOWED_COMMANDS is set, and Codex can't limit commands to those prefixes"
    if spec.allowed_tools is not None:
        return "allowed_tools is set, and Codex has no per-tool permissions"
    extra = set(spec.disallowed_tools or []) - WRITE_TOOLS
    if extra:
        return f"disallowed_tools has {sorted(extra)}; Codex can only forbid {sorted(WRITE_TOOLS)}, as a whole"
    return None


def read_only(spec: TaskCreate) -> bool:
    return spec.permission_mode == "plan" or bool(WRITE_TOOLS & set(spec.disallowed_tools or []))


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()


def filesystem(spec: TaskCreate) -> dict[str, Any]:
    """Like the Claude sandbox: writes only in the workspace, the secret files unreadable. Codex's
    own `workspace-write` mode doesn't fit: it reads everything and keeps `.git` read-only."""
    rules: dict[str, Any] = {"/": "read"}
    if not read_only(spec):
        rules[":workspace_roots"] = {".": "write", ".git": "write"}
        rules[str(uv_cache_dir())] = "write"
    for path in (*secret_paths(), codex_home() / "auth.json"):
        rules[str(path)] = "deny"
    return rules


def build_args(spec: TaskCreate, resume: str | None, schema_file: Path | None) -> list[str]:
    lean = spec.lean_context if spec.lean_context is not None else settings.lean_context
    config: dict[str, Any] = {
        "approval_policy": "never",
        # The agent's shell gets the basics only, none of the worker's credentials.
        "shell_environment_policy.inherit": "core",
        "shell_environment_policy.set": {"UV_CACHE_DIR": str(uv_cache_dir())},
    }
    if settings.sandbox or read_only(spec):
        config |= {
            "default_permissions": "harness",
            "permissions.harness.filesystem": filesystem(spec),
            "permissions.harness.network.enabled": True,
        }
    else:
        config["sandbox_mode"] = "danger-full-access"
    if lean:
        config["project_doc_max_bytes"] = 0
    else:
        config["project_doc_fallback_filenames"] = ["CLAUDE.md"]
    if spec.effort:
        config["model_reasoning_effort"] = EFFORTS.get(spec.effort, spec.effort)
    if spec.system_prompt:
        config["developer_instructions"] = spec.system_prompt

    args = [settings.codex_binary, "exec"] + (["resume"] if resume else [])
    # Not the host user's settings, as with Claude Code.
    args += ["--json", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"]
    if spec.model:
        args += ["--model", spec.model]
    if schema_file:
        args += ["--output-schema", str(schema_file)]
    for key, value in config.items():
        args += ["-c", f"{key}={toml(value)}"]
    return args + ([resume] if resume else []) + ["-"]  # "-": the prompt comes on stdin


def agent_process_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(CODEX_HIDDEN_PREFIXES) and k not in HIDDEN_ENV}
    if settings.codex_api_key:  # otherwise the CLI uses its saved login
        env["CODEX_API_KEY"] = settings.codex_api_key
    return env


def shell_command(command: str) -> str:
    """`/bin/zsh -lc 'pytest -q'` → `pytest -q`: the wrapper is noise on the board."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    return parts[2] if len(parts) == 3 and parts[1] in ("-c", "-lc") else command


def tool_call(item: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """A Codex item as the tool call the board knows how to show, or None if it isn't one."""
    match item.get("type"):
        case "command_execution":
            return "Bash", {"command": shell_command(item.get("command") or "")}
        case "file_change":
            changes = item.get("changes") or []
            paths = ", ".join(str(c.get("path")) for c in changes)
            return "Edit", {"file_path": paths, "changes": changes}
        case "mcp_tool_call":
            return f"mcp__{item.get('server')}__{item.get('tool')}", item.get("arguments") or {}
        case "web_search":
            return "WebSearch", {"description": item.get("query") or ""}
        case "todo_list":
            todos = [
                {"content": t.get("text", ""), "status": "completed" if t.get("completed") else "pending"}
                for t in item.get("items") or []
            ]
            return "TodoWrite", {"todos": todos}
    return None


def tool_result(item: dict[str, Any]) -> dict[str, Any]:
    error = item.get("error") or {}
    failed = item.get("status") in ("failed", "declined") or item.get("exit_code") not in (None, 0)
    content = item.get("aggregated_output") or (error.get("message") if isinstance(error, dict) else str(error))
    return {"tool_use_id": item.get("id"), "is_error": failed, "content": _clip(content or "")}


async def run_codex(spec: TaskCreate, *, cwd: Path, resume: str | None, emit: Emit) -> AgentOutcome:
    error: str | None = None
    if reason := unsupported(spec):
        error = f"can't run on Codex: {reason}"
    elif shutil.which(settings.codex_binary) is None:
        error = f"Codex CLI `{settings.codex_binary}` not found (see HARNESS_CODEX_BINARY)"
    if error:
        return AgentOutcome(resume, None, True, error=error, failure=FailureReason.model_error)

    max_turns = spec.max_turns or settings.default_max_turns
    session_id, last_message, failed_message = resume, None, None
    usage: dict[str, Any] | None = None
    completed = turn_limit = False
    turns = 0
    seen: set[str] = set()

    # Outside the workspace, where the agent would commit them.
    with tempfile.TemporaryDirectory(prefix="harness-codex-") as tmp:
        schema_file = None
        if spec.output_schema:
            schema_file = Path(tmp, "schema.json")
            schema_file.write_text(json.dumps(strict_schema(spec.output_schema)))
        prompt_file, stderr_file = Path(tmp, "prompt.md"), Path(tmp, "stderr.log")
        prompt_file.write_text(spec.prompt)

        with prompt_file.open("rb") as stdin, stderr_file.open("wb") as stderr:
            proc = await asyncio.create_subprocess_exec(
                *build_args(spec, resume, schema_file),
                cwd=cwd,
                env=agent_process_env(),
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr,
                limit=MAX_LINE,
                start_new_session=True,  # its own process group: a kill takes the agent's commands too
            )
        try:
            assert proc.stdout is not None
            async for line in proc.stdout:
                event = json.loads(line)
                kind, item = event.get("type"), event.get("item") or {}
                if kind == "thread.started":
                    session_id = event.get("thread_id", session_id)
                    await emit("session", {"session_id": session_id, "model": spec.model, "resumed": bool(resume)})
                elif kind == "turn.completed":
                    completed, usage = True, event.get("usage")
                elif kind == "turn.failed":
                    failed_message = (event.get("error") or {}).get("message") or "the turn failed"
                elif kind == "error":
                    failed_message = event.get("message") or failed_message
                elif kind in ("item.started", "item.updated", "item.completed"):
                    done = kind == "item.completed"
                    if item.get("type") == "agent_message" and done:
                        last_message = item.get("text")
                        await emit("text", {"text": _clip(last_message)})
                    elif item.get("type") == "reasoning" and done and item.get("text"):
                        await emit("thinking", {"text": _clip(item["text"])})
                    elif call := tool_call(item):
                        name, input_ = call
                        if item["id"] not in seen:
                            seen.add(item["id"])
                            turns += 1
                            await emit("turn", {"turn": turns})
                        elif name != "TodoWrite":  # only a to-do list changes while it runs
                            input_ = None
                        if input_ is not None:
                            await emit("tool_use", {"id": item["id"], "name": name, "input": _clip(input_)})
                        if done:
                            await emit("tool_result", tool_result(item))
                        if turns > max_turns:
                            turn_limit = True
                            break
            if not turn_limit:
                await proc.wait()
        finally:
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
        stderr_tail = stderr_file.read_text(errors="replace")[-ERROR_TAIL:].strip()

    failure = None
    if turn_limit:
        error, failure = f"stopped after {turns} tool calls (limit {max_turns})", FailureReason.turn_limit
    elif failed_message or proc.returncode != 0 or not completed:
        error = failed_message or stderr_tail or f"codex exited with code {proc.returncode} without a result"
        failure = FailureReason.model_error

    structured = None
    if spec.output_schema and last_message and not error:
        try:
            structured = json.loads(last_message)
        except json.JSONDecodeError:
            pass  # like a Claude run with no structured answer: the step reads it as an error

    await emit(
        "result",
        _clip(
            {
                "subtype": "error" if error else "success",
                "is_error": bool(error),
                "result": last_message,
                "errors": [error] if error else [],
                "num_turns": turns,
                "session_id": session_id,
                "usage": usage,
            }
        ),
    )
    return AgentOutcome(
        session_id=session_id,
        result=last_message,
        is_error=bool(error),
        error=error,
        num_turns=turns,
        structured_output=structured,
        failure=failure,
    )
