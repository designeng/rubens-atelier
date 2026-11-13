import json
import os
import stat
from pathlib import Path

import pytest

from app import codex, providers
from app.codex import build_args, filesystem, run_codex, strict_schema, tool_call, unsupported
from app.config import settings
from app.models import FailureReason, TaskCreate
from app.providers import reports_cost, resumable, route, run_routed
from app.runner import AgentOutcome, build_options

THREAD = {"type": "thread.started", "thread_id": "t-1"}
DONE = {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}


def command_item(id_="c1", command="/bin/zsh -lc 'pytest -q'", **fields):
    return {"id": id_, "type": "command_execution", "command": command, **fields}


def message(text):
    return {"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": text}}


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    """Installs a `codex` that records how it was called and prints the given events."""

    def install(events, exit_code=0, stderr=""):
        script = tmp_path / "fake-codex"
        lines = "\n".join(json.dumps(e) for e in events)
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            f"call = dict(argv=sys.argv[1:], prompt=sys.stdin.read(), cwd=os.getcwd(), env=dict(os.environ))\n"
            f"json.dump(call, open({str(tmp_path / 'call.json')!r}, 'w'))\n"
            f"print({lines!r}, flush=True)\n"
            f"print({stderr!r}, file=sys.stderr)\n"
            f"sys.exit({exit_code})\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setattr(settings, "codex_binary", str(script))
        return tmp_path / "call.json"

    return install


@pytest.fixture
def events():
    return []


@pytest.fixture
def emit(events):
    async def _emit(type_, data):
        events.append((type_, data))

    return _emit


def test_model_prefix_picks_the_provider(monkeypatch):
    assert route("claude-opus-5-5") == providers.Route("claude", "claude-opus-5-5")
    assert route("codex/gpt-5.5") == providers.Route("codex", "gpt-5.5")
    assert route("openrouter/anthropic/claude-opus-5") == providers.Route("openrouter", "anthropic/claude-opus-5")
    assert route("unknown/model").provider == "claude"  # any other id goes to Claude Code, as before
    monkeypatch.setattr(settings, "default_model", "codex/gpt-5.5")
    assert route(None) == providers.Route("codex", "gpt-5.5")


def test_only_claude_reports_cost():
    assert reports_cost("claude-opus-5-5")
    assert not reports_cost("codex/gpt-5.5")
    assert not reports_cost("openrouter/openai/gpt-5.5")


def test_a_session_is_resumable_only_on_its_provider():
    assert resumable("uuid", "claude-opus-5-5") == "uuid"
    assert resumable("codex:t-1", "codex/gpt-5.5") == "codex:t-1"
    assert resumable("codex:t-1", "claude-opus-5-5") is None
    assert resumable("uuid", "codex/gpt-5.5") is None
    assert resumable("openrouter:uuid", "codex/gpt-5.5") is None
    assert resumable(None, "codex/gpt-5.5") is None


def test_strict_schema_requires_every_property_at_every_level():
    schema = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string"},
            "notes": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}}}},
        },
        "required": ["verdict"],
    }
    strict = strict_schema(schema)
    assert strict["required"] == ["verdict", "notes"] and strict["additionalProperties"] is False
    item = strict["properties"]["notes"]["items"]
    assert item["required"] == ["title"] and item["additionalProperties"] is False


def test_limits_codex_cant_honour_are_refused(monkeypatch):
    assert unsupported(TaskCreate(prompt="x")) is None
    assert unsupported(TaskCreate(prompt="x", disallowed_tools=["Write", "Edit"])) is None
    assert unsupported(TaskCreate(prompt="x", disallowed_tools=["Write", "Edit", "Bash"])) is None  # a planner
    assert "Bash" in unsupported(TaskCreate(prompt="x", disallowed_tools=["Bash"]))  # it could still write
    assert "allowed_tools" in unsupported(TaskCreate(prompt="x", allowed_tools=["Read"]))
    assert "WebFetch" in unsupported(TaskCreate(prompt="x", disallowed_tools=["WebFetch"]))
    monkeypatch.setattr(settings, "bash_allowed_commands", ["pytest"])
    assert "BASH_ALLOWED" in unsupported(TaskCreate(prompt="x"))


