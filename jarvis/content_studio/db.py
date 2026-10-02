"""SQLite metadata storage for AI Content Studio - one row per content
project: the original topic, the generated ContentPlan, a workflow
status per content type (module brief Stage 2: planned/creating/
created/approved/exported/failed), and a POINTER to the real linked
project (a jarvis.reel_generator project id for "reel", a
jarvis.design_studio project id for "story"/"post"/"carousel", or a
plain PDF file path for "pdf") - never a duplicated copy of that
project's own brief/script/render data (see this package's own
__init__.py docstring for the full rationale).

Same pattern as jarvis.reel_generator.db/jarvis.design_studio.db (see
either module's own docstring for the full rationale): per-call sqlite3
connections, idempotent CREATE TABLE IF NOT EXISTS schema plus an
idempotent ALTER TABLE ADD COLUMN migration step for columns added
after a table's first release, JSON-blob columns for content whose
shape may still evolve across this feature's staged rollout.

This module only stores/retrieves METADATA - the actual PDF file (the
one binary asset Content Studio genuinely owns) lives on disk under
jarvis.config.CONTENT_STUDIO_PROJECTS_DIR (see jarvis.content_studio
.storage). A project row here always corresponds to a project directory
there; storage.delete_project() and delete_project_record() below are
separate calls a caller must both make to fully remove a project - same
deliberate two-step-not-auto-cascaded reasoning as
jarvis.reel_generator.db's own delete functions.

The workflow status applies PER CONTENT TYPE, not to the whole project
- a person may approve the Reel while the Post is still planned. Each
content type therefore gets its OWN status column (reel_status/
story_status/post_status/carousel_status/pdf_status), all defaulting to
'planned', rather than one project-wide status column - a single column
would force an artificial "the whole project is one state until every
piece matches" rule this feature never actually asks for; "every
generated piece of content gets its own state", not "the project as one
indivisible unit".

Stage 2 note (orchestration): WORKFLOW_STATES was widened from Stage
1's original draft/review/approved/exported to the richer planned/
creating/created/approved/exported/failed vocabulary Stage 2's own
create-a-linked-project flow needs - "creating" covers the in-flight
background call to jarvis.reel_generator/jarvis.design_studio, "failed"
covers a real, surfaced error (never faked as success, per Stage 2's
own explicit requirement), and "created" is the new terminal state for
"the linked project exists and rendered successfully" as distinct from
"approved" (an explicit person action, module brief's own approval
gate) and "exported" (an explicit Export action on top of that). Stage
1 never persisted any row using the old vocabulary's distinct values in
a way a migration would need to translate (every Stage 1 test/manual
run left every column at its schema-level default), so this is a
same-column value-set widening, not a schema migration - no
_ADDED_COLUMNS entry needed for the vocabulary change itself. The new
`*_error` columns ARE new columns, added via the normal
_ADDED_COLUMNS/migration path below."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from jarvis.config import CONTENT_STUDIO_DB_FILE

# Stage 2's own richer workflow vocabulary (see this module's own
# docstring for why this widens, rather than migrates, Stage 1's
# original draft/review/approved/exported set) - a plain, fixed
# vocabulary enforced by set_content_type_status() below, not a
# free-text status column like several other modules' own
# informational-only `status` columns (this one IS load-bearing: it
# gates PDF export and, in a later stage, Instagram hand-off/publish
# readiness).
WORKFLOW_STATES = ("planned", "creating", "created", "approved", "exported", "failed")

_CONTENT_TYPES = ("reel", "story", "post", "carousel", "pdf")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    plan_data TEXT,
    reel_generator_project_id TEXT,
    reel_status TEXT NOT NULL DEFAULT 'planned',
    reel_error TEXT,
    story_design_project_id TEXT,
    story_status TEXT NOT NULL DEFAULT 'planned',
    story_error TEXT,
    post_design_project_id TEXT,
    post_status TEXT NOT NULL DEFAULT 'planned',
    post_error TEXT,
    carousel_design_project_id TEXT,
    carousel_status TEXT NOT NULL DEFAULT 'planned',
    carousel_error TEXT,
    pdf_path TEXT,
    pdf_status TEXT NOT NULL DEFAULT 'planned',
    pdf_error TEXT
);
"""
# plan_data holds one jarvis.content_studio.plan.ContentPlan per
# project (as JSON, via dataclasses.asdict()) - the plan a person saw
# and chose content types from; regenerating overwrites it (one
# "current" plan per project, not a history of past attempts, matching
# every other generation module's own brief_data/script_data
# convention in this codebase).
#
# Each content type's own *_error column holds the real, human-readable
# error message from its most recent failed creation attempt (module
# brief Stage 2 requirement 10/11: "show the real error", "do not fake
# successful creation") - cleared (set back to NULL) whenever that
# content type's own status moves to anything other than 'failed', so a
# stale error from a since-fixed retry never lingers in the UI.

