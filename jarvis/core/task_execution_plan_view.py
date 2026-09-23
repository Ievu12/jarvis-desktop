"""Formats an ExecutionPlan (jarvis.core.task_execution_plan) into
human-readable output for the CLI's 'task' entry point. Kept separate
from the planning logic itself, mirroring project_plan_view's separation
from project_plan and project_work_view's separation from project_work -
no reasoning lives here, only presentation."""

from __future__ import annotations

from jarvis.core.task_execution_plan import ExecutionPlan, ExecutionStep

_RISK_LABELS = {
    "read_only": "read-only",
    "reversible": "reversible",
    "irreversible": "irreversible",
}


def _format_step(index: int, step: ExecutionStep) -> list[str]:
    lines = [f"  {index}. {step.description}"]
    if step.action_kind == "blocked":  # pragma: no cover - defensive; can_proceed=False already short-circuits above
        lines.append(f"     -> BLOCKED: {step.blocked_reason}")
        return lines

    risk_label = _RISK_LABELS.get(step.risk_level, step.risk_level)
    lines.append(f"     Risk: {risk_label}")
    if step.requires_approval:
        lines.append("     Requires human approval in the JARVIS REPL before anything runs.")
    return lines


def format_execution_plan(plan: ExecutionPlan) -> str:
    lines: list[str] = []

    lines.append(f"Task: {plan.intent.raw_text!r}")
    lines.append(f"Category: {plan.intent.action_category}")
    lines.append("")

    if not plan.can_proceed:
        lines.append("Cannot proceed with this task in the current JARVIS stage.")
        lines.append(f"Reason: {plan.blocked_reason}")
        lines.append("")
        lines.append(
            "jarvis task never executes anything itself - it only analyzes the request "
            "and, when unsupported, explains why."
        )
        return "\n".join(lines)

    lines.append("Proposed steps:")
    for i, step in enumerate(plan.steps, start=1):
        lines.extend(_format_step(i, step))
    lines.append("")

    lines.append(
        "jarvis task never writes files, never runs git or shell commands, and never "
        "performs an irreversible action itself - it only produces this plan. Actually "
        "doing the work still goes through the normal, approval-gated JARVIS REPL."
    )

    return "\n".join(lines)
