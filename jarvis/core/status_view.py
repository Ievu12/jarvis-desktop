"""Formats the 'status' command's output: a single, glanceable view tying
together the scan -> plan -> work -> review/precommit -> commit workflow,
so the user can see where things stand without checking scan/tasks/review
separately. Pure composition of existing read-only checks - no new
persistence, no new sandbox surface, nothing here writes anything.
"""

from __future__ import annotations

from jarvis.config import JARVIS_ROOT
from jarvis.core.project_notes import load_project_notes
from jarvis.core.review_view import ChangeSummary, get_change_summary
from jarvis.session.tasks import Task, load_tasks


def format_status() -> str:
    lines: list[str] = []

    # --- project understanding (JARVIS.md) -----------------------------
    notes_result = load_project_notes(JARVIS_ROOT)
    if notes_result.content is not None:
        lines.append("Project notes: JARVIS.md loaded.")
    else:
        lines.append("Project notes: no JARVIS.md yet.")

    # --- plan / task state ------------------------------------------------
    tasks = load_tasks()
    if not tasks:
        lines.append("Plan: no active plan.")
    else:
        done_count = sum(1 for t in tasks if t.done)
        total = len(tasks)
        lines.append(f"Plan: {done_count}/{total} step(s) done.")

    # --- uncommitted changes -----------------------------------------------
    change_summary = get_change_summary()
    if not change_summary.is_repo:
        lines.append("Git: not a repository yet.")
    elif change_summary.error is not None:
        lines.append(f"Git: could not check status ({change_summary.error}).")
    elif change_summary.changed_files == 0:
        lines.append("Git: working tree clean.")
    else:
        lines.append(f"Git: {change_summary.changed_files} file(s) with uncommitted changes.")

    lines.append("")
    lines.append(f"Suggested next step: {_suggest_next_step(notes_result.content is not None, tasks, change_summary)}")

    return "\n".join(lines)


def _suggest_next_step(
    has_notes: bool, tasks: list[Task], change_summary: ChangeSummary
) -> str:
    if not has_notes:
        return "type 'scan' to have JARVIS survey the project and propose a JARVIS.md draft."

    if tasks:
        pending = [t for t in tasks if not t.done]
        if pending:
            return f"{len(pending)} step(s) remaining in the current plan - type 'tasks' to see them."
        # Plan fully done - fall through to the git-state suggestion below.

    if change_summary.is_repo and change_summary.error is None:
        if change_summary.changed_files and change_summary.changed_files > 0:
            return "type 'review' or 'precommit' before committing your changes."
        return "nothing pending - the working tree is clean."

    if not change_summary.is_repo:
        return "ask JARVIS to run 'git-init' if you'd like to start tracking this project with git."

    return "just ask JARVIS what you'd like to work on next."
