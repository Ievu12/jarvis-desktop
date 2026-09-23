"""Central configuration. Resolves JARVIS_ROOT once, at import time, so every
other module sees the same canonical boundary."""

import os
import sys
from pathlib import Path

# The project directory itself (parent of the jarvis/ package) is the
# default sandbox root when running from source (jarvis/cli/main.py,
# 'python -m jarvis...', pytest, etc.) - __file__ here is a real file on
# disk in that case, and this is the root jarvis.core.sandbox enforces
# for file tools (read_file/write_file/etc.) - unrelated to where
# personal data lives (see JARVIS_DATA_DIR below).
#
# When running as a PyInstaller-packaged JARVIS.exe (sys.frozen is set
# by PyInstaller's bootloader - see jarvis.spec/jarvis_launcher.py),
# __file__ instead resolves to a path INSIDE the bundle's _internal/
# directory - not a meaningful "project directory" to sandbox file
# tools to, but harmless here since JARVIS_ROOT is not used to decide
# where personal data goes (see JARVIS_DATA_DIR).
if getattr(sys, "frozen", False):
    _DEFAULT_ROOT = Path(sys.executable).resolve().parent
else:
    _DEFAULT_ROOT = Path(__file__).resolve().parent.parent

JARVIS_ROOT: Path = Path(
    os.environ.get("JARVIS_ROOT", str(_DEFAULT_ROOT))
).resolve(strict=True)

# Where the .jarvis/ personal-data directory (session history, Instagram
# data, update settings, audit log) actually lives - DELIBERATELY NOT
# always JARVIS_ROOT, for one critical reason: in a PyInstaller-packaged
# JARVIS.exe, JARVIS_ROOT resolves to the exe's own directory (dist/
# JARVIS/), and jarvis.gui.updater.install_update() replaces that
# directory's ENTIRE contents on every update (see its own docstring).
# If .jarvis/ lived there, every single update would destroy the
# person's conversation history, Instagram data, and settings - exactly
# what the auto-update system exists to prevent (see RELEASE.md).
#
# So when frozen, personal data lives in the standard per-user Windows
# app-data location (%LOCALAPPDATA%\JARVIS\.jarvis\), completely outside
# any directory an update ever touches, and survives an update, a
# reinstall to a different folder, or even deleting and re-extracting
# dist/JARVIS/ entirely. When running from source, it stays exactly
# where it has always been (JARVIS_ROOT/.jarvis) - unchanged behavior,
# so existing local data for anyone running from source is untouched by
# this distinction.
if getattr(sys, "frozen", False):
    _default_data_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "JARVIS"
else:
    _default_data_dir = JARVIS_ROOT

JARVIS_DATA_DIR: Path = Path(os.environ.get("JARVIS_DATA_DIR", str(_default_data_dir)))

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

SESSION_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "session.json"
AUDIT_LOG_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "audit.log"
TASKS_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "tasks.json"
INSTAGRAM_INSIGHTS_HISTORY_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "instagram_insights_history.json"
"""One JSON object per stored calendar date (see
jarvis.integrations.instagram_history) - JARVIS's own local record of
daily Instagram Insights snapshots, kept because Meta's API does not
retain account-level insights indefinitely. Contains no OAuth token or
other credential - only numeric metrics and dates."""

CONTENT_TOPICS_HISTORY_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "content_topics_history.json"
"""One JSON object per stored calendar date (see jarvis.content_manager)
- a local record of the content topics/ideas jarvis.morning_routine has
already suggested, kept so the next morning briefing can avoid repeating
recent topics. Contains no credential - only dates and short topic
strings."""

UPDATE_SETTINGS_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "update_settings.json"
"""The desktop GUI's auto-update preferences (see
jarvis.gui.settings_store) - three booleans (check/download/install),
no credential."""