_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("reel_error", "TEXT"), ("story_error", "TEXT"), ("post_error", "TEXT"),
    ("carousel_error", "TEXT"), ("pdf_error", "TEXT"),
)
# Added in this module's Stage 2 release (orchestration failure
# messages) - present in _SCHEMA's CREATE TABLE above for a brand-new
# database, and added via this idempotent ALTER TABLE path for anyone
# who already has a Stage-1 content_studio.db on disk (matching
# jarvis.reel_generator.db/jarvis.design_studio.db's own established
# pattern for columns added after a table's first release).


def _migrate(conn: sqlite3.Connection) -> None:
    if not _ADDED_COLUMNS:
        return
    existing = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    for column, column_type in _ADDED_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {column} {column_type}")  # noqa: S608 - column/type are fixed module constants, never user input


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    CONTENT_STUDIO_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CONTENT_STUDIO_DB_FILE))
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
    topic: str
    plan_data: dict[str, Any] | None
    reel_generator_project_id: str | None
    reel_status: str
    reel_error: str | None
    story_design_project_id: str | None
    story_status: str
    story_error: str | None
    post_design_project_id: str | None
    post_status: str
    post_error: str | None
    carousel_design_project_id: str | None
    carousel_status: str
    carousel_error: str | None
    pdf_path: str | None
    pdf_status: str
    pdf_error: str | None

    def status_for(self, content_type: str) -> str:
        return {
            "reel": self.reel_status, "story": self.story_status, "post": self.post_status,
            "carousel": self.carousel_status, "pdf": self.pdf_status,
        }.get(content_type, "planned")

    def error_for(self, content_type: str) -> str | None:
        return {
            "reel": self.reel_error, "story": self.story_error, "post": self.post_error,
            "carousel": self.carousel_error, "pdf": self.pdf_error,
        }.get(content_type)

    def linked_project_id_for(self, content_type: str) -> str | None:
        return {
            "reel": self.reel_generator_project_id, "story": self.story_design_project_id,
            "post": self.post_design_project_id, "carousel": self.carousel_design_project_id,
        }.get(content_type)


def create_project_record(project_id: str, topic: str) -> None:
    """Inserts a new project row right after
    jarvis.content_studio.storage.create_project() has created the
    directory structure - called separately, matching
    jarvis.reel_generator.db/jarvis.design_studio.db's own storage/db
    split. Every content type starts at 'planned', nothing linked yet."""
    with _connect() as conn:
        conn.execute("INSERT INTO projects (id, created_at, topic) VALUES (?, ?, ?)", (project_id, _now_iso(), topic))


def save_plan(project_id: str, plan_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.content_studio.plan.ContentPlan (as a
    plain dict via dataclasses.asdict()). Overwrites any previous plan
    for this project (re-generating is idempotent, not additive) -
    does NOT reset any content type's own status, since a person may
    have already approved a piece built from a similar earlier plan and
    a plan regeneration alone shouldn't silently un-approve it (only
    actually regenerating THAT content type's own project does, via
    that module's own approval-reset conventions - e.g.
    jarvis.reel_generator.db.save_script() resetting script_approved)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET plan_data = ? WHERE id = ?",
            (json.dumps(plan_data, ensure_ascii=False), project_id),
        )


_LINK_COLUMN_BY_TYPE = {
    "reel": "reel_generator_project_id", "story": "story_design_project_id",
    "post": "post_design_project_id", "carousel": "carousel_design_project_id",
}
_STATUS_COLUMN_BY_TYPE = {
    "reel": "reel_status", "story": "story_status", "post": "post_status",
    "carousel": "carousel_status", "pdf": "pdf_status",
}
_ERROR_COLUMN_BY_TYPE = {
    "reel": "reel_error", "story": "story_error", "post": "post_error",
    "carousel": "carousel_error", "pdf": "pdf_error",
}


def link_content_type_project(project_id: str, content_type: str, linked_project_id: str) -> None:
    """Records that `content_type` ("reel"/"story"/"post"/"carousel" -
    not "pdf", which has no linked project, only a file path, see
    set_pdf_path()) for this Content Studio project is backed by
    `linked_project_id` - a real jarvis.reel_generator or
    jarvis.design_studio project id (see this module's own docstring
    for why that project's own data is never duplicated here). Raises
    ValueError for "pdf" or an unknown content type - a caller error,
    not a runtime data problem."""
    column = _LINK_COLUMN_BY_TYPE.get(content_type)
    if column is None:
        raise ValueError(f"'{content_type}' has no linked-project column - use set_pdf_path() for pdf.")
    with _connect() as conn:
        conn.execute(f"UPDATE projects SET {column} = ? WHERE id = ?", (linked_project_id, project_id))  # noqa: S608 - column is looked up from a fixed dict, never user input


