"""The Task model: the Task Execution layer's top-level, persistable-shaped
unit of work - a free-text request paired with the structured
ExecutionPlan (jarvis.core.task_execution_plan) derived for it, plus a
few summary fields (status, an overall risk level, whether any approval
is needed) that let a caller reason about a Task without re-inspecting
every individual ExecutionStep.

Like every other model in this layer (TaskIntent, ExecutionPlan), Task is
plain data. Constructing one never runs anything: no file writes, no git
mutations, no shell execution, no external-service calls, no LLM call.
v1 never reaches a "done"/"executing" status - see TaskStatus below -
because this stage of JARVIS never executes a Task itself; only a human,
acting through the existing approval-gated REPL/tools, can actually carry
out any step a Task's plan describes.
"""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.core.task_execution_plan import ExecutionPlan

# Fixed, deterministic status values. Checked in this fixed order - see
# TaskPlanner (jarvis.core.task_planner) for how a Task's status is
# derived from its ExecutionPlan. Kept as plain strings (not an enum) to
# match the rest of the codebase's dataclass style (ProjectPlan, WorkItem,
# ExecutionStep, ...).
#
#   BLOCKED           - the plan could not proceed (empty request, an
#                        external-integration or shell-execution mention,
#                        or an unrecognized request). Nothing further can
#                        be done with this Task as given.
#   PENDING_APPROVAL   - the plan has at least one concrete step, and
#                        every step in this stage requires human
#                        approval before anything runs - so a
#                        successfully-planned Task is always exactly
#                        this, never automatically "done".
#
# There is deliberately no "in_progress"/"done"/"failed" status yet: v1
# of the Task Execution layer never executes anything, so a Task can
# never actually move past PENDING_APPROVAL on its own. Adding those
# statuses is future work, gated on an actual execution stage existing.
BLOCKED = "blocked"
PENDING_APPROVAL = "pending_approval"

VALID_STATUSES = (BLOCKED, PENDING_APPROVAL)


@dataclass(frozen=True)
class Task:
    """A single unit of work: a user's request plus the plan derived for
    it. Every field is plain data - no side effects are implied or
    triggered by constructing one, and nothing about a Task ever executes
    itself.

    Fields:
      id                 - Sequential identifier, unique within whatever
                           produced this Task (see TaskPlanner). Not
                           persisted across process restarts in v1 - there
                           is no task store yet, mirroring how
                           jarvis.core.project_work's WorkItem has no
                           identity of its own either.
      description        - The original free-text request, verbatim
                           (same as plan.intent.raw_text).
      status             - One of VALID_STATUSES.
      plan               - The full ExecutionPlan (jarvis.core
                           .task_execution_plan) this Task was derived
                           from - every step, its risk level, and whether
                           it requires approval is available here.
      risk_level         - The single highest risk level (from
                           jarvis.core.task_safety: read_only <
                           reversible < irreversible) across every step in
                           `plan.steps`. A convenience summary field, not
                           independently computed - always consistent
                           with `plan`.
      requires_approval  - True if ANY step in `plan.steps` requires
                           approval. In this stage of JARVIS, this is
                           always True whenever status is PENDING_APPROVAL
                           (every proposed step requires approval) and
                           always False when status is BLOCKED (a blocked
                           Task has no actionable step to approve).
    """

    id: int
    description: str
    status: str
    plan: ExecutionPlan
    risk_level: str
    requires_approval: bool
