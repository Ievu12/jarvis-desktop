"""SQLite storage for the Instagram AI Manager module: all
LLM-generated content (Reel ideas, hooks, captions, CTAs, hashtag sets,
Story sequences, weekly plans, AI recommendations) that
jarvis.instagram_ai_manager.ai_services produces. A single local file
(jarvis.config.INSTAGRAM_AI_MANAGER_DB_FILE), using only the stdlib
sqlite3 module - no new dependency.

Deliberately separate from jarvis.integrations.instagram_history (which
holds REAL Instagram performance data fetched from the Graph API) - this
module holds JARVIS/LLM-generated DRAFT content the person hasn't
necessarily posted anywhere. Nothing here is ever synced to Instagram or
read back into a prompt as if it were real performance data.

Every table has an integer primary key, a `created_at` ISO-8601 UTC
timestamp, and a `topic`/`niche` text column so the UI's date-range and
keyword filtering (per the module's own brief) can be plain SQL WHERE
clauses rather than hand-rolled JSON scanning. Generated content itself
is stored as a JSON blob in a `data` column (its shape varies per
content type and is documented by jarvis.instagram_ai_manager
.ai_services, which is the only writer) rather than one column per
field - this keeps the schema stable even as ai_services' prompts/
output shapes evolve, without a migration for every field added.

Connections are opened per-call (sqlite3 handles this cheaply and
safely for a single-process desktop app) rather than held open for the
GUI's lifetime, so a call from a background worker thread
(jarvis.gui.worker) never shares a connection object across threads -
sqlite3 connections are not thread-safe to share without
check_same_thread=False, which this module deliberately does not use.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from jarvis.config import INSTAGRAM_AI_MANAGER_DB_FILE

# One table per content type the module's brief lists (section 9) -
# reel_ideas is a set (10 ideas generated together) stored as one row
# with a JSON array in `data`, not one row per idea, since they are
# always generated/regenerated/saved as a batch (see ai_services
# .generate_reel_ideas()'s docstring) - the UI's [Save]/[Regenerate]
# buttons act on the whole batch, matching the brief's own wording.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS reel_idea_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hook_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS captions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    tone TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cta_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hashtag_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS story_sequences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    week_start_date TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    period_label TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    INSTAGRAM_AI_MANAGER_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(INSTAGRAM_AI_MANAGER_DB_FILE))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


@dataclass(frozen=True)
class SavedRecord:
    """One saved row, generic across every table here - `data` is
    already json.loads()'d into a plain dict/list, ready for a caller
    (a view or ai_services) to use without knowing this module's
    on-disk JSON-blob storage detail."""

    id: int
    created_at: str
    data: Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _insert(table: str, columns: dict[str, Any]) -> int:
    keys = ["created_at", *columns.keys()]
    values = [_now_iso(), *columns.values()]
    placeholders = ", ".join("?" for _ in keys)
    with _connect() as conn:
        cursor = conn.execute(
            f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})",  # noqa: S608 - table name is one of this module's own fixed constants below, never user input
            values,
        )
        return int(cursor.lastrowid)  # type: ignore[arg-type]


def _list_recent(table: str, *, limit: int) -> list[SavedRecord]:
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, created_at, data FROM {table} ORDER BY id DESC LIMIT ?",  # noqa: S608
            (limit,),
        ).fetchall()
    return [SavedRecord(id=r[0], created_at=r[1], data=json.loads(r[2])) for r in rows]


def _get(table: str, record_id: int) -> SavedRecord | None:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT id, created_at, data FROM {table} WHERE id = ?",  # noqa: S608
            (record_id,),
        ).fetchone()
    if row is None:
        return None
    return SavedRecord(id=row[0], created_at=row[1], data=json.loads(row[2]))


# --- reel_idea_sets ------------------------------------------------------------------


def save_reel_idea_set(topic: str, ideas: list[dict]) -> int:
    return _insert("reel_idea_sets", {"topic": topic, "data": json.dumps(ideas, ensure_ascii=False)})


def list_reel_idea_sets(limit: int = 20) -> list[SavedRecord]:
    return _list_recent("reel_idea_sets", limit=limit)


# --- hook_sets -----------------------------------------------------------------------


def save_hook_set(topic: str, hooks: dict) -> int:
    return _insert("hook_sets", {"topic": topic, "data": json.dumps(hooks, ensure_ascii=False)})


def list_hook_sets(limit: int = 20) -> list[SavedRecord]:
    return _list_recent("hook_sets", limit=limit)


# --- captions ---------------------------------------------------------------------------


def save_caption(topic: str, tone: str, caption: dict) -> int:
    return _insert(
        "captions", {"topic": topic, "tone": tone, "data": json.dumps(caption, ensure_ascii=False)}
    )


def list_captions(limit: int = 20) -> list[SavedRecord]:
    return _list_recent("captions", limit=limit)


# --- cta_sets ---------------------------------------------------------------------------


def save_cta_set(topic: str, ctas: dict) -> int:
    return _insert("cta_sets", {"topic": topic, "data": json.dumps(ctas, ensure_ascii=False)})


def list_cta_sets(limit: int = 20) -> list[SavedRecord]:
    return _list_recent("cta_sets", limit=limit)


# --- hashtag_sets -----------------------------------------------------------------------


def save_hashtag_set(topic: str, hashtags: dict) -> int:
    return _insert("hashtag_sets", {"topic": topic, "data": json.dumps(hashtags, ensure_ascii=False)})


def list_hashtag_sets(limit: int = 20) -> list[SavedRecord]:
    return _list_recent("hashtag_sets", limit=limit)


# --- story_sequences ----------------------------------------------------------------------


def save_story_sequence(topic: str, sequence: list[dict]) -> int:
    return _insert(
        "story_sequences", {"topic": topic, "data": json.dumps(sequence, ensure_ascii=False)}
    )


def list_story_sequences(limit: int = 20) -> list[SavedRecord]:
    return _list_recent("story_sequences", limit=limit)


# --- weekly_plans -------------------------------------------------------------------------


def save_weekly_plan(week_start_date: str, plan: dict) -> int:
    return _insert(
        "weekly_plans",
        {"week_start_date": week_start_date, "data": json.dumps(plan, ensure_ascii=False)},
    )


def list_weekly_plans(limit: int = 20) -> list[SavedRecord]:
    return _list_recent("weekly_plans", limit=limit)


# --- recommendations ----------------------------------------------------------------------


def save_recommendations(period_label: str, recommendations: dict) -> int:
    return _insert(
        "recommendations",
        {"period_label": period_label, "data": json.dumps(recommendations, ensure_ascii=False)},
    )


def list_recommendations(limit: int = 20) -> list[SavedRecord]:
    return _list_recent("recommendations", limit=limit)
