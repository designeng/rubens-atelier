from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    # Durable factory state: projects, work items, runs, artifacts, human requests.
    database_url: str = "postgresql+asyncpg://harness:harness@localhost:5432/harness"
    workflows_dir: Path = ROOT / "workflows"
    prompts_dir: Path = ROOT / "prompts"
    # Agent/action steps with no progress for this long are re-enqueued by the reconciler.
    step_stale_seconds: int = 600
    # An agent step whose worker died mid-run continues its session in a new run this many
    # times per item state; one more crash ends the step with `restart_limit`.
    restart_limit: int = 3

    # Scheduler: how many agents run at once and how they pace subscription usage.
    # Celery worker concurrency must be at least max_agents (actions need a few more).
    max_agents: int = 4
    max_agents_per_project: int = 2
    # Utilization (0..1) of a subscription window at which dispatch drops to one agent / stops.
    quota_throttle_at: float = 0.8
    quota_pause_at: float = 0.95
    # Weekly windows may be used up to the elapsed share of the week plus this slack.
    quota_pace_slack: float = 0.1
    # A dispatched step that hasn't started its agent within this time gives its slot back.
    dispatch_ttl_seconds: int = 1800
    # How often check states (CI, merge) are polled when GitHub webhooks are off.
    checks_poll_seconds: int = 30
    # Secret of the GitHub webhook (POST /github/webhook). Set = webhooks on: events trigger
    # the checks and polling slows down to `checks_fallback_poll_seconds`. Empty = endpoint off.
    github_webhook_secret: str = ""
    # Polling with webhooks on: catches lost deliveries and time-based verdicts (no_checks, timeout).
    checks_fallback_poll_seconds: int = 300
    # Optional bearer token for the HTTP API. Empty = auth disabled (local dev only).
    api_token: str = ""

    # Board terminal: run shell commands in a story's workspace. Remote code execution, so it
    # is off by default and refused unless `api_token` is set.
    terminal_enabled: bool = False
    terminal_timeout_seconds: int = 120

    workspaces_root: Path = Path("./workspaces")
    # Hourly, remove the workspaces of finished stories and tasks (and of deleted ones) that are
    # older than this many hours. 0 = never; failed and cancelled ones are always left alone.
    workspace_ttl_hours: float = 0
    worker_concurrency: int = 2
    # Worker refreshes the lease every lease_ttl/3 seconds; a task whose lease
    # expired is considered orphaned (worker crashed) and gets marked failed.
    lease_ttl_seconds: int = 30
    task_timeout_seconds: int = 60 * 60
    # How long finished tasks and their event logs stay in Redis.
    result_ttl_seconds: int = 7 * 24 * 3600
    events_maxlen: int = 20_000

    # Defaults for agent runs; each can be overridden per task.
    default_model: str = "claude-sonnet-5-5"
    # Offered by the board's model picker on a story (JSON list); any model id is still accepted.
    models: list[str] = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
    default_permission_mode: str = "acceptEdits"
    default_allowed_tools: list[str] = ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
    # Bash is allowed only for commands starting with one of these prefixes (JSON list, e.g.
    # ["uv run", "pytest", "git status"]); anything else is denied. Empty = any command.
    bash_allowed_commands: list[str] = []
    # Run the agent's Bash in Claude Code's OS sandbox: writes confined to the workspace, no
    # unsandboxed escape hatch. Needs bubblewrap/socat on Linux. On by default: without it Bash
    # ignores the secret-file read rules and can read .env, /proc/1/environ and process argv.
    sandbox: bool = True
    default_max_turns: int = 50
    default_max_budget_usd: float = 5.0
    default_verify_attempts: int = 2
    verify_timeout_seconds: int = 30 * 60

    # GitHub issue workflow. The token needs contents, issues and pull-requests write
    # access; it is used only by the harness (clone/push/API), never exposed to the agent.
    github_token: str = ""
    github_api_url: str = "https://api.github.com"
    # GitHub App: per-project access to selected repositories via short-lived installation
    # tokens. The private key is a PEM (literal "\n" is accepted) or a path to one.
    # Projects without an installation fall back to `github_token`.
    github_app_id: str = ""
    github_app_slug: str = ""
    github_app_private_key: str = ""
    github_app_private_key_file: Path | None = None
    git_author_name: str = "claude-agent"
    git_author_email: str = "claude-agent@users.noreply.github.com"
    # Repos polled by celery beat for issues carrying `issue_label`. Empty = polling off.
    issue_repos: list[str] = []
    issue_label: str = "agent"
    issue_poll_seconds: int = 300
    # Default verify command for issue tasks when the request doesn't set one.
    issue_verify_command: str = ""


settings = Settings()
