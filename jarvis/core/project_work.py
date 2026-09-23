"""Deterministic "next step" derivation from a ProjectPlan
(jarvis.core.project_plan): identifies the single next actionable item
from plan.suggested_order and produces a concrete, safe way to actually
do it - either the exact command to run, or a ready-to-use prompt to hand
to JARVIS's LLM-driven agent (which remains approval-gated as normal).

This module NEVER performs the work itself: no file writes, no git
mutations, no LLM calls. v1 is deliberately execution-free - it only
identifies and clearly describes the next step, so there is no risk of
jarvis work making an uncontrolled change to the user's project. Actually
doing the work still goes through the normal, already-tested,
approval-gated jarvis REPL (write_file, run_command, etc.), the same as
any other project change in this codebase.

Reuses jarvis.core.project_plan.build_plan's output entirely - this
module does not re-scan or re-plan; it only reasons about the
already-computed ProjectPlan passed in, keeping scan/plan/work modular
(scan owns detection, plan owns recommendations, work owns turning the
next recommendation into a concrete, safe action description).
"""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.core.project_plan import ProjectPlan

# A step is "safe" for work v1 to prescribe a raw command for only when
# doing so takes no judgment or generated content - just running a fixed,
# argument-free command. Every other step needs judgment (what to write in
# a README, what tests to add, etc.) and gets a REPL-prompt suggestion
# instead, keeping the LLM (and its existing approval gate) in the loop
# for anything that isn't purely mechanical.
_GIT_INIT_PREFIX = "Initialize a git repository"
_INITIAL_COMMIT_PREFIX = "Make an initial commit"


@dataclass
class WorkItem:
    """A single, concrete description of the next actionable step. Plain
    data - no side effects are implied or triggered by constructing one."""

    has_plan: bool
    is_complete: bool
    step_description: str | None
    action_kind: str  # "command", "repl_prompt", "none", or "no_plan"
    action_detail: str | None
    remaining_step_count: int


def _classify_step(step: str) -> tuple[str, str]:
    """Return (action_kind, action_detail) for a single recommended-step
    description. action_kind is 'command' for steps with one fixed, safe,
    no-judgment command; 'repl_prompt' for anything needing judgment or
    generated content."""
    if step.startswith(_GIT_INIT_PREFIX):
        return "command", "git init"

    if step.startswith(_INITIAL_COMMIT_PREFIX):
        return (
            "repl_prompt",
            "Ask JARVIS: \"stage and commit the current changes with an appropriate message\"",
        )

    if step.lower().startswith("add a readme"):
        return (
            "repl_prompt",
            "Ask JARVIS: \"write a README.md describing what this project is and how to use it\"",
        )

    if step.lower().startswith("add a dependency"):
        return (
            "repl_prompt",
            "Ask JARVIS: \"look at the project's source files and create an appropriate "
            "dependency/package manifest\"",
        )

    if step.lower().startswith("add a test suite"):
        return (
            "repl_prompt",
            "Ask JARVIS: \"add a first test for this project's main functionality\"",
        )

    if step.lower().startswith("create a jarvis.md"):
        return "repl_prompt", "Run 'scan' inside the JARVIS REPL, then review and save the proposed draft."

    if step.lower().startswith("review the uncommitted changes"):
        return "repl_prompt", "Run 'review' or 'precommit' inside the JARVIS REPL."

    # Any future/unrecognized recommended-step wording still gets a safe,
    # generic fallback rather than crashing or guessing at a command.
    return "repl_prompt", f"Ask JARVIS to help with: {step}"


def build_work_item(plan: ProjectPlan) -> WorkItem:
    """Derive the single next actionable step from an already-computed
    ProjectPlan. Never performs any work itself - purely identifies and
    describes. Deterministic: the same plan always produces the same
    WorkItem.
    """
    if plan is None:  # pragma: no cover - defensive, callers always pass a real plan
        return WorkItem(
            has_plan=False,
            is_complete=False,
            step_description=None,
            action_kind="no_plan",
            action_detail=None,
            remaining_step_count=0,
        )

    steps = plan.suggested_order
    if not steps:
        # An empty suggested_order (as opposed to the "no gaps detected"
        # sentinel message build_plan() always includes) means there is
        # genuinely no plan data to work from.
        return WorkItem(
            has_plan=False,
            is_complete=False,
            step_description=None,
            action_kind="no_plan",
            action_detail=None,
            remaining_step_count=0,
        )

    first_step = steps[0]
    if first_step.lower().startswith("no gaps detected"):
        return WorkItem(
            has_plan=True,
            is_complete=True,
            step_description=None,
            action_kind="none",
            action_detail=None,
            remaining_step_count=0,
        )

    action_kind, action_detail = _classify_step(first_step)
    return WorkItem(
        has_plan=True,
        is_complete=False,
        step_description=first_step,
        action_kind=action_kind,
        action_detail=action_detail,
        remaining_step_count=len(steps),
    )
