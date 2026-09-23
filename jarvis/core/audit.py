"""Append-only audit log for every approval decision (sandboxed writes,
out-of-sandbox requests, denials). Kept separate from session state so it
can't be edited away by normal conversation flow.

Rotates by file size so the file (and history_view's read of it) stays
bounded even across weeks/months of use, without needing to read the file
to check - os.path.getsize() is O(1), unlike counting lines. Rotation only
ever moves old entries aside into a timestamped archive - it never deletes
them. Deciding to prune old archives is a deliberate choice left to the
user, not something done silently here.
"""

from __future__ import annotations

import json
import os
import time

from jarvis.config import AUDIT_LOG_FILE
from jarvis.core.secrets import redact_secret

# ~10,000 short JSON-lines entries at roughly 200 bytes each. Approximate
# by design - this is a bound to keep the file manageable, not an exact
# entry-count guarantee.
MAX_BYTES_BEFORE_ROTATION = 2_000_000


def _rotate_if_needed() -> None:
    try:
        size = os.path.getsize(AUDIT_LOG_FILE)
    except OSError:
        return  # file doesn't exist yet - nothing to rotate
    if size < MAX_BYTES_BEFORE_ROTATION:
        return

    timestamp = time.strftime("%Y%m%dT%H%M%S")
    archive_path = AUDIT_LOG_FILE.parent / f"audit.log.{timestamp}.jsonl"
    # Extremely unlikely but possible within the same second under heavy
    # write volume - fall back to a counter suffix rather than overwrite
    # an existing archive.
    suffix = 1
    while archive_path.exists():
        archive_path = AUDIT_LOG_FILE.parent / f"audit.log.{timestamp}-{suffix}.jsonl"
        suffix += 1

    AUDIT_LOG_FILE.rename(archive_path)


def log_event(event_type: str, **fields) -> None:
    AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed()

    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "event": event_type,
        **fields,
    }
    line = json.dumps(entry, default=str)
    # Defense in depth: scrub the API key even though no known field here
    # should ever carry it - this only guards against a future field
    # accidentally including secret-bearing text.
    line = redact_secret(line)
    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
