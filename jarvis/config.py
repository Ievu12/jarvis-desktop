"""Central configuration. Resolves JARVIS_ROOT once, at import time, so every
other module sees the same canonical boundary."""

import os
from pathlib import Path

# The project directory itself (parent of the jarvis/ package) is the default
# sandbox root. Override with the JARVIS_ROOT env var if needed.
_DEFAULT_ROOT = Path(__file__).resolve().parent.parent

JARVIS_ROOT: Path = Path(
    os.environ.get("JARVIS_ROOT", str(_DEFAULT_ROOT))
).resolve(strict=True)

ANTHROPIC_API_KEY: str | None = os.environ.get("ANTHROPIC_API_KEY")
"""Loaded once from the environment. Never write this value to a file,
log, or print it directly - use jarvis.core.secrets.mask_secret() for any
display purpose."""

AZURE_SPEECH_KEY: str | None = os.environ.get("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION: str | None = os.environ.get("AZURE_SPEECH_REGION")
"""Optional Azure Cognitive Services Speech credentials, used only by
jarvis.voice.text_to_speech as a fallback Lithuanian voice when no
Lithuanian SAPI voice is installed in Windows (see that module's
docstring). A service credential, not a per-user OAuth token, so it
follows ANTHROPIC_API_KEY's pattern (plain environment variable, not
jarvis.integrations.oauth.TokenStore) rather than the OAuth connectors'
pattern. Voice mode works without these set - speak() falls back to a
clear "install a Lithuanian voice" message instead of Azure. Never write
either value to a file, log, or print it directly - use
jarvis.core.secrets.mask_secret() for any display purpose."""

SESSION_FILE: Path = JARVIS_ROOT / ".jarvis" / "session.json"
AUDIT_LOG_FILE: Path = JARVIS_ROOT / ".jarvis" / "audit.log"
TASKS_FILE: Path = JARVIS_ROOT / ".jarvis" / "tasks.json"
INSTAGRAM_INSIGHTS_HISTORY_FILE: Path = JARVIS_ROOT / ".jarvis" / "instagram_insights_history.json"
"""One JSON object per stored calendar date (see
jarvis.integrations.instagram_history) - JARVIS's own local record of
daily Instagram Insights snapshots, kept because Meta's API does not
retain account-level insights indefinitely. Contains no OAuth token or
other credential - only numeric metrics and dates."""

CONTENT_TOPICS_HISTORY_FILE: Path = JARVIS_ROOT / ".jarvis" / "content_topics_history.json"
"""One JSON object per stored calendar date (see jarvis.content_manager)
- a local record of the content topics/ideas jarvis.morning_routine has
already suggested, kept so the next morning briefing can avoid repeating
recent topics. Contains no credential - only dates and short topic
strings."""
