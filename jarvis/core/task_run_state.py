"""Runtime state model for the Task Runner (jarvis.core.task_runner): a
RunStep wraps one ExecutionStep (jarvis.core.task_execution_plan) with
its live progress through a fixed state machine, and TaskRun collects the
full result of running every step of a Task (jarvis.core.task).

ExecutionStep itself is frozen/immutable (it's part of the read-only
planning layer) - RunStep is the separate, mutable runtime record the
Task Runner actually advances, so the planning layer's data never needs
to become mutable just to support execution tracking.

Plain data only: constructing a RunStep or TaskRun never runs anything
and never talks to the audit log itself - jarvis.core.task_runner owns
every state transition and every audit_log.log_event() call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.core.task import Task
from jarvis.core.task_execution_plan import ExecutionStep

# Fixed, deterministic status values for a single RunStep, in the order
# a step (that isn't blocked) is expected to move through them:
#
#   PENDING    -> (APPROVED if requires_approval, else straight to
#                 RUNNING) -> RUNNING -> COMPLETED or FAILED
#
# BLOCKED is a terminal state reachable from PENDING at any point: either
# the step was already blocked by the planning layer (action_kind ==
# "blocked"), or a human denied the approval prompt. A BLOCKED or FAILED
# step always stops the whole TaskRun - see task_runner.py - so no step
# after it is ever started.
PENDING = "pending"
APPROVED = "approved"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
BLOCKED = "blocked"

VALID_STEP_STATUSES = (PENDING, APPROVED, RUNNING, COMPLETED, FAILED, BLOCKED)

# Terminal statuses: once a RunStep reaches one of these, the Task Runner
# never transitions it again.
TERMINAL_STEP_STATUSES = (COMPLETED, FAILED, BLOCKED)


@dataclass
class RunStep:
    """One ExecutionStep's live progress through the Task Runner's state
    machine. Mutable (unlike ExecutionStep) - this is the record
    task_runner.py actually advances step by step. Constructing or
    mutating a RunStep directly has no side effect of its own; only
    TaskRunner.run() decides when and why a transition happens, and logs
    it."""

    step: ExecutionStep
    status: str = PENDING
    result_summary: str | None = None
    failure_reason: str | None = None


@dataclass
class TaskRun:
    """The full result of running a Task's plan through the Task Runner:
    one RunStep per ExecutionStep in `task.plan.steps`, plus whether the
    run completed every step or stopped early and why. Plain data - a
    TaskRun is the record of what happened, not something that causes
    anything by being constructed."""

    task: Task
    run_steps: list[RunStep] = field(default_factory=list)
    stopped_early: bool = False
    stop_reason: str | None = None

    @property
    def is_fully_completed(self) -> bool:
        """True only if every step reached COMPLETED - i.e. the run
        never stopped early and had at least one step to run."""
        return (
            not self.stopped_early
            and bool(self.run_steps)
            and all(rs.status == COMPLETED for rs in self.run_steps)
        )
