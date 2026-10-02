"""SQLite metadata storage for AI Video Studio - one row per video
project (jarvis.video_studio.storage.VideoProject's id, original
filename, and its VideoAnalysis result) plus a growing set of related
tables as later stages add them (transcript segments, subtitle style
choices, generated hooks/covers/captions, exports).

Same pattern as jarvis.instagram_ai_manager.db (see that module's
docstring for the full rationale): per-call sqlite3 connections (never
held open across the GUI's lifetime, so a background-thread call never
shares a connection object), idempotent CREATE TABLE IF NOT EXISTS
schema, JSON-blob `data` columns for content whose shape may still
evolve across this feature's staged rollout - only the handful of
columns every query needs to filter/sort by (id, created_at,
project_id, original_filename) are their own real columns.

This module only stores/retrieves METADATA - the actual video/audio/
subtitle/export files live on disk under
jarvis.config.VIDEO_STUDIO_PROJECTS_DIR (see jarvis.video_studio
.storage). A project row here always corresponds to a project
directory there; jarvis.video_studio.storage.delete_project() and
delete_project() below are separate calls a caller must both make to
fully remove a project (kept separate, not auto-cascaded, so a UI
"delete" action is one deliberate, visible two-step operation rather
than a hidden side effect - matches the module's brief's emphasis on
never silently destroying data).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterator

from jarvis.config import VIDEO_STUDIO_DB_FILE

if TYPE_CHECKING:
    # Import-time only (see save_export_record()'s docstring for why
    # this module otherwise has no dependency on jarvis.video_studio
    # .export - it stores plain values, not ExportResult objects,
    # keeping db.py free of an import cycle risk as this package
    # grows).
    from jarvis.video_studio.export import ExportResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    analysis_data TEXT,
    transcript_data TEXT,
    highlights_data TEXT,
    reel_plan_data TEXT,
    cover_path TEXT,
    handoff_data TEXT,
    status TEXT NOT NULL DEFAULT 'uploaded'
);
CREATE TABLE IF NOT EXISTS exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    export_format TEXT NOT NULL,
    file_path TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    file_size_bytes INTEGER NOT NULL
);
"""
# `exports` is a real, separate table (not a JSON blob column on
# `projects`) because a project can have MANY exports over time (one
# per format/attempt, module brief section 12's "Create Another"
# button) - unlike analysis/transcript/highlights/reel_plan, which are
# each "the one current X" for a project and get overwritten in place.
# transcript_data/highlights_data hold one jarvis.video_studio
# .transcribe.TranscriptionResult / .highlights.HighlightResult per
# project (as JSON, via dataclasses.asdict()) - one of each per
# project, not a separate table, since a project has exactly one
# "current" transcript and one "current" highlight set (re-transcribing
# or re-detecting highlights overwrites the previous one, same
# overwrite-not-additive convention as analysis_data/save_analysis()
# above) rather than a history of past attempts.
# status is a free-text progress marker ("uploaded", "analyzed",
# "transcribed", "reel_created", "exported") - purely informational for
# the "Recent Projects" list (module brief section 1) to show at a
# glance; nothing in this module enforces a state machine over it, and
# a value this module doesn't recognize is stored/returned as-is rather
# than rejected, so a later stage can introduce new status values
# without a migration here.


