"""Local, JSON-backed storage for the desktop GUI's update preferences
(the three checkboxes on the Settings screen - see jarvis.gui.app),
implemented identically to jarvis.integrations.instagram_history and
jarvis.content_manager's local history files: a plain JSON file under
jarvis.config.JARVIS_ROOT/.jarvis/, corruption reported rather than
crashing, no credential ever stored here.

Three independent booleans, matching the person's requested Settings
screen:
  - check_for_updates: look for a newer release on startup.
  - download_updates: automatically download (not install) a detected
    update.
  - install_updates: automatically install a downloaded update without
    asking first - defaults to False and is expected to stay False for
    most people, since jarvis.gui.updater.install_update() replaces the
    running program's files; jarvis.gui.app always asks for explicit
    confirmation before installing regardless of this setting UNLESS it
    is explicitly turned on.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from jarvis.config import UPDATE_SETTINGS_FILE


@dataclass
class UpdateSettings:
    check_for_updates: bool = True
    download_updates: bool = True
    install_updates: bool = False


def load_update_settings() -> UpdateSettings:
    """Never raises - a missing or corrupted file falls back to the
    documented defaults (check + download on, install off) rather than
    blocking the GUI from starting."""
    if not UPDATE_SETTINGS_FILE.exists():
        return UpdateSettings()

    try:
        raw_text = UPDATE_SETTINGS_FILE.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except (OSError, json.JSONDecodeError):
        return UpdateSettings()

    if not isinstance(data, dict):
        return UpdateSettings()

    defaults = UpdateSettings()
    return UpdateSettings(
        check_for_updates=bool(data.get("check_for_updates", defaults.check_for_updates)),
        download_updates=bool(data.get("download_updates", defaults.download_updates)),
        install_updates=bool(data.get("install_updates", defaults.install_updates)),
    )


def save_update_settings(settings: UpdateSettings) -> None:
    UPDATE_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_SETTINGS_FILE.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
