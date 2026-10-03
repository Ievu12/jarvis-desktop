"""The person's favorites (⭐) and how often each template was used
(for "Populiariausi"), kept in the carousel studio's SQLite file next to
the project index."""

from __future__ import annotations

from datetime import datetime, timezone

from jarvis.carousel_studio import storage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS carousel_template_usage (
    template_id TEXT PRIMARY KEY,
    uses INTEGER NOT NULL DEFAULT 0,
    favorite INTEGER NOT NULL DEFAULT 0,
    last_used TEXT NOT NULL DEFAULT ''
);
"""


def _connect():
    connection = storage._connect()
    return connection


def favorites() -> set[str]:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        return {row[0] for row in conn.execute("SELECT template_id FROM carousel_template_usage WHERE favorite = 1")}


def uses() -> dict[str, int]:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        return dict(conn.execute("SELECT template_id, uses FROM carousel_template_usage WHERE uses > 0").fetchall())


def set_favorite(template_id: str, favorite: bool) -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO carousel_template_usage (template_id) VALUES (?)", (template_id,))
        conn.execute("UPDATE carousel_template_usage SET favorite = ? WHERE template_id = ?", (int(favorite), template_id))


def toggle_favorite(template_id: str) -> bool:
    now = template_id not in favorites()
    set_favorite(template_id, now)
    return now


def record_use(template_id: str) -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO carousel_template_usage (template_id) VALUES (?)", (template_id,))
        conn.execute(
            "UPDATE carousel_template_usage SET uses = uses + 1, last_used = ? WHERE template_id = ?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), template_id),
        )
