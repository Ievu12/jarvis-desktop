"""Tests for jarvis.core.task_view.format_task: pure formatting of a
Task, no derivation logic, no side effects."""

from __future__ import annotations

from jarvis.core.task import BLOCKED, PENDING_APPROVAL, Task
from jarvis.core.task_execution_plan import ExecutionPlan, ExecutionStep
from jarvis.core.task_intent import TaskIntent
from jarvis.core.task_view import format_task


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


def _plan(**overrides) -> ExecutionPlan:
    defaults = dict(
        intent=_intent(),
        steps=[
            ExecutionStep(
                description="Ask JARVIS to write the README",
                action_kind="repl_prompt",
                risk_level="reversible",
                requires_approval=True,
            )
        ],
        can_proceed=True,
        blocked_reason=None,
    )
    defaults.update(overrides)
    return ExecutionPlan(**defaults)


def _task(**overrides) -> Task:
    defaults = dict(
        id=1,
        description="write a README",
        status=PENDING_APPROVAL,
        plan=_plan(),
        risk_level="reversible",
        requires_approval=True,
    )
    defaults.update(overrides)
    return Task(**defaults)


def test_includes_task_id():
    output = format_task(_task(id=42))
    assert "42" in output


def test_includes_status():
    output = format_task(_task(status=BLOCKED))
    assert BLOCKED in output


def test_includes_overall_risk_level():
    output = format_task(_task(risk_level="irreversible"))
    assert "irreversible" in output


def test_requires_approval_yes():
    output = format_task(_task(requires_approval=True))
    assert "requires approval: yes" in output.lower()


def test_requires_approval_no():
    output = format_task(_task(requires_approval=False))
    assert "requires approval: no" in output.lower()


def test_includes_underlying_plan_output():
    task = _task()
    output = format_task(task)
    # The task's raw description shows up via the embedded
    # format_execution_plan() output.
    assert "write a README" in output


def test_output_is_deterministic():
    task = _task()
    assert format_task(task) == format_task(task)
