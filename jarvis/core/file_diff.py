"""Formats a unified diff between a file's old and new content, for
showing the user exactly what a write would change before they approve
it. Pure and read-only: never touches the filesystem itself, never
computes anything beyond the two strings it's given.

Used by every file-writing tool (write_file, append_to_file,
replace_in_file, edit_file_lines) so the approval prompt always shows the
same, standard diff format - not each tool's own ad hoc "write N chars"/
"replace N occurrences" summary, which told the user nothing about what
the content actually became.
"""

from __future__ import annotations

import difflib

# A brand-new file (old == "") is shown as a fixed line count rather than
# a full unified diff - difflib would mark every line as an addition
# anyway, and for a large new file that's a wall of "+" lines with no
# useful "before" to compare against. Existing-file edits always get the
# real diff.
_NEW_FILE_LINE_PREVIEW_LIMIT = 20

# A diff can still be huge for a large file with pervasive changes -
# capped so the approval prompt (and the audit log summary built from it)
# stays readable rather than dumping megabytes of text to the terminal.
MAX_DIFF_CHARS = 4000


def format_unified_diff(path: str, old_content: str, new_content: str) -> str:
    """Return a human-readable summary of what changed between
    old_content and new_content for the file at `path`. Never raises -
    this only ever runs on strings already in memory.
    """
    if old_content == new_content:
        return f"No changes to {path} - new content is identical to current content."

    if old_content == "":
        lines = new_content.splitlines()
        preview = "\n".join(lines[:_NEW_FILE_LINE_PREVIEW_LIMIT])
        header = f"New file {path} ({len(lines)} line(s)):"
        if len(lines) > _NEW_FILE_LINE_PREVIEW_LIMIT:
            preview += f"\n... ({len(lines) - _NEW_FILE_LINE_PREVIEW_LIMIT} more line(s))"
        return f"{header}\n{preview}" if preview else header

    diff_lines = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"{path} (current)",
        tofile=f"{path} (proposed)",
    )
    diff_text = "".join(diff_lines)

    if not diff_text:
        # splitlines()-level equality can differ from string equality only
        # in edge cases (e.g. trailing-newline-only differences) - treat it
        # the same as the identical-content case above rather than
        # returning an empty diff block.
        return f"No changes to {path} - new content is identical to current content."

    if len(diff_text) > MAX_DIFF_CHARS:
        diff_text = diff_text[:MAX_DIFF_CHARS] + f"\n... [diff truncated at {MAX_DIFF_CHARS} chars]"

    return f"Diff for {path}:\n{diff_text}"
