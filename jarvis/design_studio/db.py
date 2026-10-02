"""SQLite metadata storage for AI Design Studio - one row per design
project (jarvis.design_studio.storage.DesignProject's id, the original
plain-language prompt, the generated DesignBrief, and the currently
selected/rendered design) plus a growing set of related tables as
later stages add them (variants, Brand Kit).

Same pattern as jarvis.video_studio.db (see that module's own
docstring for the full rationale, equally applicable here): per-call
sqlite3 connections, idempotent CREATE TABLE IF NOT EXISTS schema plus
an idempotent ALTER TABLE ADD COLUMN migration step for columns added
after a table's first release, JSON-blob columns for content whose
shape may still evolve across this feature's staged rollout.

This module only stores/retrieves METADATA - the actual rendered
design images live on disk under
jarvis.config.DESIGN_STUDIO_PROJECTS_DIR (see jarvis.design_studio
.storage). A project row here always corresponds to a project
directory there; jarvis.design_studio.storage.delete_project() and
delete_project_record() below are separate calls a caller must both
make to fully remove a project - same deliberate two-step-not-
auto-cascaded reasoning as jarvis.video_studio.db's own delete
functions.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from jarvis.config import DESIGN_STUDIO_DB_FILE

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    original_prompt TEXT NOT NULL,
    brief_data TEXT,
    design_path TEXT,
    status TEXT NOT NULL DEFAULT 'created'
);
"""
# status is a free-text progress marker ("created", "brief_generated",
# "rendered", "exported") - purely informational for a "Recent
# Designs" strip (module brief section 1) to show at a glance, same
# convention as jarvis.video_studio.db's own `status` column; nothing
# in this module enforces a state machine over it.
#
# brief_data holds one jarvis.design_studio.brief.DesignBrief per
# project (as JSON, via dataclasses.asdict()) - one "current" brief per
# project (re-generating overwrites it, matching
# jarvis.video_studio.db's save_analysis()/save_transcript()'s own
# overwrite-not-additive convention), not a history of past attempts.
#
# design_path holds the path to the currently selected/rendered design
# image - a plain path string, not JSON, since Stage 1 renders exactly
# one design per project (a later stage's 3-variant picker adds a
# separate `variants` table rather than overloading this column - see
# this module's own future-columns comment below).

_ADDED_COLUMNS: tuple[tuple[str, str], ...] = ()
# Empty for this module's first release (unlike jarvis.video_studio.db,
# which started with a smaller schema and grew columns over several
# stages already completed before this module existed) - kept as an
# explicit empty tuple (not simply omitted) so a later stage's
# additions follow the exact same idempotent-ALTER-TABLE pattern
# jarvis.video_studio.db already established, rather than needing to
# introduce the pattern from scratch.


def _migrate(conn: sqlite3.Connection) -> None:
    if not _ADDED_COLUMNS:
        return
    existing = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    for column, column_type in _ADDED_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {column} {column_type}")  # noqa: S608 - column/type are fixed module constants, never user input


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    DESIGN_STUDIO_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DESIGN_STUDIO_DB_FILE))
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
    original_prompt: str
    brief_data: dict[str, Any] | None
    design_path: str | None
    status: str


def create_project_record(project_id: str, original_prompt: str) -> None:
    """Inserts a new project row right after
    jarvis.design_studio.storage.create_project() has created the
    directory structure - called separately (not from storage.py,
    which has no database dependency), matching
    jarvis.video_studio.db's own storage/db split."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, created_at, original_prompt, brief_data, design_path, status) "
            "VALUES (?, ?, ?, NULL, NULL, 'created')",
            (project_id, _now_iso(), original_prompt),
        )


def save_brief(project_id: str, brief_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.design_studio.brief.DesignBrief (as a
    plain dict via dataclasses.asdict()) and advances status to
    'brief_generated'. Overwrites any previous brief for this project
    (re-generating is idempotent, not additive)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET brief_data = ?, status = 'brief_generated' WHERE id = ?",
            (json.dumps(brief_data, ensure_ascii=False), project_id),
        )


def save_design_path(project_id: str, design_path: str) -> None:
    """Records the path to the project's currently rendered design
    image and advances status to 'rendered'. Overwrites any previous
    design path (re-rendering is idempotent, not additive)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET design_path = ?, status = 'rendered' WHERE id = ?",
            (design_path, project_id),
        )


def set_status(project_id: str, status: str) -> None:
    """Updates only the status marker - used by later stages (export)
    to advance a project's at-a-glance state without touching
    brief_data/design_path."""
    with _connect() as conn:
        conn.execute("UPDATE projects SET status = ? WHERE id = ?", (status, project_id))


_SELECT_COLUMNS = "id, created_at, original_prompt, brief_data, design_path, status"


def _row_to_record(row: tuple) -> ProjectRecord:
    return ProjectRecord(
        id=row[0], created_at=row[1], original_prompt=row[2],
        brief_data=json.loads(row[3]) if row[3] else None, design_path=row[4], status=row[5],
    )


def get_project(project_id: str) -> ProjectRecord | None:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM projects WHERE id = ?",  # noqa: S608 - _SELECT_COLUMNS is a fixed module constant, never user input
            (project_id,),
        ).fetchone()
    return None if row is None else _row_to_record(row)


def list_projects(limit: int = 50) -> list[ProjectRecord]:
    """Newest-first, for the module brief's "Recent Designs" strip on
    the dashboard. Ties broken by sqlite's own implicit rowid (which
    always reflects insertion order) rather than created_at alone,
    since created_at has only whole-second precision - same reasoning
    jarvis.video_studio.db.list_projects() documents for its own
    identical tie-break."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM projects ORDER BY created_at DESC, rowid DESC LIMIT ?",  # noqa: S608
            (limit,),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def delete_project_record(project_id: str) -> None:
    """Deletes only this table's row - does NOT touch the project's
    on-disk directory (see jarvis.design_studio.storage.delete_project()
    for that; this module's own docstring explains why the two are
    kept as separate, deliberate calls rather than auto-cascaded)."""
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
