"""Local, JSON-backed storage for daily Instagram Insights snapshots -
JARVIS's own historical record, kept because Meta's Graph API does not
retain account-level insights indefinitely (in practice, only a recent
rolling window). This module has no knowledge of the Graph API, OAuth,
or access tokens at all - it only reads and writes a plain JSON file
(jarvis.config.INSTAGRAM_INSIGHTS_HISTORY_FILE), exactly the shape
jarvis.session.store uses for conversation history. It stores metrics
and dates only - nothing that could be mistaken for a credential.

jarvis.integrations.connectors.instagram.InstagramConnector is the only
caller: its record_daily_snapshot action reads today's real numbers from
the Instagram API and calls upsert_snapshot() here to persist them; its
compare_history action calls load_history() to read back what has
actually been recorded, never re-deriving or guessing a day's numbers
that were never stored. A metric missing from a given day (e.g.
profile_views not returned by the API that day) is simply absent from
that day's dict here - never invented, never filled with a placeholder
like 0.

One JSON object, keyed by ISO date string ("YYYY-MM-DD"), each value a
flat dict of whatever metrics were actually available that day. Writing
the same date twice overwrites that date's entry (upsert, not append) -
running record_daily_snapshot more than once on the same day is safe and
idempotent, matching how jarvis.session.store.save_history() always
writes the full current state rather than appending a log.
"""

from __future__ import annotations

import json
from typing import Any

from jarvis.config import INSTAGRAM_INSIGHTS_HISTORY_FILE

# The metrics this module knows how to store - matches exactly what
# InstagramConnector.record_daily_snapshot is able to read from the API
# (see that action's docstring). Not enforced here (this module stores
# whatever dict it's given), but documented so a caller doesn't invent a
# new key name that compare_history's callers won't recognize.
KNOWN_METRICS = (
    "reach", "likes", "comments", "shares", "saved",
    "total_interactions", "profile_views",
)


class HistoryLoadResult:
    """Wraps load_history()'s outcome so a recovered-from-corruption case
    is distinguishable from a normal empty/missing file, without changing
    the return type callers already depend on (entries is still a plain
    dict - see .entries). Mirrors jarvis.session.store.SessionLoadResult.
    """

    def __init__(self, entries: dict[str, dict[str, Any]], warning: str | None = None):
        self.entries = entries
        self.warning = warning


def load_history() -> HistoryLoadResult:
    """Load the full stored history as {date_string: {metric: value}}.
    Never raises - a missing file is a normal empty history, and a
    corrupted or wrongly-shaped file is reported via .warning rather than
    losing silently or crashing the caller, exactly like
    jarvis.session.store.load_history()."""
    if not INSTAGRAM_INSIGHTS_HISTORY_FILE.exists():
        return HistoryLoadResult({})

    try:
        raw_text = INSTAGRAM_INSIGHTS_HISTORY_FILE.read_text(encoding="utf-8")
    except OSError as e:
        return HistoryLoadResult(
            {},
            warning=(
                f"Could not read {INSTAGRAM_INSIGHTS_HISTORY_FILE} ({e}); "
                "treating Instagram Insights history as empty."
            ),
        )

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return HistoryLoadResult(
            {},
            warning=(
                f"Instagram Insights history file at {INSTAGRAM_INSIGHTS_HISTORY_FILE} "
                "is corrupted (invalid JSON) and could not be loaded. Previously "
                "recorded snapshots are unavailable for comparison; the corrupted "
                "file was left in place for inspection. New snapshots can still be "
                "recorded going forward."
            ),
        )

    if not isinstance(data, dict):
        return HistoryLoadResult(
            {},
            warning=(
                f"Instagram Insights history file at {INSTAGRAM_INSIGHTS_HISTORY_FILE} "
                "has an unexpected format (expected a date -> metrics mapping) and "
                "could not be loaded."
            ),
        )

    # Defensive: only keep entries that are themselves dicts, so one
    # malformed entry (e.g. from manual editing) doesn't poison every
    # date's lookup for the caller.
    cleaned = {k: v for k, v in data.items() if isinstance(v, dict)}
    return HistoryLoadResult(cleaned)


def _save_history(entries: dict[str, dict[str, Any]]) -> None:
    INSTAGRAM_INSIGHTS_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTAGRAM_INSIGHTS_HISTORY_FILE.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")


def upsert_snapshot(date_str: str, metrics: dict[str, float]) -> None:
    """Store (or overwrite) one date's metrics. Only the keys present in
    `metrics` are stored for that date - a metric the caller doesn't pass
    (because the API didn't return it that day) is simply absent, never
    defaulted to 0 or null. Overwriting the same date is intentional and
    safe (idempotent re-recording), not an error."""
    result = load_history()
    entries = result.entries
    entries[date_str] = dict(metrics)
    _save_history(entries)


def get_snapshot(date_str: str) -> dict[str, Any] | None:
    """The stored metrics for one date, or None if nothing was ever
    recorded for it - distinct from a metric within a recorded date being
    absent (see upsert_snapshot)."""
    return load_history().entries.get(date_str)
