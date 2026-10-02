"""SQLite metadata storage for AI Storytelling Generator - one row per
Story project (jarvis.story_generator.storage.StoryProject's id, the
original plain-language idea, story_type, the generated StoryStructure,
the generated Storyboard/scenes, and export data).

Same pattern as jarvis.reel_generator.db (see that module's own
docstring for the full rationale): per-call sqlite3 connections,
idempotent CREATE TABLE IF NOT EXISTS schema plus an idempotent ALTER
TABLE ADD COLUMN migration step for columns added later, JSON-blob
columns for content whose shape may still evolve.

This module only stores/retrieves METADATA - actual rendered
images/video live on disk under
jarvis.config.STORY_GENERATOR_PROJECTS_DIR (see
jarvis.story_generator.storage).

structure_approved tracks module brief requirements 5-6's own hard
requirement ("APPROVE STORY" gate) as an explicit, persisted boolean -
not inferred from "structure_data is not NULL", exactly mirroring
jarvis.reel_generator.db's own script_approved reasoning (see that
module's docstring)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from jarvis.config import STORY_GENERATOR_DB_FILE

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    original_idea TEXT NOT NULL,
    story_type TEXT NOT NULL DEFAULT 'personal',
    structure_data TEXT,
    structure_approved INTEGER NOT NULL DEFAULT 0,
    storyboard_data TEXT,
    visual_plan_data TEXT,
    export_path TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    error_message TEXT
);
"""
# status is a free-text progress marker ("created", "structure_generated",
# "structure_approved", "scenes_generated", "visuals_generated",
# "exported", "failed") - purely informational for a "Recent Stories"
# strip to show at a glance, same convention as
# jarvis.reel_generator.db's own `status` column; nothing in this module
# enforces a state machine over it beyond the structure_approved flag
# itself.
#
# error_message holds the last real failure's human-readable message
# (module brief requirement 12: errors visible in the UI) - cleared
# whenever a set_status() call moves the project to any non-"failed"
# status (see set_status()/set_failed() below).

_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("visual_plan_data", "TEXT"),
)
# visual_plan_data was added after this table's first release (this
# feature's own visual-richness stage - see
# jarvis.story_generator.visual_plan's own docstring) - present in
# _SCHEMA's own CREATE TABLE above for a brand-new database, and added
# via this idempotent ALTER TABLE path for anyone who already has an
# older story_generator.db on disk, matching
# jarvis.reel_generator.db's own established migration pattern.


def _migrate(conn: sqlite3.Connection) -> None:
    if not _ADDED_COLUMNS:
        return
    existing = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    for column, column_type in _ADDED_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {column} {column_type}")  # noqa: S608 - column/type are fixed module constants, never user input


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    STORY_GENERATOR_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(STORY_GENERATOR_DB_FILE))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ProjectRecord:
    id: str
    created_at: str
    original_idea: str
    story_type: str
    structure_data: dict[str, Any] | None
    structure_approved: bool
    storyboard_data: dict[str, Any] | None
    visual_plan_data: dict[str, Any] | None
    export_path: str | None
    status: str
    error_message: str | None


def create_project_record(project_id: str, original_idea: str, *, story_type: str) -> None:
    """Inserts a new project row right after
    jarvis.story_generator.storage.create_project() has created the
    directory structure - called separately, matching
    jarvis.reel_generator.db's own storage/db split."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, created_at, original_idea, story_type, structure_data, "
            "structure_approved, status) VALUES (?, ?, ?, ?, NULL, 0, 'created')",
            (project_id, _now_iso(), original_idea, story_type),
        )


def save_structure(project_id: str, structure_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.story_generator.structure.StoryStructure
    (as a plain dict via dataclasses.asdict()), advances status to
    'structure_generated', and RESETS structure_approved back to False -
    a freshly (re)generated structure has not been approved yet, even if
    a previous version once was (mirrors
    jarvis.reel_generator.db.save_script()'s own exact reasoning).
    Clears any previous error_message (a fresh attempt supersedes a past
    failure)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET structure_data = ?, structure_approved = 0, "
            "status = 'structure_generated', error_message = NULL WHERE id = ?",
            (json.dumps(structure_data, ensure_ascii=False), project_id),
        )


def approve_structure(project_id: str) -> None:
    """Marks the project's CURRENT structure_data as approved (module
    brief requirement 6: "APPROVE STORY") and advances status to
    'structure_approved'. A later save_structure() call (regenerating
    the structure) resets this back to False."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET structure_approved = 1, status = 'structure_approved' WHERE id = ?",
            (project_id,),
        )


