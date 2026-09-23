"""Tests for jarvis.core.task: the Task model is plain data - no side
effects are implied or triggered by constructing one."""

from __future__ import annotations

from jarvis.core.task import BLOCKED, PENDING_APPROVAL, VALID_STATUSES, Task
from jarvis.core.task_execution_plan import ExecutionPlan, ExecutionStep
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


def _plan(**overrides) -> ExecutionPlan:
    defaults = dict(
        intent=_intent(),
        steps=[
            ExecutionStep(
                description="do it",
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


def test_valid_statuses_are_blocked_and_pending_approval():
    assert set(VALID_STATUSES) == {BLOCKED, PENDING_APPROVAL}


def test_task_fields_are_accessible():
    task = _task()
    assert task.id == 1
    assert task.description == "write a README"
    assert task.status == PENDING_APPROVAL
    assert isinstance(task.plan, ExecutionPlan)
    assert task.risk_level == "reversible"
    assert task.requires_approval is True


def test_task_status_must_be_one_of_valid_statuses_by_convention():
    # Task itself doesn't validate (plain dataclass) - this documents the
    # invariant that every producer (TaskPlanner) is expected to uphold.
    task = _task(status=BLOCKED)
    assert task.status in VALID_STATUSES


def test_task_is_frozen():
    task = _task()
    try:
        task.status = BLOCKED  # type: ignore[misc]
    except Exception as e:
        assert isinstance(e, Exception)
    else:
        raise AssertionError("Task should be immutable (frozen dataclass)")


def test_two_tasks_with_same_fields_are_equal():
    plan = _plan()
    a = _task(plan=plan)
    b = _task(plan=plan)
    assert a == b


def test_two_tasks_with_different_ids_are_not_equal():
    plan = _plan()
    a = _task(id=1, plan=plan)
    b = _task(id=2, plan=plan)
    assert a != b


def test_constructing_a_task_has_no_side_effects(monkeypatch):
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("constructing a Task must never call subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    _task()