def test_permissions_deny_secrets_and_follow_the_role(monkeypatch):
    key = Path("/keys/app.pem")
    monkeypatch.setattr(settings, "github_app_private_key_file", key)
    monkeypatch.setenv("CODEX_HOME", "/codex-home")

    writer = filesystem(TaskCreate(prompt="x"))
    assert writer[":workspace_roots"] == {".": "write", ".git": "write"}
    assert writer[str(Path(".env").resolve())] == writer[str(key)] == "deny"
    assert writer["/codex-home/auth.json"] == "deny"

    reviewer = filesystem(TaskCreate(prompt="x", disallowed_tools=["Write", "Edit"]))
    assert ":workspace_roots" not in reviewer and reviewer["/"] == "read"
    assert reviewer[str(key)] == "deny"


def test_disabling_the_sandbox_keeps_read_only_roles_read_only(monkeypatch):
    monkeypatch.setattr(settings, "sandbox", False)
    assert 'sandbox_mode="danger-full-access"' in build_args(TaskCreate(prompt="x"), None, None)
    args = build_args(TaskCreate(prompt="x", permission_mode="plan"), None, None)
    assert 'default_permissions="harness"' in args and not any("danger-full-access" in a for a in args)


def test_codex_and_claude_share_one_domain_list(monkeypatch):
    from app.runner import sandbox_settings

    monkeypatch.setattr(settings, "allowed_domains", ["github.com", "*.pypi.org"])
    assert sandbox_settings()["network"] == {"allowedDomains": ["github.com", "*.pypi.org"]}
    args = build_args(TaskCreate(prompt="x"), None, None)
    assert 'permissions.harness.network.domains={"github.com"="allow","*.pypi.org"="allow"}' in args
    assert "permissions.harness.network.enabled=true" in args and "features.network_proxy=true" in args

    monkeypatch.setattr(settings, "allowed_domains", [])
    assert sandbox_settings()["network"] == {"allowedDomains": []}
    assert "permissions.harness.network.enabled=false" in build_args(TaskCreate(prompt="x"), None, None)


def test_args_for_resume_and_options():
    args = build_args(TaskCreate(prompt="x", model="gpt-5.5", effort="max", system_prompt='say "hi"'), "t-1", None)
    assert args[1:3] == ["exec", "resume"] and args[-2:] == ["t-1", "-"]
    assert args[args.index("--model") + 1] == "gpt-5.5"
    assert 'model_reasoning_effort="xhigh"' in args
    assert 'developer_instructions="say \\"hi\\""' in args
    assert 'project_doc_fallback_filenames=["CLAUDE.md"]' in args
    assert "project_doc_max_bytes=0" in build_args(TaskCreate(prompt="x", lean_context=True), None, None)


def test_items_become_the_tool_calls_the_board_shows():
    assert tool_call(command_item()) == ("Bash", {"command": "pytest -q"})
    name, args = tool_call({"id": "f", "type": "file_change", "changes": [{"path": "a.py", "kind": "update"}]})
    assert name == "Edit" and args["file_path"] == "a.py"
    todos = {"id": "t", "type": "todo_list", "items": [{"text": "a", "completed": True}, {"text": "b"}]}
    assert tool_call(todos)[1]["todos"] == [
        {"content": "a", "status": "completed"},
        {"content": "b", "status": "pending"},
    ]
    assert tool_call({"id": "r", "type": "reasoning", "text": "hm"}) is None


