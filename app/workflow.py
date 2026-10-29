"""Declarative workflows: deterministic state machines whose transitions are agent tasks.

The agent never picks the next step. An agent state returns a verdict (a value of an
enum enforced through structured output, or `done`), an action returns `done`/`error`,
a human state returns the chosen option; `next` maps that verdict to the next state.
An agent run that ends without a result gives its failure reason instead (`turn_limit`,
`budget`, `verify_failed`, `restart_limit`, ... see `models.FailureReason`).
Anything unmapped — including `error` — falls back to `next.error`, then to `failed`.

Every state is exactly one of:
  agent:    run a role's prompt in the item's workspace (optionally followed by verify)
  action:   a built-in harness step, e.g. open_pr
  check:    poll an external condition, e.g. ci (GitHub checks on the PR head)
  human:    wait for a person to pick one of `options`; an optional `watch` check polls
            meanwhile and answers on their behalf once it knows the outcome (e.g. merge)
  terminal: the item is finished with the given outcome
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from .models import FailureReason

Outcome = Literal["succeeded", "failed", "cancelled"]
ERROR = "error"
DONE = "done"
KINDS = ("agent", "action", "check", "human", "terminal")
CHECK_OUTCOMES = ["passed", "failed", "no_checks", "timeout"]
FAILURE_REASONS = [r.value for r in FailureReason]


class State(BaseModel):
    agent: str | None = None  # role name → prompts/<role>.md
    action: str | None = None
    check: str | None = None
    human: str | None = None  # the question
    watch: str | None = None  # human: a check polled while waiting; its verdict must be an option
    terminal: Outcome | None = None

    verdicts: list[str] = []  # agent/action: allowed verdicts; empty = success is `done`
    options: list[str] = []  # human: allowed answers
    verify: bool = False  # agent: run the project's verify command, feeding failures back
    resume: bool = False  # agent: continue this role's previous session on the item
    model: str | None = None
    effort: str | None = None
    disallowed_tools: list[str] = []
    # check: give up with `timeout` after this long; `no_checks` if nothing reported within grace.
    timeout_seconds: int = Field(default=3600, ge=1)
    grace_seconds: int = Field(default=300, ge=0)

    next: dict[str, str] = {}
    # Loop guard: entering this state more often sends the item to `on_exhausted`.
    max_visits: int | None = Field(default=None, ge=1)
    on_exhausted: str = "failed"

    @property
    def kind(self) -> str:
        return next(k for k in KINDS if getattr(self, k))

    @property
    def runs_on_worker(self) -> bool:
        return self.kind in ("agent", "action")

    def outcomes(self) -> list[str]:
        match self.kind:
            case "agent":
                return (self.verdicts or [DONE]) + FAILURE_REASONS + [ERROR]
            case "action":
                return (self.verdicts or [DONE]) + [ERROR]
            case "check":
                return CHECK_OUTCOMES + [ERROR]
            case "human":
                return self.options
        return []

    @model_validator(mode="after")
    def _check(self) -> "State":
        kinds = [k for k in KINDS if getattr(self, k)]
        if len(kinds) != 1:
            raise ValueError(f"a state must be exactly one of {'/'.join(KINDS)}, got {kinds}")
        if self.watch and not self.human:
            raise ValueError("`watch` only applies to human states")
        if self.human and not self.options:
            raise ValueError("a human state needs options")
        return self


class Workflow(BaseModel):
    name: str
    description: str = ""
    initial: str
    states: dict[str, State]

    @model_validator(mode="after")
    def _check(self) -> "Workflow":
        names = set(self.states)
        if self.initial not in names:
            raise ValueError(f"initial state {self.initial!r} is not defined")
        if "failed" not in names or self.states["failed"].terminal != "failed":
            raise ValueError("a workflow needs a terminal `failed` state (the fallback)")
        if not any(s.terminal == "cancelled" for s in self.states.values()):
            raise ValueError("a workflow needs a terminal state with outcome `cancelled`")
        for name, st in self.states.items():
            for verdict, target in st.next.items():
                if target not in names:
                    raise ValueError(f"{name}: {verdict} → unknown state {target!r}")
                if verdict not in st.outcomes():
                    raise ValueError(f"{name}: {verdict!r} is not an outcome of this state")
            if st.on_exhausted not in names:
                raise ValueError(f"{name}: on_exhausted → unknown state {st.on_exhausted!r}")
            if st.human and (missing := set(st.options) - set(st.next)):
                raise ValueError(f"{name}: options without a transition: {sorted(missing)}")
            if st.terminal and st.next:
                raise ValueError(f"{name}: terminal states have no transitions")
        return self

    def decide(self, state: str, verdict: str) -> str:
        st = self.states[state]
        return st.next.get(verdict) or st.next.get(ERROR) or "failed"

    @property
    def cancelled_state(self) -> str:
        return next(n for n, s in self.states.items() if s.terminal == "cancelled")


def verdict_schema(verdicts: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": verdicts},
            "summary": {"type": "string", "description": "Markdown: what you found or did and why."},
        },
        "required": ["verdict", "summary"],
        "additionalProperties": False,
    }


def load_workflows(directory: Path) -> dict[str, Workflow]:
    workflows = {}
    for path in sorted(directory.glob("*.yaml")):
        wf = Workflow.model_validate(yaml.safe_load(path.read_text()))
        workflows[wf.name] = wf
    return workflows
