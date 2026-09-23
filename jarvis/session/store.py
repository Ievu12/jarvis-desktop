"""Simple JSON-backed session persistence: conversation history only.
Approval/denial audit trail lives separately in jarvis.core.audit."""

from __future__ import annotations

import json
from typing import Any

from jarvis.config import SESSION_FILE


class SessionLoadResult:
    """Wraps load_history()'s outcome so a recovered-from-corruption case
    is distinguishable from a normal empty/missing session, without
    changing the return type callers already depend on (history is still
    a plain list - see .history)."""

    def __init__(self, history: list[dict[str, Any]], warning: str | None = None):
        self.history = history
        self.warning = warning


def load_history() -> SessionLoadResult:
    if not SESSION_FILE.exists():
        return SessionLoadResult([])

    try:
        raw_text = SESSION_FILE.read_text(encoding="utf-8")
    except OSError as e:
        return SessionLoadResult(
            [], warning=f"Could not read {SESSION_FILE} ({e}); starting a new session."
        )

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return SessionLoadResult(
            [],
            warning=(
                f"Session file at {SESSION_FILE} is corrupted (invalid JSON) and could "
                "not be loaded. Previous conversation history has been lost; starting "
                "a new session. The corrupted file was left in place for inspection."
            ),
        )

    if not isinstance(data, list):
        return SessionLoadResult(
            [],
            warning=(
                f"Session file at {SESSION_FILE} has an unexpected format (expected a "
                "list of turns) and could not be loaded. Starting a new session."
            ),
        )

    return SessionLoadResult(data)


def save_history(history: list[dict[str, Any]]) -> None:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")