async def test_run_translates_events_and_hides_credentials(fake_codex, tmp_path, monkeypatch, emit, events):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("HARNESS_GITHUB_TOKEN", "github-secret")
    monkeypatch.setattr(settings, "codex_api_key", "codex-key")
    call = fake_codex(
        [
            THREAD,
            {"type": "item.completed", "item": {"id": "r", "type": "reasoning", "text": "thinking it over"}},
            {"type": "item.started", "item": command_item(status="in_progress")},
            {"type": "item.completed", "item": command_item(aggregated_output="1 failed", exit_code=1)},
            message("all done"),
            DONE,
        ]
    )
    outcome = await run_codex(TaskCreate(prompt="fix it", model="gpt-5.5"), cwd=tmp_path, resume=None, emit=emit)

    assert not outcome.is_error and outcome.result == "all done"
    assert outcome.session_id == "t-1" and outcome.num_turns == 1 and outcome.cost_usd is None
    assert [t for t, _ in events] == ["session", "thinking", "turn", "tool_use", "tool_result", "text", "result"]
    assert dict(events)["tool_use"]["input"] == {"command": "pytest -q"}
    assert dict(events)["tool_result"] == {"tool_use_id": "c1", "is_error": True, "content": "1 failed"}
    assert dict(events)["result"]["usage"]["input_tokens"] == 10

    seen = json.loads(call.read_text())
    assert seen["prompt"] == "fix it" and Path(seen["cwd"]).resolve() == tmp_path.resolve()
    assert seen["env"]["CODEX_API_KEY"] == "codex-key"
    assert "ANTHROPIC_API_KEY" not in seen["env"] and "HARNESS_GITHUB_TOKEN" not in seen["env"]


async def test_structured_answer_is_parsed_with_a_strict_schema(fake_codex, tmp_path, emit, monkeypatch):
    schemas = []
    real = codex.build_args

    def spying_args(spec, resume, schema_file):
        schemas.append(json.loads(schema_file.read_text()))
        return real(spec, resume, schema_file)

    monkeypatch.setattr(codex, "build_args", spying_args)
    fake_codex([THREAD, message('{"verdict": "approved", "summary": "ok"}'), DONE])
    spec = TaskCreate(
        prompt="x",
        output_schema={"type": "object", "properties": {"verdict": {"type": "string"}, "summary": {"type": "string"}}},
    )
    outcome = await run_codex(spec, cwd=tmp_path, resume=None, emit=emit)
    assert outcome.structured_output == {"verdict": "approved", "summary": "ok"}
    assert schemas[0]["required"] == ["verdict", "summary"]


@pytest.mark.parametrize(
    "stream,code,stderr,expected",
    [
        ([THREAD, {"type": "turn.failed", "error": {"message": "quota exceeded"}}], 1, "", "quota exceeded"),
        ([THREAD, {"type": "error", "message": "stream lost"}], 1, "", "stream lost"),
        ([THREAD], 2, "bad config", "bad config"),
        ([THREAD], 0, "", "without a result"),
    ],
)
async def test_failures_are_reported(fake_codex, tmp_path, emit, stream, code, stderr, expected):
    fake_codex(stream, exit_code=code, stderr=stderr)
    outcome = await run_codex(TaskCreate(prompt="x"), cwd=tmp_path, resume=None, emit=emit)
    assert outcome.is_error and expected in outcome.error
    assert outcome.failure == FailureReason.model_error and outcome.session_id == "t-1"


async def test_turn_limit_stops_the_run_and_keeps_the_session(fake_codex, tmp_path, emit):
    calls = [{"type": "item.completed", "item": command_item(f"c{i}", exit_code=0)} for i in range(5)]
    fake_codex([THREAD, *calls, DONE])
    outcome = await run_codex(TaskCreate(prompt="x", max_turns=2), cwd=tmp_path, resume=None, emit=emit)
    assert outcome.is_error and outcome.failure == FailureReason.turn_limit
    assert outcome.session_id == "t-1"


