"""Tests for jarvis.core.task_execution_plan_view.format_execution_plan:
pure formatting of an ExecutionPlan, no derivation logic, no side
effects."""

from __future__ import annotations

from jarvis.core.task_execution_plan import ExecutionPlan, ExecutionStep
from jarvis.core.task_execution_plan_view import format_execution_plan
from jarvis.core.task_intent import TaskIntent


def _intent(**overrides) -> TaskIntent:
    defaults = dict(
        raw_text="write a README",
        action_category="file_change",
        mentions_external_service=False,
        external_service_name=None,
        mentions_shell_execution=False,
        is_supported=True,
    )
    defaults.update(overrides)
    return TaskIntent(**defaults)


def _step(**overrides) -> ExecutionStep:
    defaults = dict(
        description="Ask JARVIS to write the README",
        action_kind="repl_prompt",
        risk_level="reversible",
        requires_approval=True,
        blocked_reason=None,
    )
    defaults.update(overrides)
    return ExecutionStep(**defaults)


def _plan(**overrides) -> ExecutionPlan:
    defaults = dict(intent=_intent(), steps=[_step()], can_proceed=True, blocked_reason=None)
    defaults.update(overrides)
    return ExecutionPlan(**defaults)


def test_includes_raw_task_text():
    output = format_execution_plan(_plan(intent=_intent(raw_text="commit my changes")))
    assert "commit my changes" in output


def test_includes_category():
    output = format_execution_plan(_plan(intent=_intent(action_category="git_operation")))
    assert "git_operation" in output


def test_blocked_plan_shows_reason_and_no_steps_list_header():
    plan = _plan(
        intent=_intent(is_supported=False),
        steps=[_step(action_kind="blocked", blocked_reason="Not supported yet.")],
        can_proceed=False,
        blocked_reason="Not supported yet.",
    )
    output = format_execution_plan(plan)
    assert "cannot proceed" in output.lower()
    assert "Not supported yet." in output
    assert "proposed steps" not in output.lower()


def test_supported_plan_lists_steps():
    plan = _plan(steps=[_step(description="Step A"), _step(description="Step B")])
    output = format_execution_plan(plan)
    assert "Step A" in output
    assert "Step B" in output
    assert "proposed steps" in output.lower()


def test_step_shows_risk_level():
    plan = _plan(steps=[_step(risk_level="irreversible")])
    output = format_execution_plan(plan)
    assert "irreversible" in output.lower()


def test_step_shows_approval_requirement():
    plan = _plan(steps=[_step(requires_approval=True)])
    output = format_execution_plan(plan)
    assert "approval" in output.lower()


def test_blocked_plan_shows_reason_text():
    plan = _plan(
        can_proceed=False,
        blocked_reason="External integration not supported.",
        steps=[_step(action_kind="blocked", blocked_reason="External integration not supported.")],
    )
    output = format_execution_plan(plan)
    assert "External integration not supported." in output


def test_safety_disclaimer_present_for_supported_plan():
    output = format_execution_plan(_plan())
    assert "never writes files" in output.lower()
    assert "never runs git or shell" in output.lower()


def test_safety_disclaimer_present_for_blocked_plan():
    plan = _plan(can_proceed=False, blocked_reason="x", steps=[_step(action_kind="blocked", blocked_reason="x")])
    output = format_execution_plan(plan)
    assert "never executes anything" in output.lower()


def test_output_is_deterministic():
    plan = _plan()
    assert format_execution_plan(plan) == format_execution_plan(plan)
