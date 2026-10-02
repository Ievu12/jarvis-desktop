"""SQLite metadata storage for the Professional Video Editor - one row
per edit project: its Timeline (ordered clips/stills, trim points,
retime, transitions), its imported MediaItems, and its last-used export
settings.

A SEPARATE database file (jarvis.config.VIDEO_EDITOR_DB_FILE) from
jarvis.video_studio.db - deliberately NEVER an added column/table on
that database. A Video Editor project's own schema (a general,
multi-source Timeline) is a genuinely different shape of record from
AI Video Studio's own single-source, highlight-driven reel project (see
jarvis.video_editor's own package docstring for the full architectural
reasoning) - keeping them in separate files means a bug in one module's
own schema/migration code can never corrupt the other's data (see this
package's own isolation rules).

Same pattern as jarvis.video_studio.db/jarvis.reel_generator.db (see
either module's own docstring for the full rationale): per-call sqlite3
connections, idempotent CREATE TABLE IF NOT EXISTS schema plus an
idempotent ALTER TABLE ADD COLUMN migration step for columns added
after this table's first release, JSON-blob columns for content whose
shape may still evolve across this module's staged rollout.

This module only stores/retrieves METADATA - the actual imported media
files and rendered exports live on disk under
jarvis.config.VIDEO_EDITOR_PROJECTS_DIR (see jarvis.video_editor
.storage). A project row here always corresponds to a project directory
there; jarvis.video_editor.storage.delete_project() and
delete_project_record() below are separate calls a caller must both
make to fully remove a project, matching jarvis.video_studio.db's own
deliberate two-step-not-auto-cascaded delete convention."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from jarvis.config import VIDEO_EDITOR_DB_FILE

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    name TEXT NOT NULL,
    timeline_data TEXT,
    media_items_data TEXT,
    export_format_last_used TEXT,
    status TEXT NOT NULL DEFAULT 'created'
);
"""
# timeline_data holds one jarvis.video_editor.timeline.Timeline (as
# JSON, via dataclasses.asdict()) - one "current" timeline per project,
# overwritten in place on every save (not a history of past edits,
# same "the one current X" convention jarvis.video_studio.db's own
# analysis_data/reel_plan_data columns already establish).
# media_items_data holds a JSON list of every jarvis.video_editor
# .media_import.MediaItem imported into this project (Path fields
# serialized to plain strings by the caller - jarvis.video_editor
# .storage/the GUI layer - before calling save_media_items(), the same
# "caller serializes Path fields" convention jarvis.reel_generator
# .dashboard's own motion-clip persistence already established for an
# analogous Path-bearing dataclass).
# status is a free-text progress marker ("created", "editing",
# "exported") - purely informational, same as jarvis.video_studio.db's
# own status column; nothing in this module enforces a state machine
# over it.


class VideoEditorProjectError(Exception):
    """Raised for a genuine database-layer failure (e.g. saving to an
    unknown project id) - always with a human-readable message. A LOCAL
    exception, never shared with jarvis.video_studio.db's own error
    handling (that module has none of its own today, but this keeps the
    two modules independent regardless)."""


@dataclass(frozen=True)
class VideoEditorProjectRecord:
    id: str
    created_at: str
    updated_at: str
    name: str
    timeline_data: dict[str, Any] | None
    media_items_data: list[dict[str, Any]] | None
    export_format_last_used: str | None
    status: str


_SELECT_COLUMNS = "id, created_at, updated_at, name, timeline_data, media_items_data, export_format_last_used, status"


def _row_to_record(row: tuple) -> VideoEditorProjectRecord:
    return VideoEditorProjectRecord(
        id=row[0], created_at=row[1], updated_at=row[2], name=row[3],
        timeline_data=json.loads(row[4]) if row[4] else None,
        media_items_data=json.loads(row[5]) if row[5] else None,
        export_format_last_used=row[6], status=row[7],
    )


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    VIDEO_EDITOR_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(VIDEO_EDITOR_DB_FILE))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_project_record(project_id: str, name: str) -> None:
    """Inserts a new project row right after
    jarvis.video_editor.storage.create_project() has created the
    directory structure - called separately, matching
    jarvis.video_studio.db/jarvis.reel_generator.db's own storage/db
    split. timeline_data/media_items_data/export_format_last_used all
    start NULL (an empty project has no timeline/media/export settings
    yet); status defaults to "created"."""
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, created_at, updated_at, name, status) VALUES (?, ?, ?, ?, 'created')",
            (project_id, now, now, name),
        )


def get_project(project_id: str) -> VideoEditorProjectRecord | None:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM projects WHERE id = ?",  # noqa: S608 - _SELECT_COLUMNS is a fixed module constant, never user input
            (project_id,),
        ).fetchone()
    return _row_to_record(row) if row is not None else None


def list_projects(limit: int = 50) -> list[VideoEditorProjectRecord]:
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM projects ORDER BY updated_at DESC LIMIT ?",  # noqa: S608
            (limit,),
        ).fetchall()
    return [_row_to_record(row) for row in rows]


def save_timeline(project_id: str, timeline_data: dict[str, Any]) -> None:
    """Stores this project's current Timeline (as a plain dict - the
    caller is responsible for dataclasses.asdict(timeline) before
    calling this, keeping this module free of a dependency on
    jarvis.video_editor.timeline's own dataclass shapes). Touches ONLY
    this column and updated_at - never media_items_data/status, same
    "each save_*() function owns exactly its own column(s)" discipline
    jarvis.reel_generator.db's own save_*() functions already
    establish."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET timeline_data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(timeline_data, ensure_ascii=False), _now_iso(), project_id),
        )


def save_media_items(project_id: str, media_items_data: list[dict[str, Any]]) -> None:
    """Stores this project's full list of imported MediaItems (as plain
    dicts - same caller-serializes convention as save_timeline()).
    Touches ONLY this column and updated_at."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET media_items_data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(media_items_data, ensure_ascii=False), _now_iso(), project_id),
        )


def save_export_format_last_used(project_id: str, export_format_label: str) -> None:
    """Remembers this project's own last-chosen export format label
    (e.g. "9:16 1080p") so reopening the project pre-selects it in the
    GUI's own export panel - purely a UI convenience, never read by
    jarvis.video_editor.multisource_export itself (which always takes
    an explicit ExportFormat from its own caller)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET export_format_last_used = ?, updated_at = ? WHERE id = ?",
            (export_format_label, _now_iso(), project_id),
        )


def set_status(project_id: str, status: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now_iso(), project_id),
        )


def delete_project_record(project_id: str) -> None:
    """Removes a project's metadata row - does NOT delete its on-disk
    directory (see jarvis.video_editor.storage.delete_project() for
    that separate, deliberate second step). Does nothing (not an error)
    if the project doesn't exist."""
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
