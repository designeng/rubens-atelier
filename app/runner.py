"""Runs one Claude Code session via the Claude Agent SDK and translates its
message stream into harness events."""

import dataclasses
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from .config import ROOT, settings
from .models import FailureReason, TaskCreate

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

MAX_EVENT_STR = 8_000

# `uv run --env-file .env` exports the harness's config (GitHub token, App key) and its own
# virtualenv into the worker, and Claude Code inherits the worker's environment.
HIDDEN_ENV_PREFIXES = ("HARNESS_",)
HIDDEN_ENV = ("GITHUB_TOKEN", "GH_TOKEN", "VIRTUAL_ENV")

# Replaces Claude Code's preset under `lean_context`; the task prompt carries the rest.
LEAN_SYSTEM_PROMPT = """\
You are a software engineer working autonomously in the git repository in the current directory.
Nobody answers questions during the run. Read files with Read, search with Glob and Grep, change
them with Edit and Write, and use Bash only to run commands (tests, linters, git). Bash runs in a
sandbox: writes outside the repository fail. Keep your messages short."""


@dataclass
class AgentOutcome:
    session_id: str | None
    result: str | None
    is_error: bool
    error: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    structured_output: Any = None
    failure: FailureReason | None = None  # set with is_error; None there means model_error


class AgentRunner(Protocol):
    async def __call__(
        self, spec: TaskCreate, *, cwd: Path, resume: str | None, emit: Emit
    ) -> AgentOutcome: ...


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_EVENT_STR:
        return value[:MAX_EVENT_STR] + f"… [{len(value) - MAX_EVENT_STR} chars truncated]"
    if isinstance(value, list):
        return [_clip(v) for v in value]
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    return value


def effective_tools(allowed: list[str]) -> list[str]:
    """Narrow a bare `Bash` to the configured command prefixes."""
    if "Bash" not in allowed or not settings.bash_allowed_commands:
        return allowed
    return [t for t in allowed if t != "Bash"] + [f"Bash({c}:*)" for c in settings.bash_allowed_commands]


def uv_cache_dir() -> Path:
    """Shared by all workspaces, outside them so it is never committed."""
    path = (settings.workspaces_root / ".cache" / "uv").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def agent_env() -> dict[str, str]:
    """The SDK merges `env` over the worker's environment and can't drop a variable, so the
    hidden ones are blanked. The uv cache moves to a directory the sandbox lets the agent write."""
    env = {k: "" for k in os.environ if k.startswith(HIDDEN_ENV_PREFIXES) or k in HIDDEN_ENV}
    env["UV_CACHE_DIR"] = str(uv_cache_dir())
    return env


def secret_read_rules() -> list[str]:
    """Deny rules for the harness's secret files. Workspaces may sit inside the harness checkout,
    and the sandbox only confines writes, so without these `cat ../../../.env` would work.
    Read deny rules cover the Read tool and, under the sandbox, Bash as well (only when they
    come from settings: `--disallowedTools` rules don't reach the sandbox)."""
    paths = [ROOT / ".env", Path(".env").resolve()]
    if settings.github_app_private_key_file:
        paths.append(settings.github_app_private_key_file.resolve())
    # `//` marks an absolute path in a permission rule.
    return [f"Read(/{p})" for p in dict.fromkeys(paths)]


def sandbox_settings() -> Any:
    """Bash sandbox config. `failIfUnavailable` makes a sandbox that can't start (no bubblewrap,
    user namespaces blocked) fail the command instead of silently running it unsandboxed; the
    SDK's `SandboxSettings` doesn't declare that key yet, hence `Any`."""
    return {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "failIfUnavailable": True,
    }


def tool_names(allowed: list[str]) -> list[str]:
    """Built-in tool names behind permission rules: `Bash(pytest:*)` → `Bash`."""
    return list(dict.fromkeys(t.split("(", 1)[0] for t in allowed))


