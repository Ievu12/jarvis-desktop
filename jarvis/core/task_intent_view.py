"""Formats a TaskIntent (jarvis.core.task_intent) into human-readable
output. Kept separate from the classification logic itself, mirroring
project_scan_view's separation from project_scan - no reasoning lives
here, only presentation."""

from __future__ import annotations

from jarvis.core.task_intent import TaskIntent


def format_task_intent(intent: TaskIntent) -> str:
    lines: list[str] = []

    lines.append(f"Request: {intent.raw_text!r}")
    lines.append(f"Category: {intent.action_category}")

    if intent.mentions_external_service:
        lines.append(f"External service mentioned: {intent.external_service_name}")

    if intent.mentions_shell_execution:
        lines.append("Mentions shell/terminal command execution.")

    lines.append("Supported in this stage: " + ("yes" if intent.is_supported else "no"))

    return "\n".join(lines)
