"""TaskRunner: controlled, step-by-step progression through an already-
planned Task's ExecutionPlan (jarvis.core.task_execution_plan).

This is the Task Execution layer's *execution* stage - but v1 is
deliberately still side-effect-free in the real world. The only
"execution" a step can currently perform is through a pluggable
StepExecutor, and the only StepExecutor provided here (DryRunExecutor) is
a mock: it never runs a shell command, never writes a file, never
mutates git, and never calls an external service. It exists so the
Runner's state machine (pending -> approved -> running -> completed/
failed/blocked) and its approval gate can be fully exercised and tested
without granting any new real capability.

Safety properties, enforced in code here (not just documented):
  - Steps are processed strictly one at a time, in plan order.
  - A step whose ExecutionStep.action_kind == "blocked" (already decided
    unsafe/unsupported by the planning layer - external integration,
    shell execution, unknown request) is never run: it goes straight to
    BLOCKED and the whole run stops.
  - A step whose ExecutionStep.requires_approval is True is NEVER passed
    to the executor before an explicit approval decision is obtained.
    The default approval function is jarvis.core.approval
    .confirm_side_effect - the exact same human-in-the-loop gate every
    other real side effect in JARVIS (file writes, run_command, git
    commit) already goes through. A denial (or an approval function that
    returns False) blocks that step and stops the run - it never
    silently continues to the next step.
  - Every state transition for every step, and the run's overall
    completion/stop, is written to the audit log via
    jarvis.core.audit.log_event - the same append-only, redaction-aware
    log every other approval-gated action already uses.
  - No shell execution, no Instagram/Facebook/Gmail/Stripe/other external
    API is enabled by this module - DryRunExecutor is the only executor
    wired in, and it performs no real action of any kind.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from jarvis.core.approval import confirm_side_effect
from jarvis.core.audit import log_event
from jarvis.core.task import Task
from jarvis.core.task_execution_plan import ExecutionStep
from jarvis.core.task_run_state import (
    APPROVED,
    BLOCKED,
    COMPLETED,
    FAILED,
    PENDING,
    RUNNING,
    RunStep,
    TaskRun,
)

# Type alias for the pluggable approval callback: given the ExecutionStep
# about to run, return True to approve it, False to deny. The default
# (confirm_side_effect) prompts on stdin exactly like every other
# approval-gated tool; tests and any future non-interactive caller can
# supply their own.
ApproveFn = Callable[[ExecutionStep], bool]


@dataclass(frozen=True)
class StepExecutionResult:
    """What a StepExecutor reports after attempting one step. Plain data
    - ok=True/False plus a short human-readable summary. A StepExecutor
    implementation decides what "doing" a step means; DryRunExecutor
    below never performs any real action regardless of the step."""

    ok: bool
    summary: str


class StepExecutor:
    """Interface every step executor must implement. Not an ABC (kept
    lightweight, matching this codebase's preference for simple duck-typed
    collaborators elsewhere) - TaskRunner only ever calls `.execute(step)`.
    """

    def execute(self, step: ExecutionStep) -> StepExecutionResult:  # pragma: no cover - interface
        raise NotImplementedError


class DryRunExecutor(StepExecutor):
    """The only StepExecutor wired into TaskRunner by default. Never runs
    a shell command, never writes a file, never touches git, never calls
    an external service or the LLM - it only reports what *would* have
    been attempted. This is what keeps TaskRunner v1 a safe, fully
    testable core: swapping in a real executor is explicitly future work,
    gated on a separate, deliberate decision (see README's Task Runner
    section), not something this module does on its own.
    """

    def execute(self, step: ExecutionStep) -> StepExecutionResult:
        return StepExecutionResult(
            ok=True,
            summary=f"[dry-run] Would perform: {step.description}",
        )


def _default_approve_fn(step: ExecutionStep) -> bool:
    description = f"perform this step: {step.description}"
    return confirm_side_effect(description)


class TaskRunner:
    """Advances a Task's ExecutionPlan one step at a time under an
    approval gate. Construct one per run (mirrors TaskPlanner's
    per-instance state) with the executor and approval function to use -
    defaults are the safe, side-effect-free DryRunExecutor and the real
    confirm_side_effect() prompt, respectively.
    """

    def __init__(
        self,
        executor: StepExecutor | None = None,
        approve_fn: ApproveFn | None = None,
    ) -> None:
        self._executor = executor if executor is not None else DryRunExecutor()
        self._approve_fn = approve_fn if approve_fn is not None else _default_approve_fn

    def run(self, task: Task) -> TaskRun:
        """Run every step of `task.plan.steps` in order, stopping
        immediately at the first blocked, denied, or failed step. Never
        raises for an expected outcome (denial, failure, a pre-blocked
        step) - those are reflected in the returned TaskRun, not
        exceptions. A KeyboardInterrupt during an approval prompt is not
        caught here (matching every other approval-gated call in this
        codebase - see jarvis.core.approval) and propagates to the
        caller.
        """
        run = TaskRun(task=task)
        log_event("task_run_started", task_id=task.id, description=task.description, status=task.status)

        if task.status == "blocked" or not task.plan.can_proceed:
            # The planning layer already decided this Task cannot
            # proceed at all (empty/unsupported/external/shell request) -
            # there is nothing to run. Mirror that as a single BLOCKED
            # RunStep per planned step, without ever invoking the
            # approval function or the executor.
            for step in task.plan.steps:
                run_step = RunStep(step=step, status=BLOCKED, failure_reason=step.blocked_reason)
                run.run_steps.append(run_step)
                log_event(
                    "task_step_blocked",
                    task_id=task.id,
                    step_description=step.description,
                    reason=step.blocked_reason,
                )
            run.stopped_early = True
            run.stop_reason = task.plan.blocked_reason or "Task cannot proceed."
            log_event("task_run_stopped", task_id=task.id, reason=run.stop_reason)
            return run

        for step in task.plan.steps:
            run_step = RunStep(step=step, status=PENDING)
            run.run_steps.append(run_step)

            if step.action_kind == "blocked":
                run_step.status = BLOCKED
                run_step.failure_reason = step.blocked_reason
                log_event(
                    "task_step_blocked",
                    task_id=task.id,
                    step_description=step.description,
                    reason=step.blocked_reason,
                )
                run.stopped_early = True
                run.stop_reason = step.blocked_reason or "Step is blocked."
                break

            if step.requires_approval:
                approved = self._approve_fn(step)
                log_event(
                    "task_step_approval",
                    task_id=task.id,
                    step_description=step.description,
                    approved=approved,
                )
                if not approved:
                    run_step.status = BLOCKED
                    run_step.failure_reason = "Denied by user."
                    run.stopped_early = True
                    run.stop_reason = f"Step denied by user: {step.description}"
                    break
                run_step.status = APPROVED

            run_step.status = RUNNING
            log_event("task_step_running", task_id=task.id, step_description=step.description)

            result = self._executor.execute(step)

            if result.ok:
                run_step.status = COMPLETED
                run_step.result_summary = result.summary
                log_event(
                    "task_step_completed",
                    task_id=task.id,
                    step_description=step.description,
                    summary=result.summary,
                )
            else:
                run_step.status = FAILED
                run_step.failure_reason = result.summary
                log_event(
                    "task_step_failed",
                    task_id=task.id,
                    step_description=step.description,
                    reason=result.summary,
                )
                run.stopped_early = True
                run.stop_reason = f"Step failed: {step.description}"
                break

        log_event(
            "task_run_finished",
            task_id=task.id,
            stopped_early=run.stopped_early,
            stop_reason=run.stop_reason,
        )
        return run
