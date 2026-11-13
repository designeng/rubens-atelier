"""Request and response models of the board API."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Kind = Literal["epic", "story", "task"]
# Board columns, derived from the kind of the item's current state.
Column = Literal["backlog", "working", "needs_human", "done", "failed"]

MAX_BLOCKERS = 20
MAX_PLAN_ITEMS = 100


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


ModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ProjectIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    base_branch: str | None = None
    verify_command: str | None = None
    installation_id: int | None = Field(default=None, description="GitHub App installation granting access to repo")
    auto_merge: bool = False
    budget_usd: float | None = Field(default=None, gt=0, description="Most the project's agent runs may cost")


class ProjectPatch(BaseModel):
    """Editable project settings; fields left out stay as they are, null clears them."""

    model_config = ConfigDict(extra="forbid")

    # Passed to git as a ref, so it must not start with "-" (would be read as an option).
    base_branch: str | None = Field(default=None, max_length=200, pattern=r"^[\w.][\w./-]*$")
    # Blank is rejected rather than stored: send null to clear.
    verify_command: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None
    auto_merge: bool = False  # left out = unchanged; null is rejected
    budget_usd: float | None = Field(default=None, gt=0)  # null removes the limit


class ProjectOut(Orm):
    id: str
    slug: str
    repo: str
    base_branch: str | None
    verify_command: str | None
    installation_id: int | None
    auto_merge: bool
    budget_usd: float | None
    spent_usd: float = 0.0


class ItemIn(BaseModel):
    kind: Kind = "story"
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    workflow: str = "story"
    parent_id: str | None = None
    issue: int | None = Field(default=None, ge=1, description="GitHub issue the PR should close")
    priority: int = Field(default=0, ge=-100, le=100, description="Higher is scheduled first")
    model: ModelName | None = None
    lean_context: bool | None = Field(default=None, description="null = the harness default")
    budget_usd: float | None = Field(default=None, gt=0, description="Most the item and its children may cost")
    blocked_by: list[str] = Field(
        default_factory=list, max_length=MAX_BLOCKERS, description="Items that must succeed first"
    )
    start: bool = Field(default=False, description="Start now; refused while a blocker has not succeeded")
    auto_start: bool = Field(default=False, description="Start by itself once every blocker has succeeded")


class EpicIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    priority: int = Field(default=0, ge=-100, le=100)
    model: ModelName | None = None
    lean_context: bool | None = None
    budget_usd: float | None = Field(default=None, gt=0, description="Most its stories may cost together")


class PlanItemIn(ItemIn):
    key: str = Field(
        pattern=r"^[\w.-]{1,64}$", description="The item's name within the plan, for blocked_by of other items"
    )
    blocked_by: list[str] = Field(
        default_factory=list,
        max_length=MAX_BLOCKERS,
        description="Keys of items of this plan, or ids of existing items, that must succeed first",
    )


class PlanIn(BaseModel):
    """An epic (optional) and its items, created at once: blocked_by may name items of the same plan by key."""

    epic: EpicIn | None = None
    items: list[PlanItemIn] = Field(min_length=1, max_length=MAX_PLAN_ITEMS)


class PlanOut(BaseModel):
    epic: "ItemOut | None"
    items: dict[str, "ItemOut"] = Field(description="By key")


class ItemPatch(BaseModel):
    """Editable item settings; applies from the next agent step, null goes back to the default.
    A step still waiting for a slot picks everything up when it starts; a new priority moves it
    in the queue right away."""

    model_config = ConfigDict(extra="forbid")

    model: ModelName | None = None
    lean_context: bool | None = None
    budget_usd: float | None = Field(default=None, gt=0)  # null removes the limit
    priority: int = Field(default=None, ge=-100, le=100)
    title: str = Field(default=None, min_length=1, max_length=300)
    body: str = Field(default=None)
    blocked_by: list[str] = Field(default=None, max_length=MAX_BLOCKERS)
    auto_start: bool = Field(default=None, description="Start by itself once every blocker has succeeded")


class ItemOut(Orm):
    id: str
    project_id: str
    parent_id: str | None
    kind: str
    title: str
    workflow: str
    state: str | None
    version: int
    branch: str | None
    pr_url: str | None
    pr_conflicts: bool | None = None
    priority: int
    model: str | None
    lean_context: bool | None = None
    budget_usd: float | None = None
    # What the item's runs and its descendants' runs have cost so far.
    spent_usd: float = 0.0
    blocked_by: list[str] = []
    auto_start: bool = False
    updated_at: datetime
    column: Column = "backlog"
    # GitHub issue the story came from or should close (item.source["issue"]).
    issue: int | None = None
    # Login of the issue's author, and whether the story is untrusted (written by someone who is not
    # OWNER/MEMBER/COLLABORATOR) and still awaits confirmation to start.
    author: str | None = None
    external: bool = False
    # Only for items in the working column: what is happening right now (see _progress).
    progress: dict[str, Any] | None = None
    # Only for items waiting for a human: why they are stuck (see _attention).
    attention: dict[str, str] | None = None
    # Only for backlog items: the blockers that have not succeeded yet, as "title (id)".
    blocked: list[str] = []
    # Only for backlog items: had blockers and all of them have succeeded, so it can be started.
    ready: bool = False
    # Ids of the backlog items that wait for this one.
    blocking: list[str] = []

    @field_validator("blocked_by", mode="before")
    @classmethod
    def _no_blockers(cls, v):
        return v or []  # rows from before the column existed hold NULL


class ImportResult(BaseModel):
    imported: list[ItemOut]
    skipped: int = Field(description="Open issues that already have an item, in any state")


class RunOut(Orm):
    id: str
    version: int
    state: str
    role: str
    task_id: str
    status: str
    verdict: str | None
    cost_usd: float | None
    model: str | None = None
    input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    context_tokens: int | None = None  # what the run's last request read
    failed_tools: int | None = None  # tool calls that failed: refused commands, errors
    created_at: datetime
    error: str | None = None  # the `error` artifact of a run that ended in the error verdict


class ArtifactOut(Orm):
    id: str
    run_id: str | None  # for a comment: the run it was delivered to
    name: str
    content: str
    created_at: datetime


class EventOut(Orm):
    from_state: str | None
    to_state: str
    verdict: str | None
    actor: str
    at: datetime


class RequestOut(Orm):
    id: str
    item_id: str
    state: str
    question: str
    options: list[str]
    answer: str | None
    created_at: datetime


class TransitionOut(BaseModel):
    id: int
    item_id: str
    title: str
    from_state: str | None
    to_state: str
    verdict: str | None
    actor: str
    at: datetime
    # What the new state means: "agent", "action", "check", "human" (needs a person), "terminal",
    # or "backlog" for a reopened item.
    kind: str
    outcome: str | None = None  # terminal states: succeeded | failed | cancelled


class TransitionsOut(BaseModel):
    cursor: int  # pass back as `after` to get the events that follow
    events: list[TransitionOut]


class ItemDetail(ItemOut):
    body: str
    runs: list[RunOut]
    artifacts: list[ArtifactOut]
    events: list[EventOut]
    open_request: RequestOut | None


class Answer(BaseModel):
    option: str
    comment: str = ""


class CommentIn(BaseModel):
    # Below steps.ARTIFACT_LIMIT, so a prompt never clips a comment.
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]
