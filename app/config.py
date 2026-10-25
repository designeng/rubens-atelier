from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    # Optional bearer token for the HTTP API. Empty = auth disabled (local dev only).
    api_token: str = ""

    workspaces_root: Path = Path("./workspaces")
    worker_concurrency: int = 2
    # Worker refreshes the lease every lease_ttl/3 seconds; a task whose lease
    # expired is considered orphaned (worker crashed) and gets marked failed.
    lease_ttl_seconds: int = 30
    task_timeout_seconds: int = 60 * 60
    # How long finished tasks and their event logs stay in Redis.
    result_ttl_seconds: int = 7 * 24 * 3600
    events_maxlen: int = 20_000

    # Defaults for agent runs; each can be overridden per task.
    default_model: str = "claude-opus-5-5"
    default_permission_mode: str = "acceptEdits"
    default_allowed_tools: list[str] = ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
    default_max_turns: int = 50
    default_max_budget_usd: float = 5.0
    default_verify_attempts: int = 2
    verify_timeout_seconds: int = 30 * 60

    # GitHub issue workflow. The token needs contents, issues and pull-requests write
    # access; it is used only by the harness (clone/push/API), never exposed to the agent.
    github_token: str = ""
    github_api_url: str = "https://api.github.com"
    git_author_name: str = "claude-agent"
    git_author_email: str = "claude-agent@users.noreply.github.com"
    # Repos polled by celery beat for issues carrying `issue_label`. Empty = polling off.
    issue_repos: list[str] = []
    issue_label: str = "agent"
    issue_poll_seconds: int = 300
    # Default verify command for issue tasks when the request doesn't set one.
    issue_verify_command: str = ""


settings = Settings()
