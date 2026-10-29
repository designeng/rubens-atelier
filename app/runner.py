"""Runs one Claude Code session via the Claude Agent SDK and translates its
message stream into harness events."""

import dataclasses
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

from .config import settings
from .models import TaskCreate

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

MAX_EVENT_STR = 8_000


@dataclass
class AgentOutcome:
    session_id: str | None
    result: str | None
    is_error: bool
    error: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    structured_output: Any = None


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


def build_options(spec: TaskCreate, cwd: Path, resume: str | None) -> ClaudeAgentOptions:
    system_prompt: dict[str, Any] = {"type": "preset", "preset": "claude_code"}
    if spec.system_prompt:
        system_prompt["append"] = spec.system_prompt
    return ClaudeAgentOptions(
        cwd=str(cwd),
        resume=resume,
        system_prompt=system_prompt,
        # Load the repo's CLAUDE.md / .claude/settings.json, but not the host user's settings.
        setting_sources=["project"],
        model=spec.model or settings.default_model,
        effort=spec.effort,
        permission_mode=spec.permission_mode or settings.default_permission_mode,
        allowed_tools=effective_tools(
            spec.allowed_tools if spec.allowed_tools is not None else settings.default_allowed_tools
        ),
        sandbox={"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False}
        if settings.sandbox
        else None,
        disallowed_tools=spec.disallowed_tools or [],
        max_turns=spec.max_turns or settings.default_max_turns,
        max_budget_usd=spec.max_budget_usd or settings.default_max_budget_usd,
        output_format={"type": "json_schema", "schema": spec.output_schema} if spec.output_schema else None,
    )


async def run_claude(
    spec: TaskCreate, *, cwd: Path, resume: str | None, emit: Emit
) -> AgentOutcome:
    session_id: str | None = resume
    result: ResultMessage | None = None
    turns = 0

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
                            await emit("tool_use", {"id": id_, "name": name, "input": _clip(input_)})
            case UserMessage(content=list() as blocks):
                for block in blocks:
                    if isinstance(block, ToolResultBlock):
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
        return AgentOutcome(session_id, None, True, error="agent exited without a result")
    return AgentOutcome(
        session_id=session_id,
        result=result.result,
        is_error=result.is_error,
        error=(", ".join(map(str, result.errors or [])) or result.subtype) if result.is_error else None,
        cost_usd=result.total_cost_usd,
        num_turns=result.num_turns,
        structured_output=result.structured_output,
    )

