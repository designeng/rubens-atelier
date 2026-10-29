from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class TaskStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (TaskStatus.succeeded, TaskStatus.failed, TaskStatus.cancelled)


class FailureReason(StrEnum):
    """Why an agent run ended without a result. A workflow's agent state can map each one in
    `next` (e.g. `turn_limit: implement`); unmapped ones fall back to `error`."""

    turn_limit = "turn_limit"  # the SDK stopped at max_turns
    budget = "budget"  # the SDK stopped at max_budget_usd
    model_error = "model_error"  # any other error the agent reported, or no result at all
    verify_failed = "verify_failed"  # verify_command still failed after the last attempt
    timeout = "timeout"
    crashed = "crashed"  # an exception in the harness around the agent
    workspace = "workspace"  # the workspace could not be prepared
    worker_lost = "worker_lost"  # the worker died mid-run
    restart_limit = "restart_limit"  # workers died mid-run more often than `restart_limit`
    cancelled = "cancelled"


PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class IssueRef(BaseModel):
    """Work a GitHub issue end to end: branch, implement, verify, push, open a PR."""

    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$", examples=["octocat/hello-world"])
    number: int = Field(ge=1)
    base_branch: str | None = None  # default: the repo's default branch
    open_pr: bool = True


class TaskCreate(BaseModel):
    # For issue tasks the prompt is optional extra guidance appended to the issue.
    prompt: str = ""
    issue: IssueRef | None = None
    # Clone this repo into the task workspace before running. Mutually exclusive with parent_task_id.
    repo_url: str | None = None
    repo_ref: str | None = None
    # Continue a previous task: reuse its workspace and resume its Claude session.
    parent_task_id: str | None = None
    # Run in a workspace the workflow engine already prepared (workspaces_root/items/<key>),
    # optionally resuming an earlier session there.
    workspace_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    resume_session: str | None = None

    system_prompt: str | None = None
    model: str | None = None
    effort: Effort | None = None
    permission_mode: PermissionMode | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    max_turns: int | None = Field(default=None, ge=1)
    max_budget_usd: float | None = Field(default=None, gt=0)
    timeout_seconds: int | None = Field(default=None, ge=1)
    # Shell command run in the workspace after the agent finishes (e.g. "make test").
    # On failure the agent is resumed with the output, up to verify_attempts times.
    verify_command: str | None = None
    verify_attempts: int | None = Field(default=None, ge=0, le=10)
    # JSON schema for the agent's final answer; the parsed object lands in `structured_output`.
    output_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "TaskCreate":
        if not self.prompt.strip() and self.issue is None:
            raise ValueError("either prompt or issue is required")
        if self.repo_url and (self.issue or self.parent_task_id):
            raise ValueError("repo_url can't be combined with issue or parent_task_id")
        if self.workspace_key and (self.repo_url or self.issue or self.parent_task_id):
            raise ValueError("workspace_key can't be combined with repo_url, issue or parent_task_id")
        return self


class TaskInfo(BaseModel):
    id: str
    status: TaskStatus
    prompt: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    workspace: str | None = None
    session_id: str | None = None
    parent_task_id: str | None = None
    result: str | None = None
    structured_output: Any = None
    error: str | None = None
    failure_reason: FailureReason | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    branch: str | None = None
    pr_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
