"""Turns a TaskIntent (jarvis.core.task_intent) plus the project's
already-computed scan/plan state (jarvis.core.project_scan /
jarvis.core.project_plan) into a structured ExecutionPlan: an ordered list
of concrete-but-unexecuted steps, each labelled with a risk level and
whether it is blocked.

This is the Task Execution layer's planning stage only. Like
jarvis.core.project_work, this module NEVER performs any work itself: no
file writes, no git mutations, no shell execution, no LLM calls. It only
reasons about text (the intent) and already-computed, read-only project
state (the scan/plan) to describe what *would* need to happen and how
safe each part is - actually doing anything still requires a human to
act through the existing, separately-approval-gated REPL/tools
(write_file, run_command, etc.), exactly as jarvis work already
documents for its own single-step suggestions.

Reuses jarvis.core.project_scan.scan_project's and
jarvis.core.project_plan.build_plan's output entirely - this module does
not re-scan, re-plan, or duplicate any git/filesystem detection logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.core.project_plan import ProjectPlan
from jarvis.core.project_scan import ProjectScanResult
from jarvis.core.task_intent import (
    EXTERNAL_INTEGRATION,
    FILE_CHANGE,
    GIT_OPERATION,
    SHELL_EXECUTION,
    TEST_RUN,
    UNKNOWN,
    TaskIntent,
)
from jarvis.core.task_safety import IRREVERSIBLE, READ_ONLY, REVERSIBLE, classify_risk


@dataclass(frozen=True)
class ExecutionStep:
    """A single, concrete-but-unexecuted description of part of the work
    needed to satisfy a task request. Plain data - no side effects are
    implied or triggered by constructing one."""

    description: str
    action_kind: str  # "repl_prompt", "command_suggestion", or "blocked"
    risk_level: str  # "read_only", "reversible", or "irreversible"
    requires_approval: bool
    blocked_reason: str | None = None


@dataclass
class ExecutionPlan:
    """Structured, unexecuted plan for a free-text task request. Every
    field is plain data - constructing one never runs anything."""

    intent: TaskIntent
    steps: list[ExecutionStep] = field(default_factory=list)
    can_proceed: bool = False
    blocked_reason: str | None = None


def _blocked_plan(intent: TaskIntent, reason: str) -> ExecutionPlan:
    return ExecutionPlan(
        intent=intent,
        steps=[
            ExecutionStep(
                description=intent.raw_text,
                action_kind="blocked",
                risk_level=IRREVERSIBLE,
                requires_approval=False,
                blocked_reason=reason,
            )
        ],
        can_proceed=False,
        blocked_reason=reason,
    )


def _git_operation_steps(scan: ProjectScanResult) -> list[ExecutionStep]:
    steps: list[ExecutionStep] = []
    git = scan.git

    if not git.is_repo:
        steps.append(
            ExecutionStep(
                description="Initialize a git repository first (git-init).",
                action_kind="command_suggestion",
                risk_level=REVERSIBLE,
                requires_approval=True,
            )
        )

    steps.append(
        ExecutionStep(
            description=(
                "Ask JARVIS in the REPL to perform the requested git operation "
                "(e.g. stage and commit) - it will use the approval-gated "
                "run_command('git-add'/'git-commit') path, never an automatic commit."
            ),
            action_kind="repl_prompt",
            risk_level=REVERSIBLE,
            requires_approval=True,
        )
    )
    return steps


def _test_run_steps() -> list[ExecutionStep]:
    return [
        ExecutionStep(
            description=(
                "Ask JARVIS in the REPL to run the test suite "
                "(run_command('pytest')) or add the requested test."
            ),
            action_kind="repl_prompt",
            risk_level=READ_ONLY,
            requires_approval=True,
        )
    ]


def _file_change_steps(intent: TaskIntent) -> list[ExecutionStep]:
    risk = classify_risk(intent.raw_text)
    return [
        ExecutionStep(
            description=(
                f"Ask JARVIS in the REPL to make this change: \"{intent.raw_text}\" - it "
                "will use the approval-gated file tools (write_file/edit_file_lines/"
                "delete_file/etc.), never an automatic, unapproved write."
            ),
            action_kind="repl_prompt",
            risk_level=risk.risk_level,
            requires_approval=True,
        )
    ]


def build_execution_plan(intent: TaskIntent, scan: ProjectScanResult, plan: ProjectPlan) -> ExecutionPlan:
    """Derive a structured ExecutionPlan from an already-classified
    TaskIntent plus the project's already-computed scan/plan. Never
    touches the filesystem, git, network, or the LLM - purely reasons
    about the inputs given. Deterministic: the same intent + scan + plan
    always produces the same ExecutionPlan.

    `plan` is accepted (not just `scan`) so a future revision can factor
    in recommended_steps/suggested_order without changing this function's
    signature again - v1 does not yet use it beyond accepting it, mirroring
    how project_work.py already treats the ProjectPlan as the sole input
    its callers pass through.
    """
    if not intent.raw_text.strip():
        return _blocked_plan(
            intent,
            "No task description was given. Provide a description of what you'd like done.",
        )

    if intent.mentions_external_service:
        return _blocked_plan(
            intent,
            (
                f"Mentions '{intent.external_service_name}', an external integration that is "
                "not enabled in this stage of JARVIS. External integrations (Instagram, "
                "Facebook, Gmail, Stripe, and others) are out of scope for Task Execution v1."
            ),
        )

    if intent.mentions_shell_execution or intent.action_category == SHELL_EXECUTION:
        return _blocked_plan(
            intent,
            (
                "Mentions running a shell/terminal command directly. Shell command execution "
                "is not enabled in this stage of JARVIS - Task Execution v1 never plans an "
                "auto-run shell step."
            ),
        )

    if intent.action_category == EXTERNAL_INTEGRATION:  # pragma: no cover - defensive, already caught above
        return _blocked_plan(intent, "External integrations are not enabled in this stage of JARVIS.")

    if intent.action_category == GIT_OPERATION:
        steps = _git_operation_steps(scan)
    elif intent.action_category == TEST_RUN:
        steps = _test_run_steps()
    elif intent.action_category == FILE_CHANGE:
        steps = _file_change_steps(intent)
    else:
        assert intent.action_category == UNKNOWN
        return _blocked_plan(
            intent,
            (
                "Could not confidently classify this request. Describe the task more "
                "specifically (e.g. mention a file, test, or git operation), or ask JARVIS "
                "directly in the REPL."
            ),
        )

    return ExecutionPlan(intent=intent, steps=steps, can_proceed=True, blocked_reason=None)
