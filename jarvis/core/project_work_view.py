"""Formats a WorkItem (jarvis.core.project_work) into human-readable
output for the CLI's 'work' entry point. Kept separate from the
next-step-derivation logic itself, mirroring project_plan_view's
separation from project_plan - no reasoning lives here, only
presentation."""

from __future__ import annotations

from jarvis.core.project_work import WorkItem


def format_work_item(item: WorkItem) -> str:
    if not item.has_plan:
        return (
            "No plan available yet.\n"
            "Run 'jarvis plan' first to see recommended next steps."
        )

    if item.is_complete:
        return (
            "Nothing to do - the last plan found no gaps.\n"
            "Run 'jarvis plan' again after making changes to see if new steps appear."
        )

    lines: list[str] = []
    lines.append(f"Next step: {item.step_description}")
    lines.append("")

    if item.action_kind == "command":
        lines.append("This step is safe and mechanical. Run:")
        lines.append(f"  {item.action_detail}")
    else:
        lines.append("This step needs judgment, so JARVIS will not do it automatically.")
        lines.append("Suggested next action:")
        lines.append(f"  {item.action_detail}")

    lines.append("")
    lines.append(
        "jarvis work never writes files or runs git itself - it only tells you what to do next."
    )

    if item.remaining_step_count > 1:
        remaining_after_this = item.remaining_step_count - 1
        step_word = "step" if remaining_after_this == 1 else "steps"
        lines.append(
            f"({remaining_after_this} more {step_word} after this one - run 'jarvis plan' to see the full list.)"
        )

    return "\n".join(lines)