# Columns added to `projects` after its first release (v1.1.0's Stage 1
# had only analysis_data/status) - CREATE TABLE IF NOT EXISTS alone
# does nothing for a table that already exists with an older column
# set, so an existing .jarvis/video_studio.db from before this stage
# would otherwise raise "no such column" the first time a query
# touches transcript_data/highlights_data. _migrate() below adds any
# column here that's missing, each guarded by its own existence check
# so it's safe to run on every connection (idempotent, like the rest of
# this module's schema handling) rather than needing a versioned
# migration runner for what is, so far, only ever an additive change.
_ADDED_COLUMNS = (
    ("transcript_data", "TEXT"),
    ("highlights_data", "TEXT"),
    ("reel_plan_data", "TEXT"),
    ("cover_path", "TEXT"),
    ("handoff_data", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    for column, column_type in _ADDED_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {column} {column_type}")  # noqa: S608 - column/type are fixed module constants, never user input


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    VIDEO_STUDIO_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(VIDEO_STUDIO_DB_FILE))
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
    original_filename: str
    analysis_data: dict[str, Any] | None
    transcript_data: dict[str, Any] | None
    highlights_data: dict[str, Any] | None
    reel_plan_data: dict[str, Any] | None
    cover_path: str | None
    """Path to the project's chosen/rendered cover image (see
    jarvis.video_studio.cover) - a plain file path string, not JSON
    (unlike the other *_data columns), since a cover is a single image
    file on disk, not a structured result to reconstruct into a
    dataclass."""
    handoff_data: dict[str, Any] | None
    """Set once this project's hook/caption/CTA/hashtags/cover have
    been sent to Instagram AI Manager (see jarvis.video_studio
    .instagram_handoff) - records WHAT was sent and the
    jarvis.instagram_ai_manager.db row ids it created, so the UI can
    show "already sent" rather than risk silently duplicating records
    on a second click."""
    status: str


def create_project_record(project_id: str, original_filename: str) -> None:
    """Inserts a new project row right after
    jarvis.video_studio.storage.create_project() has copied the file to
    disk - called separately (not from storage.py, which has no
    database dependency) so the two concerns - disk layout and metadata
    - stay independently testable, matching this package's storage/db
    split."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, created_at, original_filename, analysis_data, "
            "transcript_data, highlights_data, reel_plan_data, cover_path, handoff_data, status) "
            "VALUES (?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, 'uploaded')",
            (project_id, _now_iso(), original_filename),
        )


def save_analysis(project_id: str, analysis_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.video_studio.analysis.VideoAnalysis
    result (as a plain dict - callers pass dataclasses.asdict(analysis)
    - see jarvis.gui.views.video_studio for the exact call site) and
    advances status to 'analyzed'. Overwrites any previous analysis for
    this project (re-analyzing is idempotent, not additive)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET analysis_data = ?, status = 'analyzed' WHERE id = ?",
            (json.dumps(analysis_data, ensure_ascii=False), project_id),
        )


def save_transcript(project_id: str, transcript_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.video_studio.transcribe
    .TranscriptionResult (as a plain dict via dataclasses.asdict()) and
    advances status to 'transcribed'. Overwrites any previous
    transcript (re-transcribing is idempotent, not additive - matches
    save_analysis()'s own convention)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET transcript_data = ?, status = 'transcribed' WHERE id = ?",
            (json.dumps(transcript_data, ensure_ascii=False), project_id),
        )


def save_highlights(project_id: str, highlights_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.video_studio.highlights.HighlightResult
    (as a plain dict via dataclasses.asdict()). Does not change
    `status` - highlight detection is a read-only analysis step over an
    existing transcript, not a further stage of the project's own
    progress marker (unlike transcription, which unlocks subtitle
    export and is therefore worth surfacing in the Recent Projects
    status label)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET highlights_data = ? WHERE id = ?",
            (json.dumps(highlights_data, ensure_ascii=False), project_id),
        )


def save_reel_plan(project_id: str, reel_plan_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.video_studio.reel.ReelEditPlan (as a
    plain dict via dataclasses.asdict()) and advances status to
    'reel_created'. Overwrites any previous plan (re-generating is
    idempotent, not additive - matches save_analysis()/
    save_transcript()'s own convention)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET reel_plan_data = ?, status = 'reel_created' WHERE id = ?",
            (json.dumps(reel_plan_data, ensure_ascii=False), project_id),
        )


def save_cover(project_id: str, cover_path: str) -> None:
    """Records the path to a project's chosen/rendered cover image
    (see jarvis.video_studio.cover.render_cover()) - a plain path
    string, not JSON (see ProjectRecord.cover_path's own docstring for
    why). Overwrites any previously saved cover path."""
    with _connect() as conn:
        conn.execute("UPDATE projects SET cover_path = ? WHERE id = ?", (cover_path, project_id))


def save_handoff(project_id: str, handoff_data: dict[str, Any]) -> None:
    """Records that this project's content was sent to Instagram AI
    Manager (see jarvis.video_studio.instagram_handoff) - `handoff_data`
    holds what was sent and the resulting jarvis.instagram_ai_manager.db
    row ids, so a UI can show "already sent to Instagram Manager"
    rather than risk a person clicking the hand-off button twice and
    creating duplicate draft rows there. Overwrites any previous
    hand-off record (a second hand-off is a deliberate re-send, not
    accumulated)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET handoff_data = ? WHERE id = ?",
            (json.dumps(handoff_data, ensure_ascii=False), project_id),
        )


