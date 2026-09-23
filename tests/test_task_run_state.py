"""Tests for jarvis.core.task_run_state: plain runtime-state data
(RunStep, TaskRun) for the Task Runner. Constructing either never runs
anything."""

from __future__ import annotations

from jarvis.core.task import PENDING_APPROVAL, Task
from jarvis.core.task_execution_plan import ExecutionPlan, ExecutionStep
from jarvis.core.task_intent import TaskIntent
from jarvis.core.task_run_state import (
    APPROVED,
    BLOCKED,
    COMPLETED,
    FAILED,
    PENDING,
    RUNNING,
    TERMINAL_STEP_STATUSES,
    VALID_STEP_STATUSES,
    RunStep,
    TaskRun,
)


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
        description="do it",
        action_kind="repl_prompt",
        risk_level="reversible",
        requires_approval=True,
    )
    defaults.update(overrides)
    return ExecutionStep(**defaults)


def _plan(**overrides) -> ExecutionPlan:
    defaults = dict(intent=_intent(), steps=[_step()], can_proceed=True, blocked_reason=None)
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


# --- status constants -----------------------------------------------------


def test_valid_step_statuses_are_the_six_documented_values():
    assert set(VALID_STEP_STATUSES) == {PENDING, APPROVED, RUNNING, COMPLETED, FAILED, BLOCKED}


def test_terminal_statuses_are_completed_failed_blocked():
    assert set(TERMINAL_STEP_STATUSES) == {COMPLETED, FAILED, BLOCKED}


def test_pending_is_not_terminal():
    assert PENDING not in TERMINAL_STEP_STATUSES


def test_running_is_not_terminal():
    assert RUNNING not in TERMINAL_STEP_STATUSES


def test_approved_is_not_terminal():
    assert APPROVED not in TERMINAL_STEP_STATUSES


# --- RunStep -----------------------------------------------------


def test_runstep_defaults_to_pending():
    run_step = RunStep(step=_step())
    assert run_step.status == PENDING
    assert run_step.result_summary is None
    assert run_step.failure_reason is None


def test_runstep_is_mutable_unlike_executionstep():
    run_step = RunStep(step=_step())
    run_step.status = COMPLETED
    run_step.result_summary = "done"
    assert run_step.status == COMPLETED
    assert run_step.result_summary == "done"


def test_constructing_a_runstep_has_no_side_effects(monkeypatch):
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("constructing a RunStep must never call subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    RunStep(step=_step())


# --- TaskRun -----------------------------------------------------


def test_taskrun_defaults_to_empty_not_stopped():
    run = TaskRun(task=_task())
    assert run.run_steps == []
    assert run.stopped_early is False
    assert run.stop_reason is None


def test_is_fully_completed_false_when_no_steps_ran():
    run = TaskRun(task=_task())
    assert run.is_fully_completed is False


def test_is_fully_completed_true_when_every_step_completed():
    run = TaskRun(task=_task())
    run.run_steps.append(RunStep(step=_step(), status=COMPLETED))
    run.run_steps.append(RunStep(step=_step(), status=COMPLETED))
    assert run.is_fully_completed is True


def test_is_fully_completed_false_if_any_step_not_completed():
    run = TaskRun(task=_task())
    run.run_steps.append(RunStep(step=_step(), status=COMPLETED))
    run.run_steps.append(RunStep(step=_step(), status=FAILED))
    assert run.is_fully_completed is False


def test_is_fully_completed_false_when_stopped_early_even_if_steps_completed():
    run = TaskRun(task=_task())
    run.run_steps.append(RunStep(step=_step(), status=COMPLETED))
    run.stopped_early = True
    assert run.is_fully_completed is False


def test_constructing_a_taskrun_has_no_side_effects(monkeypatch):
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("constructing a TaskRun must never call subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    TaskRun(task=_task())
