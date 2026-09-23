"""The create_plan tool: the single, atomic "propose a plan" action for a
multi-step request, distinct from manage_tasks' one-at-a-time add/
complete mutations used to execute against that plan afterward. Same
no-approval-prompt design as manage_tasks - this is JARVIS's own working
notes, not a project file, so it has no effect on the sandboxed project
itself."""

from __future__ import annotations

from jarvis.session.tasks import create_plan
from jarvis.tools.base import Tool, ToolResult


class CreatePlanTool(Tool):
    name = "create_plan"
    description = (
        "Create a fresh, ordered plan of steps for a multi-step request, "
        "replacing any existing task list in one atomic action. Use this "
        "once, at the start of a request that has three or more "
        "distinguishable steps, before beginning execution - it is the "
        "single 'here is the plan' moment, distinct from manage_tasks' "
        "add/complete actions which you use afterward to execute against "
        "and track progress on the plan this creates. If an incomplete "
        "plan already exists, it is replaced (the count of replaced steps "
        "is reported, never silently discarded). No approval prompt is "
        "required - this has no effect on the project's files, only on "
        "JARVIS's own bookkeeping."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Ordered list of step descriptions, each a short, concrete "
                    "action (e.g. 'Create hello.py with a greet function', "
                    "'Write a test for greet', 'Run the test suite')."
                ),
            },
        },
        "required": ["steps"],
    }

    def run(self, *, steps: list[str]) -> ToolResult:
        cleaned = [s.strip() for s in steps if s and s.strip()]
        if not cleaned:
            return ToolResult(ok=False, output="Denied: 'steps' must contain at least one non-empty step.")

        new_tasks, replaced_count = create_plan(cleaned)

        lines = [f"Created a plan with {len(new_tasks)} step(s):"]
        lines.extend(f"[ ] #{t.id} {t.text}" for t in new_tasks)
        if replaced_count:
            lines.append(f"\n(Replaced {replaced_count} step(s) from a previous plan.)")

        return ToolResult(ok=True, output="\n".join(lines))
