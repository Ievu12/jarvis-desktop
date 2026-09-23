"""Persistent project task list: JARVIS's own working notes about a
multi-step request, stored in .jarvis/tasks.json alongside session.json
and audit.log. Purely local bookkeeping - no filesystem/shell side
effects, so it needs no sandbox path resolution beyond living in .jarvis/,
and (per explicit user decision) task mutations don't require the
approval prompt used for real side effects like file writes."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from jarvis.config import TASKS_FILE


@dataclass
class Task:
    id: int
    text: str
    done: bool
    created_at: str


def _load_raw() -> list[dict[str, Any]]:
    if not TASKS_FILE.exists():
        return []
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []  # corrupted/unreadable - fail closed to an empty list, not a crash
    if not isinstance(data, list):
        return []
    return data


def _save_raw(tasks: list[dict[str, Any]]) -> None:
    TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TASKS_FILE.write_text(json.dumps(tasks, indent=2), encoding="utf-8")


def load_tasks() -> list[Task]:
    return [Task(**t) for t in _load_raw()]


def add_task(text: str) -> Task:
    raw = _load_raw()
    next_id = max((t["id"] for t in raw), default=0) + 1
    task = Task(
        id=next_id,
        text=text,
        done=False,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    raw.append(asdict(task))
    _save_raw(raw)
    return task


def complete_task(task_id: int) -> bool:
    """Mark a task done by id. Returns True if found and marked, False if
    no task with that id exists - never raises for a missing id."""
    raw = _load_raw()
    found = False
    for t in raw:
        if t["id"] == task_id:
            t["done"] = True
            found = True
            break
    if found:
        _save_raw(raw)
    return found


def clear_completed_tasks() -> int:
    """Remove all done tasks. Returns how many were removed."""
    raw = _load_raw()
    remaining = [t for t in raw if not t["done"]]
    removed = len(raw) - len(remaining)
    if removed:
        _save_raw(remaining)
    return removed


def clear_all_tasks() -> int:
    """Remove every task, done or not. Returns how many were removed."""
    raw = _load_raw()
    removed = len(raw)
    if removed:
        _save_raw([])
    return removed


def create_plan(steps: list[str]) -> tuple[list[Task], int]:
    """Atomically replace the current task list with a fresh ordered plan.
    This is the single "propose a plan" action - distinct from add_task's
    one-at-a-time mutation - so a plan has one clear moment of creation
    rather than being inferred from a sequence of individual adds.

    Any existing incomplete tasks are cleared first (never silently mixed
    with the new plan, which could leave stale, unrelated steps in the
    list). Returns (new_tasks, replaced_count) so the caller can report
    when prior work was discarded, rather than doing so silently.
    """
    raw = _load_raw()
    replaced_count = len(raw)

    new_tasks: list[Task] = []
    for i, text in enumerate(steps, start=1):
        new_tasks.append(
            Task(
                id=i,
                text=text,
                done=False,
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
        )
    _save_raw([asdict(t) for t in new_tasks])
    return new_tasks, replaced_count