async def test_missing_cli_and_refused_limits_never_start_a_run(tmp_path, monkeypatch, emit):
    monkeypatch.setattr(settings, "codex_binary", "/no/such/codex")
    outcome = await run_codex(TaskCreate(prompt="x"), cwd=tmp_path, resume="t-1", emit=emit)
    assert outcome.is_error and "not found" in outcome.error and outcome.session_id == "t-1"
    outcome = await run_codex(TaskCreate(prompt="x", allowed_tools=["Read"]), cwd=tmp_path, resume=None, emit=emit)
    assert outcome.is_error and "per-tool" in outcome.error


async def test_routed_codex_tags_the_session_and_resumes_it_untagged(fake_codex, tmp_path, emit, events):
    call = fake_codex([THREAD, message("ok"), DONE])
    spec = TaskCreate(prompt="x", model="codex/gpt-5.5")
    outcome = await run_routed(spec, cwd=tmp_path, resume=None, emit=emit)
    assert outcome.session_id == "codex:t-1" and dict(events)["session"]["session_id"] == "codex:t-1"
    assert "gpt-5.5" in json.loads(call.read_text())["argv"]

    await run_routed(spec, cwd=tmp_path, resume="codex:t-1", emit=emit)
    argv = json.loads(call.read_text())["argv"]
    assert argv[:2] == ["exec", "resume"] and argv[-2:] == ["t-1", "-"]


async def test_routed_run_starts_afresh_on_another_providers_session(fake_codex, tmp_path, emit):
    call = fake_codex([THREAD, message("ok"), DONE])
    await run_routed(TaskCreate(prompt="x", model="codex/gpt-5.5"), cwd=tmp_path, resume="claude-uuid", emit=emit)
    assert json.loads(call.read_text())["argv"][1] != "resume"


async def test_openrouter_needs_its_key(tmp_path, monkeypatch, emit):
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    outcome = await run_routed(TaskCreate(prompt="x", model="openrouter/a/b"), cwd=tmp_path, resume=None, emit=emit)
    assert outcome.is_error and "HARNESS_OPENROUTER_API_KEY" in outcome.error


async def test_openrouter_runs_claude_code_pointed_at_it(tmp_path, monkeypatch, emit, events):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    seen = {}

    async def fake_claude(spec, *, cwd, resume, emit, env=None):
        seen.update(model=spec.model, resume=resume, env=env)
        await emit("session", {"session_id": "s-1"})
        await emit("result", {"total_cost_usd": 1.5, "result": "ok"})
        return AgentOutcome("s-1", "ok", False, cost_usd=1.5)

    monkeypatch.setattr(providers, "run_claude", fake_claude)
    spec = TaskCreate(prompt="x", model="openrouter/openai/gpt-5.5")
    outcome = await run_routed(spec, cwd=tmp_path, resume="openrouter:s-0", emit=emit)

    assert seen["model"] == "openai/gpt-5.5" and seen["resume"] == "s-0"
    assert seen["env"]["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert seen["env"]["ANTHROPIC_AUTH_TOKEN"] == "or-key"
    assert seen["env"]["ANTHROPIC_API_KEY"] == "" and seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert seen["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "openai/gpt-5.5"
    assert outcome.session_id == "openrouter:s-1" and outcome.cost_usd is None
    assert "total_cost_usd" not in dict(events)["result"]  # Claude Code's estimate of someone else's bill


def test_extra_env_goes_over_the_agents_env(monkeypatch):
    monkeypatch.setitem(os.environ, "HARNESS_X", "1")
    opts = build_options(TaskCreate(prompt="x"), Path("."), None, {"ANTHROPIC_API_KEY": ""})
    assert opts.env["ANTHROPIC_API_KEY"] == "" and opts.env["HARNESS_X"] == ""


def test_codex_usage_splits_cached_input_off():
    from app.codex import codex_usage

    assert codex_usage(None) is None
    usage = {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50}
    assert codex_usage(usage) == {
        "input_tokens": 200, "cache_read_tokens": 800, "cache_write_tokens": 0, "output_tokens": 50
    }