def save_storyboard(project_id: str, storyboard_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.reel_generator.storyboard.Storyboard
    (the story's own 5-10 scenes, as a plain dict via
    dataclasses.asdict()) and advances status to 'scenes_generated'.
    Overwrites any previous storyboard for this project - only ever
    called after structure_approved is already True (module brief
    requirements 5-6's gate), enforced by the GUI caller, not by this
    function itself. Clears any previous error_message."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET storyboard_data = ?, status = 'scenes_generated', "
            "error_message = NULL WHERE id = ?",
            (json.dumps(storyboard_data, ensure_ascii=False), project_id),
        )


def save_visual_plan(project_id: str, visual_plan_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.story_generator.visual_plan.VisualPlan
    (one ScenePlan per scene, as a plain dict via
    dataclasses.asdict()) - saved alongside save_storyboard() (both come
    from the SAME generate_story_scenes() call - see that function's own
    docstring) but as its OWN column, never merged into storyboard_data,
    so a project saved before this feature existed (or reopened by an
    older build) still has a valid storyboard_data with no
    visual_plan_data, and every reader treats a missing plan as "render
    this scene plainly" rather than an error."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET visual_plan_data = ? WHERE id = ?",
            (json.dumps(visual_plan_data, ensure_ascii=False), project_id),
        )


def save_export_path(project_id: str, export_path: str) -> None:
    """Records the path to the project's most recent final MP4 export
    and advances status to 'exported'. Clears any previous
    error_message. Same "one current export path, old files stay on
    disk under exports_dir" reasoning as
    jarvis.reel_generator.db.save_export_path()'s own docstring."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET export_path = ?, status = 'exported', error_message = NULL WHERE id = ?",
            (export_path, project_id),
        )


def set_status(project_id: str, status: str) -> None:
    """Updates only the status marker (e.g. 'visuals_generated') and
    clears any previous error_message - used by later stages to advance
    a project's at-a-glance state without touching structure_data/
    storyboard_data."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET status = ?, error_message = NULL WHERE id = ?", (status, project_id),
        )


def set_failed(project_id: str, error_message: str) -> None:
    """Records a real failure (module brief requirement 12: errors
    visible in the UI, not just the terminal/log) - status becomes
    'failed' and error_message holds the human-readable reason, so a
    reopened project shows exactly what went wrong rather than silently
    looking stuck."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET status = 'failed', error_message = ? WHERE id = ?",
            (error_message, project_id),
        )


_SELECT_COLUMNS = (
    "id, created_at, original_idea, story_type, structure_data, structure_approved, "
    "storyboard_data, visual_plan_data, export_path, status, error_message"
)


def _row_to_record(row: tuple) -> ProjectRecord:
    return ProjectRecord(
        id=row[0], created_at=row[1], original_idea=row[2], story_type=row[3],
        structure_data=json.loads(row[4]) if row[4] else None,
        structure_approved=bool(row[5]),
        storyboard_data=json.loads(row[6]) if row[6] else None,
        visual_plan_data=json.loads(row[7]) if row[7] else None,
        export_path=row[8], status=row[9], error_message=row[10],
    )


def get_project(project_id: str) -> ProjectRecord | None:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM projects WHERE id = ?",  # noqa: S608 - _SELECT_COLUMNS is a fixed module constant, never user input
            (project_id,),
        ).fetchone()
    return None if row is None else _row_to_record(row)


def list_projects(limit: int = 50) -> list[ProjectRecord]:
    """Newest-first, for a "Recent Stories" strip. Ties broken by
    sqlite's own implicit rowid, same reasoning
    jarvis.reel_generator.db.list_projects() documents for its own
    identical tie-break."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM projects ORDER BY created_at DESC, rowid DESC LIMIT ?",  # noqa: S608
            (limit,),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def delete_project_record(project_id: str) -> None:
    """Deletes only this table's row - does NOT touch the project's
    on-disk directory (see jarvis.story_generator.storage
    .delete_project() for that)."""
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
