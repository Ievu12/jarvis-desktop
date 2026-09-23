"""Tests for jarvis.core.task_runner_view.format_task_run: pure
formatting of a TaskRun, no state-transition logic, no side effects."""

from __future__ import annotations

from jarvis.core.task import PENDING_APPROVAL, Task
from jarvis.core.task_execution_plan import ExecutionPlan, ExecutionStep
from jarvis.core.task_intent import TaskIntent
from jarvis.core.task_run_state import BLOCKED, COMPLETED, PENDING, RunStep, TaskRun
from jarvis.core.task_runner_view import format_task_run


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
        description="write the README",
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
        id=3,
        description="write a README",
        status=PENDING_APPROVAL,
        plan=_plan(),
        risk_level="reversible",
        requires_approval=True,
    )
    defaults.update(overrides)
    return Task(**defaults)


def test_includes_task_id_and_description():
    run = TaskRun(task=_task(id=5, description="write a README"))
    output = format_task_run(run)
    assert "5" in output
    assert "write a README" in output


def test_shows_step_status_and_description():
    run = TaskRun(task=_task())
    run.run_steps.append(RunStep(step=_step(description="write the README"), status=COMPLETED))
    output = format_task_run(run)
    assert "completed" in output.lower()
    assert "write the README" in output


def test_shows_result_summary_when_present():
    run = TaskRun(task=_task())
    run.run_steps.append(RunStep(step=_step(), status=COMPLETED, result_summary="[dry-run] Would perform: x"))
    output = format_task_run(run)
    assert "[dry-run] Would perform: x" in output


def test_shows_failure_reason_when_present():
    run = TaskRun(task=_task())
    run.run_steps.append(RunStep(step=_step(), status=BLOCKED, failure_reason="Denied by user."))
    output = format_task_run(run)
    assert "Denied by user." in output


def test_fully_completed_run_shows_success_message():
    run = TaskRun(task=_task())
    run.run_steps.append(RunStep(step=_step(), status=COMPLETED))
    output = format_task_run(run)
    assert "all steps completed" in output.lower()


def test_stopped_early_run_shows_stop_reason():
    run = TaskRun(task=_task(), stopped_early=True, stop_reason="Step denied by user: x")
    run.run_steps.append(RunStep(step=_step(), status=BLOCKED, failure_reason="Denied by user."))
    output = format_task_run(run)
    assert "stopped early" in output.lower()
    assert "Step denied by user: x" in output


def test_no_steps_run_shows_did_not_complete_message():
    run = TaskRun(task=_task())
    output = format_task_run(run)
    assert "did not complete" in output.lower()


def test_pending_step_shows_pending_label():
    run = TaskRun(task=_task())
    run.run_steps.append(RunStep(step=_step(), status=PENDING))
    output = format_task_run(run)
    assert "pending" in output.lower()


def test_output_is_deterministic():
    run = TaskRun(task=_task())
    run.run_steps.append(RunStep(step=_step(), status=COMPLETED))
    assert format_task_run(run) == format_task_run(run)
