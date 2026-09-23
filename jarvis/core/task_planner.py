"""TaskPlanner: the Task Execution layer's single entry point for turning
a free-text request into a Task (jarvis.core.task).

This module wires together the already-existing, already-tested pipeline
rather than adding new reasoning of its own:

  parse_task_intent (jarvis.core.task_intent)     - classify the text
        -> scan_project (jarvis.core.project_scan) - read project state
        -> build_plan (jarvis.core.project_plan)    - reason about it
        -> build_execution_plan (jarvis.core.task_execution_plan)
              - derive the concrete, unexecuted, risk-labelled steps
        -> Task (jarvis.core.task) - the summarized, top-level result

TaskPlanner NEVER performs any work itself: no file writes, no git
mutations, no shell execution, no external-service calls, no LLM call.
It only classifies text and reasons about already-computed, read-only
project state - exactly like every module it calls. Actually carrying
out any step a planned Task describes still requires a human, acting
through the existing, separately-approval-gated REPL/tools.
"""

from __future__ import annotations

from jarvis.config import JARVIS_ROOT
from jarvis.core.project_plan import build_plan
from jarvis.core.project_scan import scan_project
from jarvis.core.task import BLOCKED, PENDING_APPROVAL, Task
from jarvis.core.task_execution_plan import ExecutionPlan, build_execution_plan
from jarvis.core.task_intent import parse_task_intent
from jarvis.core.task_safety import IRREVERSIBLE, READ_ONLY, REVERSIBLE

# Fixed severity order (lowest to highest) used to pick the single
# highest risk level across every step of a plan. Mirrors the ordering
# jarvis.core.task_safety already documents (read_only < reversible <
# irreversible) - kept here rather than in task_safety itself since this
# is specifically about summarizing a *list* of steps, not classifying a
# single piece of text.
_RISK_SEVERITY = {READ_ONLY: 0, REVERSIBLE: 1, IRREVERSIBLE: 2}


def _overall_risk_level(plan: ExecutionPlan) -> str:
    """The single highest risk level across every step in `plan.steps`.
    Defaults to IRREVERSIBLE for a plan with no steps at all (defensive -
    build_execution_plan always produces at least one step, even for a
    blocked plan), since an unknown/empty plan should never be summarized
    as safe."""
    if not plan.steps:  # pragma: no cover - defensive, build_execution_plan always yields >=1 step
        return IRREVERSIBLE
    return max(plan.steps, key=lambda step: _RISK_SEVERITY.get(step.risk_level, 2)).risk_level


def _derive_status_and_approval(plan: ExecutionPlan) -> tuple[str, bool]:
    """Derive (status, requires_approval) from an ExecutionPlan.
    Deterministic and total: every possible ExecutionPlan maps to exactly
    one of the two statuses defined in jarvis.core.task."""
    if not plan.can_proceed:
        return BLOCKED, False
    return PENDING_APPROVAL, any(step.requires_approval for step in plan.steps)


class TaskPlanner:
    """Builds a Task from a free-text request. Assigns sequential,
    in-memory-only ids starting at 1, scoped to this TaskPlanner instance
    - there is no cross-process task store yet (see jarvis.core.task's
    module docstring), mirroring how jarvis.core.project_work's WorkItem
    has no identity of its own either. A fresh TaskPlanner() (as CLI
    entry points create per invocation) always starts its own count at 1;
    a caller that wants ids that stay unique across many plan() calls in
    one process should keep and reuse a single TaskPlanner instance.
    """

    def __init__(self) -> None:
        self._next_id = 1

    def plan(self, description: str) -> Task:
        """Classify `description` and derive a full Task for it. Never
        touches the filesystem, git, network, or the LLM beyond the
        read-only project scan already performed by 'jarvis scan'/'plan'/
        'work'/'task'. Deterministic given the same description and
        project state - only the assigned id varies across calls on the
        same TaskPlanner instance.
        """
        intent = parse_task_intent(description)
        scan_result = scan_project(JARVIS_ROOT)
        project_plan = build_plan(scan_result)
        execution_plan = build_execution_plan(intent, scan_result, project_plan)

        status, requires_approval = _derive_status_and_approval(execution_plan)
        risk_level = _overall_risk_level(execution_plan)

        task = Task(
            id=self._next_id,
            description=intent.raw_text,
            status=status,
            plan=execution_plan,
            risk_level=risk_level,
            requires_approval=requires_approval,
        )
        self._next_id += 1
        return task
