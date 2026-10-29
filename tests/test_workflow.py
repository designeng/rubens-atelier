import pytest
from pydantic import ValidationError

from app.config import settings
from app.workflow import Workflow, load_workflows, verdict_schema


def wf(**states):
    base = {"failed": {"terminal": "failed"}, "cancelled": {"terminal": "cancelled"}}
    return Workflow.model_validate({"name": "t", "initial": next(iter(states)), "states": states | base})


def test_story_workflow_loads_and_routes():
    story = load_workflows(settings.workflows_dir)["story"]
    assert story.initial == "plan"
    assert story.decide("plan", "ready") == "implement"
    assert story.decide("plan", "needs_clarification") == "clarify"
    assert story.decide("clarify", "answered") == "plan"
    assert story.decide("implement", "done") == "sync_base"
    assert story.decide("sync_base", "done") == "review"
    assert story.decide("sync_base", "conflict") == "implement"
    assert story.decide("awaiting_merge", "update") == "sync_base"
    assert story.decide("review", "changes_requested") == "implement"
    assert story.decide("review", "approved") == "publish"
    assert story.decide("awaiting_merge", "merged") == "done"
    assert story.cancelled_state == "cancelled"


def test_unmapped_verdicts_fall_back_to_error_then_failed():
    w = wf(a={"agent": "x", "verdicts": ["yes", "no"], "next": {"yes": "b", "error": "b"}}, b={"action": "open_pr"})
    assert w.decide("a", "no") == "b"
    assert w.decide("b", "error") == "failed"


@pytest.mark.parametrize(
    "states, message",
    [
        ({"a": {"agent": "x", "next": {"done": "nowhere"}}}, "unknown state"),
        ({"a": {"agent": "x", "next": {"approved": "failed"}}}, "not an outcome"),
        ({"a": {"human": "?", "options": ["y", "n"], "next": {"y": "failed"}}}, "without a transition"),
        ({"a": {"agent": "x", "action": "open_pr"}}, "exactly one"),
        ({"a": {"terminal": "succeeded", "next": {"done": "failed"}}}, "not an outcome"),
        ({"a": {"action": "open_pr", "next": {"turn_limit": "failed"}}}, "not an outcome"),
    ],
)
def test_invalid_workflows_are_rejected(states, message):
    with pytest.raises(ValidationError, match=message):
        wf(**states)


def test_agent_states_route_failure_reasons():
    w = wf(a={"agent": "x", "next": {"done": "b", "turn_limit": "a", "error": "b"}}, b={"action": "open_pr"})
    assert w.decide("a", "turn_limit") == "a"
    assert w.decide("a", "budget") == "b"  # unmapped: like error
    story = load_workflows(settings.workflows_dir)["story"]
    assert story.decide("implement", "turn_limit") == "implement"
    assert story.decide("implement", "restart_limit") == "needs_human"


def test_failed_state_is_required():
    with pytest.raises(ValidationError, match="failed"):
        Workflow.model_validate({"name": "t", "initial": "a", "states": {"a": {"agent": "x"}}})


def test_verdict_schema():
    schema = verdict_schema(["approved", "changes_requested"])
    assert schema["properties"]["verdict"]["enum"] == ["approved", "changes_requested"]
    assert schema["required"] == ["verdict", "summary"]