def set_pdf_path(project_id: str, pdf_path: str) -> None:
    """Records the path to this project's generated PDF file (module
    brief section 5: only ever called after an explicit Approve PDF
    action - enforced by the caller, not this function itself, exactly
    as jarvis.reel_generator.db.save_export_path()'s own docstring
    documents for the equivalent Reel export case)."""
    with _connect() as conn:
        conn.execute("UPDATE projects SET pdf_path = ? WHERE id = ?", (pdf_path, project_id))


def set_content_type_status(project_id: str, content_type: str, status: str) -> None:
    """Advances `content_type`'s own workflow status - must be one of
    WORKFLOW_STATES (Stage 2: planned/creating/created/approved/
    exported/failed). Raises ValueError for an unknown content_type or
    status - a caller error (a fixed, small vocabulary on both sides),
    not a runtime data problem to silently ignore. Moving to any status
    OTHER than 'failed' clears that content type's own stored error
    message (see set_content_type_failed() for setting one) - a
    successful retry must not leave a stale error behind for the UI to
    keep showing."""
    if content_type not in _CONTENT_TYPES:
        raise ValueError(f"Unknown content_type '{content_type}'.")
    if status not in WORKFLOW_STATES:
        raise ValueError(f"Unknown workflow status '{status}' - must be one of {WORKFLOW_STATES}.")
    status_column = _STATUS_COLUMN_BY_TYPE[content_type]
    error_column = _ERROR_COLUMN_BY_TYPE[content_type]
    with _connect() as conn:
        if status == "failed":
            conn.execute(f"UPDATE projects SET {status_column} = ? WHERE id = ?", (status, project_id))  # noqa: S608 - column is looked up from a fixed dict, never user input
        else:
            conn.execute(
                f"UPDATE projects SET {status_column} = ?, {error_column} = NULL WHERE id = ?",  # noqa: S608
                (status, project_id),
            )


def set_content_type_failed(project_id: str, content_type: str, error_message: str) -> None:
    """Marks `content_type` as 'failed' and records the real,
    human-readable error message (module brief Stage 2 requirement 10:
    "if creation fails, show the real error and allow retry"; requirement
    11: "do not fake successful creation"). A subsequent successful
    retry calling set_content_type_status() with any other status clears
    this message - see that function's own docstring."""
    if content_type not in _CONTENT_TYPES:
        raise ValueError(f"Unknown content_type '{content_type}'.")
    status_column = _STATUS_COLUMN_BY_TYPE[content_type]
    error_column = _ERROR_COLUMN_BY_TYPE[content_type]
    with _connect() as conn:
        conn.execute(
            f"UPDATE projects SET {status_column} = 'failed', {error_column} = ? WHERE id = ?",  # noqa: S608 - columns are looked up from fixed dicts, never user input
            (error_message, project_id),
        )


_SELECT_COLUMNS = (
    "id, created_at, topic, plan_data, "
    "reel_generator_project_id, reel_status, reel_error, "
    "story_design_project_id, story_status, story_error, "
    "post_design_project_id, post_status, post_error, "
    "carousel_design_project_id, carousel_status, carousel_error, "
    "pdf_path, pdf_status, pdf_error"
)


def _row_to_record(row: tuple) -> ProjectRecord:
    return ProjectRecord(
        id=row[0], created_at=row[1], topic=row[2],
        plan_data=json.loads(row[3]) if row[3] else None,
        reel_generator_project_id=row[4], reel_status=row[5], reel_error=row[6],
        story_design_project_id=row[7], story_status=row[8], story_error=row[9],
        post_design_project_id=row[10], post_status=row[11], post_error=row[12],
        carousel_design_project_id=row[13], carousel_status=row[14], carousel_error=row[15],
        pdf_path=row[16], pdf_status=row[17], pdf_error=row[18],
    )


def get_project(project_id: str) -> ProjectRecord | None:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM projects WHERE id = ?",  # noqa: S608 - _SELECT_COLUMNS is a fixed module constant, never user input
            (project_id,),
        ).fetchone()
    return None if row is None else _row_to_record(row)


def list_projects(limit: int = 50) -> list[ProjectRecord]:
    """Newest-first, for the module brief's "My Projects"/Content
    Library listing. Ties broken by sqlite's own implicit rowid, same
    reasoning jarvis.reel_generator.db.list_projects() documents for
    its own identical tie-break."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM projects ORDER BY created_at DESC, rowid DESC LIMIT ?",  # noqa: S608
            (limit,),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def delete_project_record(project_id: str) -> None:
    """Deletes only this table's row - does NOT touch the project's
    on-disk directory (see jarvis.content_studio.storage.delete_project()
    for that) NOR any linked jarvis.reel_generator/jarvis.design_studio
    project (those are separate projects with their own independent
    lifecycle - deleting a Content Studio "pointer" row never deletes
    the real project it pointed at)."""
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
