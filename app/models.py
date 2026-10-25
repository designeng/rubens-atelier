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
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "TaskCreate":
        if not self.prompt.strip() and self.issue is None:
            raise ValueError("either prompt or issue is required")
        if self.repo_url and (self.issue or self.parent_task_id):
            raise ValueError("repo_url can't be combined with issue or parent_task_id")
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
    error: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    branch: str | None = None
    pr_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
