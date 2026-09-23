"""The get_workflow_state tool: lets the LLM-driven agent read the same
structured, deterministic workflow views a human sees via the
'jarvis scan'/'plan'/'work'/'review'/'precommit' CLI arguments, instead of
re-deriving project state itself by chaining several run_command calls
(git-status, git-diff, ...).

Purely read-only: every stage here builds on jarvis.core.project_scan
.scan_project() and the pure derivation/formatting functions already
used by jarvis.cli.main's run_cli_* entry points - no new logic, no
filesystem writes, no git mutations, no LLM call of its own. Like
manage_tasks, this tool does not require the confirm_side_effect approval
prompt: it has no effect on the project's files, only reads already-safe,
already-tested state.

Deliberately excludes 'commit': committing has a real side effect (it
mutates git history) and must continue to go exclusively through the
existing approval-gated path (the shell tool's 'git-commit' entry /
'jarvis commit' / the REPL 'commit' command) - this tool never runs git
commit and is not a second way to reach it.
"""

from __future__ import annotations

from jarvis.config import JARVIS_ROOT
from jarvis.core.project_plan import build_plan
from jarvis.core.project_plan_view import format_plan
from jarvis.core.project_precommit import build_precommit_check
from jarvis.core.project_precommit_view import format_precommit_check
from jarvis.core.project_review import build_review
from jarvis.core.project_review_view import format_review_result
from jarvis.core.project_scan import scan_project
from jarvis.core.project_scan_view import format_scan_result
from jarvis.core.project_work import build_work_item
from jarvis.core.project_work_view import format_work_item
from jarvis.tools.base import Tool, ToolResult

_STAGES = ("scan", "plan", "work", "review", "precommit")


def _run_stage(stage: str) -> str:
    scan_result = scan_project(JARVIS_ROOT)
    if stage == "scan":
        return format_scan_result(scan_result)

    plan = build_plan(scan_result)
    if stage == "plan":
        return format_plan(plan)
    if stage == "work":
        return format_work_item(build_work_item(plan))

    review = build_review(scan_result, plan)
    if stage == "review":
        return format_review_result(review)
    if stage == "precommit":
        return format_precommit_check(build_precommit_check(review))

    raise AssertionError(f"unreachable: unhandled stage '{stage}'")  # pragma: no cover


class WorkflowStateTool(Tool):
    name = "get_workflow_state"
    description = (
        "Read the project's current state through JARVIS's deterministic, "
        "non-interactive workflow - the same output as running "
        "'jarvis scan'/'plan'/'work'/'review'/'precommit' from the command "
        "line. Prefer this over chaining several run_command calls "
        "(git-status, git-diff, etc.) to answer questions like 'what "
        "changed', 'what should I do next', or 'am I ready to commit' - "
        "it is cheaper and gives a structured answer in one call. "
        "Read-only: never writes a file, never runs git, requires no "
        "approval. Does NOT include 'commit' - committing always goes "
        "through the normal approval-gated run_command('git-commit') path."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "stage": {
                "type": "string",
                "enum": list(_STAGES),
                "description": (
                    "Which workflow stage to read: 'scan' (project survey), "
                    "'plan' (recommended next steps), 'work' (the single next "
                    "actionable step), 'review' (structured pre-commit review), "
                    "or 'precommit' (final readiness check with changed files)."
                ),
            },
        },
        "required": ["stage"],
    }

    def run(self, *, stage: str) -> ToolResult:
        if stage not in _STAGES:
            return ToolResult(
                ok=False,
                output=f"Denied: unknown stage '{stage}'. Allowed stages: {', '.join(_STAGES)}.",
            )
        return ToolResult(ok=True, output=_run_stage(stage))
