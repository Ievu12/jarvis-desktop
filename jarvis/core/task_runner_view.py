"""Formats a TaskRun (jarvis.core.task_run_state) into human-readable
output. Kept separate from the running logic itself, mirroring every
other *_view.py module in this layer - no state transitions happen here,
only presentation."""

from __future__ import annotations

from jarvis.core.task_run_state import RunStep, TaskRun

_STATUS_LABELS = {
    "pending": "pending",
    "approved": "approved",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "blocked": "blocked",
}


def _format_run_step(index: int, run_step: RunStep) -> list[str]:
    label = _STATUS_LABELS.get(run_step.status, run_step.status)
    lines = [f"  {index}. [{label}] {run_step.step.description}"]
    if run_step.result_summary:
        lines.append(f"     {run_step.result_summary}")
    if run_step.failure_reason:
        lines.append(f"     Reason: {run_step.failure_reason}")
    return lines


def format_task_run(run: TaskRun) -> str:
    lines: list[str] = []

    lines.append(f"Task run for Task #{run.task.id}: {run.task.description!r}")
    lines.append("")

    for i, run_step in enumerate(run.run_steps, start=1):
        lines.extend(_format_run_step(i, run_step))
    lines.append("")

    if run.is_fully_completed:
        lines.append("All steps completed.")
    elif run.stopped_early:
        lines.append(f"Run stopped early. Reason: {run.stop_reason}")
    else:
        lines.append("Run did not complete (no steps were run).")

    return "\n".join(lines)