def set_status(project_id: str, status: str) -> None:
    """Updates only the status marker - used by later stages
    (transcription, Reel creation, export) to advance a project's
    at-a-glance state without touching analysis_data."""
    with _connect() as conn:
        conn.execute("UPDATE projects SET status = ? WHERE id = ?", (status, project_id))


_SELECT_COLUMNS = (
    "id, created_at, original_filename, analysis_data, transcript_data, "
    "highlights_data, reel_plan_data, cover_path, handoff_data, status"
)


def _row_to_record(row: tuple) -> ProjectRecord:
    return ProjectRecord(
        id=row[0], created_at=row[1], original_filename=row[2],
        analysis_data=json.loads(row[3]) if row[3] else None,
        transcript_data=json.loads(row[4]) if row[4] else None,
        highlights_data=json.loads(row[5]) if row[5] else None,
        reel_plan_data=json.loads(row[6]) if row[6] else None,
        cover_path=row[7],
        handoff_data=json.loads(row[8]) if row[8] else None,
        status=row[9],
    )


def get_project(project_id: str) -> ProjectRecord | None:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM projects WHERE id = ?",  # noqa: S608 - _SELECT_COLUMNS is a fixed module constant, never user input
            (project_id,),
        ).fetchone()
    return None if row is None else _row_to_record(row)


def list_projects(limit: int = 50) -> list[ProjectRecord]:
    """Newest-first, for the module brief's "Recent Projects" strip on
    the dashboard. Ties broken by sqlite's own implicit rowid (which
    always reflects insertion order) rather than created_at alone,
    since created_at has only whole-second precision - projects created
    within the same second (e.g. in a test, or two rapid uploads) would
    otherwise sort in an unspecified order."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM projects ORDER BY created_at DESC, rowid DESC LIMIT ?",  # noqa: S608
            (limit,),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def delete_project_record(project_id: str) -> None:
    """Deletes only this table's row - does NOT touch the project's
    on-disk directory (see jarvis.video_studio.storage.delete_project()
    for that; this module's own docstring explains why the two are
    kept as separate, deliberate calls rather than auto-cascaded)."""
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


# --- exports ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportRecord:
    id: int
    project_id: str
    created_at: str
    export_format: str
    file_path: str
    duration_seconds: float
    width: int
    height: int
    file_size_bytes: int


def save_export_record(project_id: str, result: "ExportResult") -> int:
    """Records one completed export (jarvis.video_studio.export
    .ExportResult) for a project - advances status to 'exported' and
    returns the new export row's id. Called AFTER
    jarvis.video_studio.export.export_reel() has already written the
    file to disk; this function never touches the file itself, only
    records that it exists (module brief section 12's "After export:
    preview, filename, duration, resolution, file size" - all of which
    this row already carries, so the UI never needs to re-probe the
    file to show them)."""
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO exports (project_id, created_at, export_format, file_path, "
            "duration_seconds, width, height, file_size_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id, _now_iso(), result.export_format, str(result.output_path),
                result.duration_seconds, result.width, result.height, result.file_size_bytes,
            ),
        )
        conn.execute("UPDATE projects SET status = 'exported' WHERE id = ?", (project_id,))
        return int(cursor.lastrowid)  # type: ignore[arg-type]


def list_exports(project_id: str) -> list[ExportRecord]:
    """Newest-first, for the module brief's "Create Another" flow (a
    person may export the same project to several formats)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, project_id, created_at, export_format, file_path, duration_seconds, "
            "width, height, file_size_bytes FROM exports WHERE project_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (project_id,),
        ).fetchall()
    return [
        ExportRecord(
            id=r[0], project_id=r[1], created_at=r[2], export_format=r[3], file_path=r[4],
            duration_seconds=r[5], width=r[6], height=r[7], file_size_bytes=r[8],
        )
        for r in rows
    ]
