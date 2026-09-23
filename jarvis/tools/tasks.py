"""The manage_tasks tool: lets the model track its own progress on a
multi-step request as a persistent, local task list. Purely internal
bookkeeping (no filesystem/shell side effects on the project itself), so
per explicit design decision it does not require the confirm_side_effect
approval prompt used for real side effects elsewhere in the toolset."""

from __future__ import annotations

from jarvis.session.tasks import (
    add_task,
    clear_all_tasks,
    clear_completed_tasks,
    complete_task,
    load_tasks,
)
from jarvis.tools.base import Tool, ToolResult


def _format_task_list() -> str:
    tasks = load_tasks()
    if not tasks:
        return "No tasks tracked."
    lines = [f"[{'x' if t.done else ' '}] #{t.id} {t.text}" for t in tasks]
    return "\n".join(lines)


class ManageTasksTool(Tool):
    name = "manage_tasks"
    description = (
        "Track a persistent, local to-do list of steps for the current "
        "project work - JARVIS's own working notes, not a project file. "
        "Use this to break a multi-step request into tracked steps and "
        "check them off as they're completed, so progress is visible "
        "across the conversation and survives if the session is resumed "
        "later. No approval prompt is required for this tool - it has no "
        "effect on the project's files, only on JARVIS's own bookkeeping. "
        "Actions: 'add' (requires text), 'complete' (requires task_id), "
        "'list' (shows all tasks), 'clear_completed' (removes done tasks), "
        "'clear_all' (removes every task)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "complete", "list", "clear_completed", "clear_all"],
                "description": "Which task operation to perform.",
            },
            "text": {
                "type": "string",
                "description": "The task description. Required for 'add'.",
            },
            "task_id": {
                "type": "integer",
                "description": "The id of the task to mark complete. Required for 'complete'.",
            },
        },
        "required": ["action"],
    }

    def run(
        self,
        *,
        action: str,
        text: str | None = None,
        task_id: int | None = None,
    ) -> ToolResult:
        if action == "add":
            if not text or not text.strip():
                return ToolResult(ok=False, output="Denied: 'text' is required for 'add'.")
            task = add_task(text.strip())
            return ToolResult(ok=True, output=f"Added task #{task.id}: {task.text}")

        if action == "complete":
            if task_id is None:
                return ToolResult(
                    ok=False, output="Denied: 'task_id' is required for 'complete'."
                )
            found = complete_task(task_id)
            if not found:
                return ToolResult(ok=False, output=f"No task with id {task_id} found.")
            return ToolResult(ok=True, output=f"Marked task #{task_id} as done.")

        if action == "list":
            return ToolResult(ok=True, output=_format_task_list())

        if action == "clear_completed":
            removed = clear_completed_tasks()
            return ToolResult(ok=True, output=f"Cleared {removed} completed task(s).")

        if action == "clear_all":
            removed = clear_all_tasks()
            return ToolResult(ok=True, output=f"Cleared {removed} task(s).")

        return ToolResult(
            ok=False,
            output=(
                f"Denied: unknown action '{action}'. Allowed actions: add, complete, "
                "list, clear_completed, clear_all."
            ),
        )
