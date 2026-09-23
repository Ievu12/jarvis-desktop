"""Tests for jarvis.core.task_runner.TaskRunner: controlled, step-by-step
progression through a Task's ExecutionPlan under an approval gate, using
the safe DryRunExecutor. Covers every RunStep status (pending/approved/
running/completed/failed/blocked), the approval gate stopping the run,
and that every transition is audited."""

from __future__ import annotations

import json

import pytest

from jarvis.core import audit
from jarvis.core.task import BLOCKED as TASK_BLOCKED
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
)
from jarvis.core.task_runner import (
    DryRunExecutor,
    StepExecutionResult,
    StepExecutor,
    TaskRunner,
)


@pytest.fixture(autouse=True)
def isolated_audit(tmp_path, monkeypatch):
    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(audit, "AUDIT_LOG_FILE", log_path)
    return log_path


def _read_events(log_path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


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


def _plan(steps=None, **overrides) -> ExecutionPlan:
    defaults = dict(intent=_intent(), steps=steps if steps is not None else [_step()], can_proceed=True, blocked_reason=None)
    defaults.update(overrides)
    return ExecutionPlan(**defaults)


def _task(**overrides) -> Task:
    defaults = dict(
        id=7,
        description="write a README",
        status=PENDING_APPROVAL,
        plan=_plan(),
        risk_level="reversible",
        requires_approval=True,
    )
    defaults.update(overrides)
    return Task(**defaults)


def _always_approve(step: ExecutionStep) -> bool:
    return True


def _always_deny(step: ExecutionStep) -> bool:
    return False


# --- happy path: single approved step completes -----------------------------------------------------


def test_single_approved_step_completes(isolated_audit):
    task = _task(plan=_plan(steps=[_step(requires_approval=True)]))
    runner = TaskRunner(approve_fn=_always_approve)
    run = runner.run(task)

    assert len(run.run_steps) == 1
    assert run.run_steps[0].status == COMPLETED
    assert run.is_fully_completed is True
    assert run.stopped_early is False


def test_dry_run_executor_never_performs_a_real_action(monkeypatch):
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("DryRunExecutor must never call subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    result = DryRunExecutor().execute(_step())
    assert result.ok is True
    assert "dry-run" in result.summary.lower()


def test_step_not_requiring_approval_skips_straight_to_running(isolated_audit):
    task = _task(plan=_plan(steps=[_step(requires_approval=False)]))
    calls = []

    def _approve_fn(step):
        calls.append(step)
        return True

    runner = TaskRunner(approve_fn=_approve_fn)
    run = runner.run(task)

    assert calls == []  # approval function never invoked
    assert run.run_steps[0].status == COMPLETED


# --- approval gate: denial stops the run -----------------------------------------------------


def test_denied_approval_blocks_the_step_and_stops_the_run(isolated_audit):
    steps = [_step(description="step one", requires_approval=True), _step(description="step two")]
    task = _task(plan=_plan(steps=steps))
    runner = TaskRunner(approve_fn=_always_deny)
    run = runner.run(task)

    assert run.run_steps[0].status == BLOCKED
    assert run.run_steps[0].failure_reason == "Denied by user."
    assert len(run.run_steps) == 1  # step two never even started
    assert run.stopped_early is True
    assert "denied" in run.stop_reason.lower()


def test_no_side_effect_can_occur_without_approval(isolated_audit):
    executed: list[ExecutionStep] = []

    class _RecordingExecutor(StepExecutor):
        def execute(self, step):
            executed.append(step)
            return StepExecutionResult(ok=True, summary="ran")

    task = _task(plan=_plan(steps=[_step(requires_approval=True)]))
    runner = TaskRunner(executor=_RecordingExecutor(), approve_fn=_always_deny)
    runner.run(task)

    assert executed == []  # executor never called when approval is denied


# --- pre-blocked step (planning layer already decided) -----------------------------------------------------


def test_blocked_step_never_reaches_approval_or_executor(isolated_audit):
    calls = []

    def _approve_fn(step):
        calls.append(step)
        return True

    step = _step(action_kind="blocked", requires_approval=False, blocked_reason="not supported")
    task = _task(status=TASK_BLOCKED, plan=_plan(steps=[step], can_proceed=False, blocked_reason="not supported"))
    runner = TaskRunner(approve_fn=_approve_fn)
    run = runner.run(task)

    assert calls == []
    assert run.run_steps[0].status == BLOCKED
    assert run.run_steps[0].failure_reason == "not supported"
    assert run.stopped_early is True


def test_task_with_blocked_status_never_calls_executor(isolated_audit):
    executed = []

    class _RecordingExecutor(StepExecutor):
        def execute(self, step):
            executed.append(step)
            return StepExecutionResult(ok=True, summary="ran")

    step = _step(action_kind="blocked", requires_approval=False, blocked_reason="external service")
    task = _task(status=TASK_BLOCKED, plan=_plan(steps=[step], can_proceed=False, blocked_reason="external service"))
    runner = TaskRunner(executor=_RecordingExecutor())
    runner.run(task)

    assert executed == []


# --- failed step stops the run -----------------------------------------------------


def test_failed_step_stops_the_run(isolated_audit):
    class _FailingExecutor(StepExecutor):
        def execute(self, step):
            return StepExecutionResult(ok=False, summary="simulated failure")

    steps = [_step(description="step one", requires_approval=False), _step(description="step two")]
    task = _task(plan=_plan(steps=steps))
    runner = TaskRunner(executor=_FailingExecutor(), approve_fn=_always_approve)
    run = runner.run(task)

    assert run.run_steps[0].status == FAILED
    assert run.run_steps[0].failure_reason == "simulated failure"
    assert len(run.run_steps) == 1
    assert run.stopped_early is True


# --- multi-step happy path -----------------------------------------------------


def test_multiple_steps_all_approved_all_complete(isolated_audit):
    steps = [
        _step(description="step one", requires_approval=True),
        _step(description="step two", requires_approval=True),
        _step(description="step three", requires_approval=False),
    ]
    task = _task(plan=_plan(steps=steps))
    runner = TaskRunner(approve_fn=_always_approve)
    run = runner.run(task)

    assert len(run.run_steps) == 3
    assert all(rs.status == COMPLETED for rs in run.run_steps)
    assert run.is_fully_completed is True


def test_second_step_denied_stops_after_first_completes(isolated_audit):
    approvals = iter([True, False])
    steps = [_step(description="step one", requires_approval=True), _step(description="step two", requires_approval=True)]
    task = _task(plan=_plan(steps=steps))
    runner = TaskRunner(approve_fn=lambda step: next(approvals))
    run = runner.run(task)

    assert run.run_steps[0].status == COMPLETED
    assert run.run_steps[1].status == BLOCKED
    assert len(run.run_steps) == 2
    assert run.stopped_early is True


# --- audit logging -----------------------------------------------------


def test_run_start_and_finish_are_audited(isolated_audit):
    task = _task(plan=_plan(steps=[_step(requires_approval=False)]))
    TaskRunner().run(task)

    events = _read_events(isolated_audit)
    event_types = [e["event"] for e in events]
    assert "task_run_started" in event_types
    assert "task_run_finished" in event_types


def test_approval_decision_is_audited(isolated_audit):
    task = _task(plan=_plan(steps=[_step(requires_approval=True)]))
    TaskRunner(approve_fn=_always_approve).run(task)

    events = _read_events(isolated_audit)
    approval_events = [e for e in events if e["event"] == "task_step_approval"]
    assert len(approval_events) == 1
    assert approval_events[0]["approved"] is True


def test_denial_is_audited(isolated_audit):
    task = _task(plan=_plan(steps=[_step(requires_approval=True)]))
    TaskRunner(approve_fn=_always_deny).run(task)

    events = _read_events(isolated_audit)
    approval_events = [e for e in events if e["event"] == "task_step_approval"]
    assert approval_events[0]["approved"] is False


def test_step_completion_is_audited_with_summary(isolated_audit):
    task = _task(plan=_plan(steps=[_step(requires_approval=False)]))
    TaskRunner().run(task)

    events = _read_events(isolated_audit)
    completed_events = [e for e in events if e["event"] == "task_step_completed"]
    assert len(completed_events) == 1
    assert "dry-run" in completed_events[0]["summary"].lower()


def test_step_failure_is_audited(isolated_audit):
    class _FailingExecutor(StepExecutor):
        def execute(self, step):
            return StepExecutionResult(ok=False, summary="boom")

    task = _task(plan=_plan(steps=[_step(requires_approval=False)]))
    TaskRunner(executor=_FailingExecutor()).run(task)

    events = _read_events(isolated_audit)
    failed_events = [e for e in events if e["event"] == "task_step_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["reason"] == "boom"


def test_blocked_step_is_audited(isolated_audit):
    step = _step(action_kind="blocked", requires_approval=False, blocked_reason="not supported")
    task = _task(status=TASK_BLOCKED, plan=_plan(steps=[step], can_proceed=False, blocked_reason="not supported"))
    TaskRunner().run(task)

    events = _read_events(isolated_audit)
    blocked_events = [e for e in events if e["event"] == "task_step_blocked"]
    assert len(blocked_events) == 1
    assert blocked_events[0]["reason"] == "not supported"


def test_every_run_step_transition_references_the_task_id(isolated_audit):
    task = _task(id=99, plan=_plan(steps=[_step(requires_approval=False)]))
    TaskRunner().run(task)

    events = _read_events(isolated_audit)
    assert all(e.get("task_id") == 99 for e in events if "task_id" in e)


# --- no real side effects ever occur -----------------------------------------------------


def test_task_runner_default_executor_never_touches_subprocess(monkeypatch, isolated_audit):
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("TaskRunner with DryRunExecutor must never call subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    task = _task(plan=_plan(steps=[_step(requires_approval=False)]))
    TaskRunner().run(task)


def test_task_runner_never_writes_files(tmp_path, monkeypatch, isolated_audit):
    from pathlib import Path

    original_write_text = Path.write_text

    def _guarded_write_text(self, *a, **k):
        if self != isolated_audit:
            raise AssertionError(f"TaskRunner must never write to {self}")
        return original_write_text(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", _guarded_write_text)
    task = _task(plan=_plan(steps=[_step(requires_approval=False)]))
    TaskRunner().run(task)


# --- return type -----------------------------------------------------


def test_run_returns_taskrun_referencing_the_same_task(isolated_audit):
    task = _task(plan=_plan(steps=[_step(requires_approval=False)]))
    run = TaskRunner().run(task)
    assert run.task is task