def build_options(spec: TaskCreate, cwd: Path, resume: str | None) -> ClaudeAgentOptions:
    lean = spec.lean_context if spec.lean_context is not None else settings.lean_context
    allowed = effective_tools(spec.allowed_tools if spec.allowed_tools is not None else settings.default_allowed_tools)
    system_prompt: str | dict[str, Any]
    if lean:
        system_prompt = LEAN_SYSTEM_PROMPT + (f"\n\n{spec.system_prompt}" if spec.system_prompt else "")
    else:
        system_prompt = {"type": "preset", "preset": "claude_code"}
        if spec.system_prompt:
            system_prompt["append"] = spec.system_prompt
    return ClaudeAgentOptions(
        cwd=str(cwd),
        resume=resume,
        env=agent_env(),
        add_dirs=[uv_cache_dir()],
        system_prompt=system_prompt,
        # Lean: only the allowed tools' definitions reach the context, not every built-in one.
        tools=tool_names(allowed) if lean else None,
        # Load the repo's CLAUDE.md / .claude/settings.json, but not the host user's settings.
        # Lean loads nothing: the sandbox and deny rules come from `sandbox` and `settings` below.
        setting_sources=[] if lean else ["project"],
        model=spec.model or settings.default_model,
        effort=spec.effort,
        permission_mode=spec.permission_mode or settings.default_permission_mode,
        allowed_tools=allowed,
        sandbox=sandbox_settings() if settings.sandbox else None,
        disallowed_tools=spec.disallowed_tools or [],
        settings=json.dumps({"permissions": {"deny": secret_read_rules()}}),
        max_turns=spec.max_turns or settings.default_max_turns,
        max_budget_usd=spec.max_budget_usd or settings.default_max_budget_usd,
        output_format={"type": "json_schema", "schema": spec.output_schema} if spec.output_schema else None,
    )


def describe_error(result: ResultMessage, last_failure: str | None) -> str:
    """The SDK's own text is often only a diagnostic; add the last failed tool call, which is
    usually the real cause (e.g. a command the permission rules denied)."""
    base = ", ".join(map(str, result.errors or [])) or result.subtype
    parts = [base]
    if last_failure:
        parts.append(f"last failed tool call: {last_failure}")
    parts.append(f"after {result.num_turns} turns")
    return "; ".join(parts)


def failure_reason(result: ResultMessage) -> FailureReason:
    if result.subtype == "error_max_turns":
        return FailureReason.turn_limit
    if result.subtype.startswith("error_max_budget"):
        return FailureReason.budget
    return FailureReason.model_error


def _failure(call: dict[str, Any] | None, content: Any) -> str:
    text = content if isinstance(content, str) else str(content)
    what = call["name"] + " " + str(call["input"].get("command") or call["input"]) if call else "tool call"
    return f"{what[:300]} -> {text[:300]}"


async def run_claude(
    spec: TaskCreate, *, cwd: Path, resume: str | None, emit: Emit
) -> AgentOutcome:
    session_id: str | None = resume
    result: ResultMessage | None = None
    turns = 0
    calls: dict[str, dict[str, Any]] = {}
    last_failure: str | None = None

    async for msg in query(prompt=spec.prompt, options=build_options(spec, cwd, resume)):
        match msg:
            case SystemMessage(subtype="init", data=data):
                session_id = data.get("session_id", session_id)
                await emit("session", {"session_id": session_id, "model": data.get("model")})
            case AssistantMessage(content=blocks):
                turns += 1
                await emit("turn", {"turn": turns})
                for block in blocks:
                    match block:
                        case TextBlock(text=text):
                            await emit("text", {"text": _clip(text)})
                        case ThinkingBlock(thinking=thinking) if thinking:
                            await emit("thinking", {"text": _clip(thinking)})
                        case ToolUseBlock(id=id_, name=name, input=input_):
                            calls[id_] = {"name": name, "input": input_}
                            await emit("tool_use", {"id": id_, "name": name, "input": _clip(input_)})
            case UserMessage(content=list() as blocks):
                for block in blocks:
                    if isinstance(block, ToolResultBlock):
                        if block.is_error:
                            last_failure = _failure(calls.get(block.tool_use_id), block.content)
                        else:
                            last_failure = None
                        await emit(
                            "tool_result",
                            {
                                "tool_use_id": block.tool_use_id,
                                "is_error": bool(block.is_error),
                                "content": _clip(block.content),
                            },
                        )
            case RateLimitEvent(rate_limit_info=info):
                await emit("rate_limit", {k: v for k, v in dataclasses.asdict(info).items() if k != "raw"})
            case ResultMessage():
                result = msg
                session_id = msg.session_id or session_id
                await emit("result", _clip(dataclasses.asdict(msg)))
            case _:
                pass

    if result is None:
        return AgentOutcome(
            session_id, None, True, error="agent exited without a result", failure=FailureReason.model_error
        )
    return AgentOutcome(
        session_id=session_id,
        result=result.result,
        is_error=result.is_error,
        error=describe_error(result, last_failure) if result.is_error else None,
        cost_usd=result.total_cost_usd,
        num_turns=result.num_turns,
        structured_output=result.structured_output,
        failure=failure_reason(result) if result.is_error else None,
    )

