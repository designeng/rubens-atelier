"""Picks the agent runtime from the model id, so wherever a model can be set (a story, a workflow
state, a task, HARNESS_DEFAULT_MODEL) a provider can be too:

    claude-sonnet-5-5                  Claude Code, with its own login
    openrouter/anthropic/claude-opus-5 Claude Code, talking to OpenRouter
    codex/gpt-5.5                      Codex CLI

A session belongs to its provider and can't be resumed on another, so the ids of the non-default
providers carry its name: `codex:<id>`. Claude's stay bare, which also keeps stored ones valid.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .codex import run_codex
from .config import settings
from .models import FailureReason, TaskCreate
from .runner import AgentOutcome, Emit, run_claude

log = logging.getLogger(__name__)

CLAUDE, OPENROUTER, CODEX = "claude", "openrouter", "codex"


@dataclass(frozen=True)
class Route:
    provider: str
    model: str  # as the provider knows it


def route(model: str | None) -> Route:
    name = model or settings.default_model
    prefix, _, rest = name.partition("/")
    return Route(prefix, rest) if rest and prefix in (OPENROUTER, CODEX) else Route(CLAUDE, name)


def reports_cost(model: str | None) -> bool:
    """Only Claude's runs are priced. Codex reports tokens, and Claude Code's estimate means
    nothing for what OpenRouter actually bills, so their budgets can't be enforced here."""
    return route(model).provider == CLAUDE


def resumable(session: str | None, model: str | None) -> str | None:
    """`session` if the model's provider can resume it."""
    if not session:
        return None
    provider, tagged, _ = session.partition(":")
    return session if (provider if tagged else CLAUDE) == route(model).provider else None


def openrouter_env(model: str) -> dict[str, str]:
    """A real Anthropic credential would be sent along and could bypass OpenRouter, hence the
    blanks. Claude Code's background and subagent requests must not ask for a model by a Claude
    name either: they stay on the chosen one."""
    tiers = ("FABLE", "OPUS", "SONNET", "HAIKU")
    return {
        "ANTHROPIC_BASE_URL": settings.openrouter_base_url,
        "ANTHROPIC_AUTH_TOKEN": settings.openrouter_api_key,
        "ANTHROPIC_API_KEY": "",
        "CLAUDE_CODE_OAUTH_TOKEN": "",
        "CLAUDE_CODE_SUBAGENT_MODEL": model,
        **{f"ANTHROPIC_DEFAULT_{tier}_MODEL": model for tier in tiers},
    }


async def run_routed(spec: TaskCreate, *, cwd: Path, resume: str | None, emit: Emit) -> AgentOutcome:
    target = route(spec.model)
    if resume and not resumable(resume, spec.model):
        log.warning("session %s can't be resumed on %s: starting a new one", resume, target.provider)
        resume = None
    tag = "" if target.provider == CLAUDE else f"{target.provider}:"

    async def tagged_emit(type_: str, data: dict[str, Any]) -> None:
        if type_ == "session" and data.get("session_id"):
            data = {**data, "session_id": tag + data["session_id"]}
        if type_ == "result" and target.provider != CLAUDE:
            data = {k: v for k, v in data.items() if k != "total_cost_usd"}
        await emit(type_, data)

    spec = spec.model_copy(update={"model": target.model})
    raw_resume = resume.removeprefix(tag) if resume else None
    if target.provider == CODEX:
        outcome = await run_codex(spec, cwd=cwd, resume=raw_resume, emit=tagged_emit)
    elif target.provider == OPENROUTER:
        if not settings.openrouter_api_key:
            error = f"model openrouter/{target.model} needs HARNESS_OPENROUTER_API_KEY"
            return AgentOutcome(resume, None, True, error=error, failure=FailureReason.model_error)
        env = openrouter_env(target.model)
        outcome = await run_claude(spec, cwd=cwd, resume=raw_resume, emit=tagged_emit, env=env)
        outcome.cost_usd = None
    else:
        outcome = await run_claude(spec, cwd=cwd, resume=raw_resume, emit=tagged_emit)
    if outcome.session_id:
        outcome.session_id = tag + outcome.session_id
    return outcome
