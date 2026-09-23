"""Formats a Task (jarvis.core.task) into human-readable output for the
CLI's 'task' entry point. Kept separate from the Task/TaskPlanner logic
itself, mirroring project_plan_view's separation from project_plan - no
reasoning lives here, only presentation. Reuses
task_execution_plan_view.format_execution_plan for the plan portion
rather than re-formatting ExecutionPlan/ExecutionStep fields itself."""

from __future__ import annotations

from jarvis.core.task import Task
from jarvis.core.task_execution_plan_view import format_execution_plan


def format_task(task: Task) -> str:
    lines: list[str] = []

    lines.append(f"Task #{task.id}")
    lines.append(f"Status: {task.status}")
    lines.append(f"Overall risk level: {task.risk_level}")
    lines.append("Requires approval: " + ("yes" if task.requires_approval else "no"))
    lines.append("")
    lines.append(format_execution_plan(task.plan))

    return "\n".join(lines)
