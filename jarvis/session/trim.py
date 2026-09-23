"""Session history trimming, so long-running sessions can't grow past the
model's context window.

History is a flat list of {"role", "content"} turns. The one hard
constraint on trimming: an assistant turn containing tool_use blocks must
stay immediately followed by the user turn carrying the matching
tool_result blocks - the Anthropic API rejects a request where that
pairing is broken. So trimming always removes whole logical turns from
the oldest end, never splits a tool_use from its tool_result.
"""

from __future__ import annotations

import json
from typing import Any

# Rough proxy for token count - good enough for a soft budget, not billing
# accuracy. ~4 chars/token is the standard rule of thumb for English text.
CHARS_PER_TOKEN_ESTIMATE = 4

# Leaves generous headroom under typical 200k-token context windows while
# still keeping a meaningful amount of conversation.
MAX_HISTORY_CHARS = 120_000 * CHARS_PER_TOKEN_ESTIMATE

TRIM_MARKER_PREFIX = "[JARVIS session note] "


def _turn_chars(turn: dict[str, Any]) -> int:
    return len(json.dumps(turn, default=str))


def _is_tool_use_message(turn: dict[str, Any]) -> bool:
    if turn.get("role") != "assistant":
        return False
    content = turn.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)


def _logical_turn_boundaries(history: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Group history into logical turns: a lone message, or an
    assistant tool_use message paired with its following tool_result
    message. Returns a list of (start, end) index ranges, end exclusive.
    """
    boundaries: list[tuple[int, int]] = []
    i = 0
    n = len(history)
    while i < n:
        if _is_tool_use_message(history[i]) and i + 1 < n:
            boundaries.append((i, i + 2))
            i += 2
        else:
            boundaries.append((i, i + 1))
            i += 1
    return boundaries


def trim_history(
    history: list[dict[str, Any]],
    *,
    max_chars: int = MAX_HISTORY_CHARS,
) -> list[dict[str, Any]]:
    """Return a trimmed copy of history that fits within max_chars,
    dropping whole logical turns from the oldest end and keeping the most
    recent context intact. Never mutates the input list.

    If any trimming occurred, a single synthetic marker turn is inserted
    at the front recording how many turns were dropped, so the truncation
    is visible rather than silent.
    """
    if not history:
        return []

    total = sum(_turn_chars(t) for t in history)
    if total <= max_chars:
        return list(history)

    boundaries = _logical_turn_boundaries(history)

    # Reserve space for the marker turn up front (its text length varies
    # only by the digit count of dropped_count, which is bounded for any
    # realistic history size) so the final result - turns plus marker -
    # actually fits within max_chars, not just the turns alone.
    marker_budget = _turn_chars(_build_marker(len(history)))
    effective_budget = max(max_chars - marker_budget, 0)

    # Walk from the newest logical turn backward, keeping turns until the
    # budget would be exceeded, then drop everything older.
    kept_from = len(boundaries)
    running = 0
    for idx in range(len(boundaries) - 1, -1, -1):
        start, end = boundaries[idx]
        turn_size = sum(_turn_chars(history[i]) for i in range(start, end))
        if running + turn_size > effective_budget and kept_from != len(boundaries):
            # Always keep at least the single most recent logical turn,
            # even if it alone exceeds the budget - trimming further
            # would remove the turn currently being responded to.
            break
        running += turn_size
        kept_from = start

    dropped_count = kept_from
    if dropped_count == 0:
        return list(history)

    return [_build_marker(dropped_count)] + history[kept_from:]


def _build_marker(dropped_count: int) -> dict[str, Any]:
    return {
        "role": "user",
        "content": (
            f"{TRIM_MARKER_PREFIX}{dropped_count} earlier message(s) were "
            "removed from this session to stay within the context limit. "
            "Recent conversation continues below."
        ),
    }
