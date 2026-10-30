from pathlib import Path

import pytest

from app.config import Settings

EMPTY_VARS = [
    "HARNESS_MAX_AGENTS",
    "HARNESS_QUOTA_THROTTLE_AT",
    "HARNESS_REDIS_URL",
    "HARNESS_WORKFLOWS_DIR",
]


def test_empty_env_vars_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in EMPTY_VARS:
        monkeypatch.setenv(name, "")

    settings = Settings(_env_file=None)

    defaults = Settings.model_construct()
    assert settings.max_agents == defaults.max_agents
    assert settings.quota_throttle_at == defaults.quota_throttle_at
    assert settings.redis_url == defaults.redis_url
    assert isinstance(settings.workflows_dir, Path)
    assert settings.workflows_dir == defaults.workflows_dir


def test_non_empty_env_vars_are_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_MAX_AGENTS", "7")

    assert Settings(_env_file=None).max_agents == 7
