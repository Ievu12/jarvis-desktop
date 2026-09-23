"""Loads the optional JARVIS.md project-notes file at session start.

JARVIS.md is a plain markdown file at the project root, fully editable by
the user or by JARVIS itself via the ordinary file tools (read_file/
write_file/append_to_file/edit_file_lines) - this module only handles
reading it in and reporting whether it was found, never creates it.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_NOTES_FILENAME = "JARVIS.md"

# Keeps one badly-behaved JARVIS.md (e.g. someone pastes a huge log file
# into it) from blowing up the system prompt / context budget.
MAX_NOTES_CHARS = 20_000


class ProjectNotesResult:
    def __init__(self, content: str | None, notice: str | None = None):
        self.content = content
        self.notice = notice


def load_project_notes(project_root: Path) -> ProjectNotesResult:
    """Read JARVIS.md from project_root if it exists. Returns content=None
    if there's no file to load - this is the normal case and produces no
    notice, since JARVIS.md is opt-in and never created automatically.
    Never raises for expected conditions (missing file, unreadable,
    invalid encoding, too large) - each is reported via `notice` instead.
    """
    path = project_root / PROJECT_NOTES_FILENAME
    if not path.exists():
        return ProjectNotesResult(content=None)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return ProjectNotesResult(
            content=None,
            notice=f"Found {path} but could not read it ({e}); continuing without it.",
        )
    except UnicodeDecodeError:
        return ProjectNotesResult(
            content=None,
            notice=f"Found {path} but it is not valid UTF-8 text; continuing without it.",
        )

    if not text.strip():
        return ProjectNotesResult(content=None)

    truncated = False
    if len(text) > MAX_NOTES_CHARS:
        text = text[:MAX_NOTES_CHARS]
        truncated = True

    notice = f"Loaded project notes from {path}."
    if truncated:
        notice += f" (truncated to {MAX_NOTES_CHARS} characters)"

    return ProjectNotesResult(content=text, notice=notice)
