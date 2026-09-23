"""Filesystem tools. Every path argument goes through the sandbox gate
(jarvis.core.sandbox) before touching the OS - no exceptions."""

from __future__ import annotations

import os
from pathlib import Path

from jarvis.core.approval import confirm_outside_sandbox, confirm_side_effect
from jarvis.core.file_diff import format_unified_diff
from jarvis.core.sandbox import SandboxViolation, check, request_outside_access
from jarvis.tools.base import Tool, ToolResult


def _format_size(num_bytes: int) -> str:
    """Human-readable file size, e.g. '1.2 KB', '4.2 MB'."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover - unreachable, satisfies type checker


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read the contents of a text file. The path must be inside the "
        "JARVIS project directory unless the user explicitly approves a "
        "one-time exception. If the file is not valid UTF-8 text (e.g. an "
        "image, archive, or compiled artifact), returns size and type "
        "metadata instead of content - binary content is never returned."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read"},
        },
        "required": ["path"],
    }

    def run(self, *, path: str) -> ToolResult:
        try:
            resolved = request_outside_access(path, confirm_outside_sandbox)
        except SandboxViolation as e:
            return ToolResult(ok=False, output=f"Denied: {e}")
        # KeyboardInterrupt during the approval prompt is intentionally not
        # caught here - it propagates so the agent loop aborts the current
        # turn instead of reporting a normal "denied" tool result.

        if not resolved.exists():
            return ToolResult(ok=False, output=f"File not found: {resolved}")
        if not resolved.is_file():
            return ToolResult(ok=False, output=f"Not a file: {resolved}")

        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            size = resolved.stat().st_size
            suffix = resolved.suffix or "(no extension)"
            return ToolResult(
                ok=True,
                output=(
                    f"{resolved} is not valid UTF-8 text, so its content cannot be "
                    f"displayed. File type: {suffix}, size: {_format_size(size)} "
                    f"({size} bytes)."
                ),
            )

        return ToolResult(ok=True, output=content)


def _read_current_text_or_empty(resolved: Path) -> str | None:
    """Read a file's current text content for diffing against a proposed
    write. Returns "" if the file doesn't exist yet (a new file - diffed
    against nothing), or None if it exists but isn't valid UTF-8 text (the
    caller should fall back to a content-free summary rather than diffing
    binary data as if it were text)."""
    if not resolved.exists():
        return ""
    try:
        return resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Write text content to a file, creating or overwriting it. The path "
        "must be inside the JARVIS project directory unless the user "
        "explicitly approves a one-time exception. Always requires "
        "confirmation before writing, showing a diff against the file's "
        "current content (or a preview of the new content if it doesn't "
        "exist yet)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write"},
            "content": {"type": "string", "description": "Text content to write"},
        },
        "required": ["path", "content"],
    }

    def run(self, *, path: str, content: str) -> ToolResult:
        try:
            resolved = request_outside_access(path, confirm_outside_sandbox)
        except SandboxViolation as e:
            return ToolResult(ok=False, output=f"Denied: {e}")
        # KeyboardInterrupt during either approval prompt is intentionally
        # not caught here - see ReadFileTool.run for rationale.

        current = _read_current_text_or_empty(resolved)
        if current is None:
            diff_summary = (
                f"{resolved} exists but is not valid UTF-8 text, so its current "
                f"content can't be diffed. This write would overwrite it with "
                f"{len(content)} chars of new text content."
            )
        else:
            diff_summary = format_unified_diff(str(resolved), current, content)

        if not confirm_side_effect(diff_summary):
            return ToolResult(ok=False, output="Denied by user.")

        # Re-read immediately before writing: if the file changed between
        # the diff shown above and this point (e.g. edited by hand while
        # the approval prompt was waiting), the diff the user just
        # approved no longer describes the real change - refuse rather
        # than silently overwrite based on a stale comparison. A brand-new
        # file (current == "") has nothing to go stale against, so this
        # only applies when the file already existed.
        if current is not None and current != "":
            latest = _read_current_text_or_empty(resolved)
            if latest != current:
                return ToolResult(
                    ok=False,
                    output=(
                        f"Denied: {resolved} changed after the diff above was shown "
                        "and approved - the approval no longer matches the file's "
                        "current content. Re-read the file and try again."
                    ),
                )

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return ToolResult(ok=True, output=f"Wrote {len(content)} chars to {resolved}")


class AppendToFileTool(Tool):
    name = "append_to_file"
    description = (
        "Append text to the end of a file, without touching its existing "
        "content. Creates the file (and any missing parent directories) if "
        "it doesn't exist yet, mirroring write_file. The path must be "
        "inside the JARVIS project directory unless the user explicitly "
        "approves a one-time exception. Always requires confirmation "
        "before writing. UTF-8 text files only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to append to"},
            "content": {"type": "string", "description": "Text content to append"},
        },
        "required": ["path", "content"],
    }

    def run(self, *, path: str, content: str) -> ToolResult:
        try:
            resolved = request_outside_access(path, confirm_outside_sandbox)
        except SandboxViolation as e:
            return ToolResult(ok=False, output=f"Denied: {e}")
        # KeyboardInterrupt during either approval prompt is intentionally
        # not caught here - see ReadFileTool.run for rationale.

        current = ""
        if resolved.exists():
            if not resolved.is_file():
                return ToolResult(ok=False, output=f"Not a file: {resolved}")
            try:
                current = resolved.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return ToolResult(
                    ok=False,
                    output=f"{resolved} is not valid UTF-8 text; cannot append to it.",
                )

        diff_summary = format_unified_diff(str(resolved), current, current + content)
        if not confirm_side_effect(diff_summary):
            return ToolResult(ok=False, output="Denied by user.")

        # Re-read immediately before writing, same rationale as
        # WriteFileTool: if the file changed after the diff above was
        # shown and approved, appending now would build on stale content.
        if current != "" and resolved.exists():
            try:
                latest = resolved.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                latest = None
            if latest != current:
                return ToolResult(
                    ok=False,
                    output=(
                        f"Denied: {resolved} changed after the diff above was shown "
                        "and approved - the approval no longer matches the file's "
                        "current content. Re-read the file and try again."
                    ),
                )

        resolved.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, "a", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(ok=True, output=f"Appended {len(content)} chars to {resolved}")


# Housekeeping directories never worth walking into: virtual envs, git
# internals, caches, and JARVIS's own session/audit state.
_SKIP_DIR_NAMES = {".venv", "venv", ".git", "__pycache__", ".jarvis", ".pytest_cache", "*.egg-info"}


def _should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIR_NAMES or name.endswith(".egg-info")


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = (
        "List the files and subdirectories at a path. The path must be "
        "inside the JARVIS project directory unless the user explicitly "
        "approves a one-time exception. Read-only, no confirmation prompt. "
        "Set recursive=true to walk the full subtree instead of one level."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to list. Defaults to the project root if omitted.",
            },
            "recursive": {
                "type": "boolean",
                "description": (
                    "If true, list the full subtree (skipping housekeeping "
                    "directories like .venv, .git, __pycache__) instead of "
                    "just the immediate contents. Defaults to false."
                ),
            },
        },
        "required": [],
    }

    MAX_RECURSIVE_ENTRIES = 500

    def run(self, *, path: str = ".", recursive: bool = False) -> ToolResult:
        try:
            resolved = request_outside_access(path, confirm_outside_sandbox)
        except SandboxViolation as e:
            return ToolResult(ok=False, output=f"Denied: {e}")

        if not resolved.exists():
            return ToolResult(ok=False, output=f"Path not found: {resolved}")
        if not resolved.is_dir():
            return ToolResult(ok=False, output=f"Not a directory: {resolved}")

        if recursive:
            return self._run_recursive(resolved)

        entries = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = [self._format_entry_line(e) for e in entries]
        if not lines:
            return ToolResult(ok=True, output=f"{resolved} is empty.")
        return ToolResult(ok=True, output="\n".join(lines))

    @staticmethod
    def _format_entry_line(entry: Path) -> str:
        if entry.is_dir():
            return f"dir   {entry.name}"
        try:
            size = _format_size(entry.stat().st_size)
        except OSError:
            size = "? "
        return f"file  {entry.name}  ({size})"

    def _run_recursive(self, resolved) -> ToolResult:
        lines: list[str] = []
        truncated = False

        # os.walk's followlinks=False does NOT stop Windows junctions -
        # Path.is_symlink() returns False for them, so they'd otherwise be
        # walked into like a normal directory. Each subdirectory is
        # instead explicitly re-checked against the sandbox boundary
        # (which correctly resolves junctions via Path.resolve()) before
        # being descended into, pruning any that escape JARVIS_ROOT.
        for dirpath, dirnames, filenames in os.walk(resolved):
            dirnames[:] = sorted(
                d
                for d in dirnames
                if not _should_skip_dir(d) and check(Path(dirpath) / d).inside_sandbox
            )
            rel_dir = Path(dirpath).relative_to(resolved)

            for d in dirnames:
                rel = rel_dir / d if str(rel_dir) != "." else Path(d)
                lines.append(f"dir   {rel}")
                if len(lines) >= self.MAX_RECURSIVE_ENTRIES:
                    truncated = True
                    break
            if truncated:
                break

            for f in sorted(filenames):
                rel = rel_dir / f if str(rel_dir) != "." else Path(f)
                try:
                    size = _format_size((Path(dirpath) / f).stat().st_size)
                except OSError:
                    size = "?"
                lines.append(f"file  {rel}  ({size})")
                if len(lines) >= self.MAX_RECURSIVE_ENTRIES:
                    truncated = True
                    break
            if truncated:
                break

        if not lines:
            return ToolResult(ok=True, output=f"{resolved} is empty.")

        output = "\n".join(lines)
        if truncated:
            output += f"\n[truncated at {self.MAX_RECURSIVE_ENTRIES} entries]"
        return ToolResult(ok=True, output=output)


class DeleteFileTool(Tool):
    name = "delete_file"
    description = (
        "Delete a single file. The path must be inside the JARVIS project "
        "directory unless the user explicitly approves a one-time "
        "exception. Always requires confirmation before deleting. Does not "
        "delete directories."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to delete"},
        },
        "required": ["path"],
    }

    def run(self, *, path: str) -> ToolResult:
        try:
            resolved = request_outside_access(path, confirm_outside_sandbox)
        except SandboxViolation as e:
            return ToolResult(ok=False, output=f"Denied: {e}")
        # KeyboardInterrupt during either approval prompt is intentionally
        # not caught here - see ReadFileTool.run for rationale.

        if not resolved.exists():
            return ToolResult(ok=False, output=f"File not found: {resolved}")
        if resolved.is_dir():
            return ToolResult(
                ok=False,
                output=f"Refusing to delete a directory: {resolved}",
            )

        if not confirm_side_effect(f"delete {resolved}"):
            return ToolResult(ok=False, output="Denied by user.")

        os.remove(resolved)
        return ToolResult(ok=True, output=f"Deleted {resolved}")


class CreateDirectoryTool(Tool):
    name = "create_directory"
    description = (
        "Create a directory (including any missing parent directories). "
        "The path must be inside the JARVIS project directory unless the "
        "user explicitly approves a one-time exception. Always requires "
        "confirmation before creating."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the directory to create"},
        },
        "required": ["path"],
    }

    def run(self, *, path: str) -> ToolResult:
        try:
            resolved = request_outside_access(path, confirm_outside_sandbox)
        except SandboxViolation as e:
            return ToolResult(ok=False, output=f"Denied: {e}")
        # KeyboardInterrupt during either approval prompt is intentionally
        # not caught here - see ReadFileTool.run for rationale.

        if resolved.is_dir():
            return ToolResult(ok=True, output=f"Directory already exists: {resolved}")
        if resolved.exists():
            return ToolResult(
                ok=False,
                output=f"Refusing to create directory: a file already exists at {resolved}",
            )

        if not confirm_side_effect(f"create directory {resolved}"):
            return ToolResult(ok=False, output="Denied by user.")

        resolved.mkdir(parents=True, exist_ok=True)
        return ToolResult(ok=True, output=f"Created directory {resolved}")


class MoveFileTool(Tool):
    name = "move_file"
    description = (
        "Move or rename a single file. Both the source and destination "
        "paths must be inside the JARVIS project directory unless the user "
        "explicitly approves a one-time exception for each. Always requires "
        "confirmation before moving. Refuses if the destination already "
        "exists, or if the source is a directory."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Path to the file to move"},
            "destination": {"type": "string", "description": "New path for the file"},
        },
        "required": ["source", "destination"],
    }

    def run(self, *, source: str, destination: str) -> ToolResult:
        try:
            resolved_source = request_outside_access(source, confirm_outside_sandbox)
        except SandboxViolation as e:
            return ToolResult(ok=False, output=f"Denied: {e}")
        try:
            resolved_dest = request_outside_access(destination, confirm_outside_sandbox)
        except SandboxViolation as e:
            return ToolResult(ok=False, output=f"Denied: {e}")
        # KeyboardInterrupt during any of the approval prompts above is
        # intentionally not caught here - see ReadFileTool.run for rationale.

        if not resolved_source.exists():
            return ToolResult(ok=False, output=f"Source file not found: {resolved_source}")
        if resolved_source.is_dir():
            return ToolResult(
                ok=False,
                output=f"Refusing to move a directory: {resolved_source}",
            )
        if resolved_dest.exists():
            return ToolResult(
                ok=False,
                output=(
                    f"Refusing to move: destination already exists: {resolved_dest}. "
                    "Delete it first if you intend to replace it."
                ),
            )

        if not confirm_side_effect(f"move {resolved_source} -> {resolved_dest}"):
            return ToolResult(ok=False, output="Denied by user.")

        resolved_dest.parent.mkdir(parents=True, exist_ok=True)
        resolved_source.rename(resolved_dest)
        return ToolResult(ok=True, output=f"Moved {resolved_source} -> {resolved_dest}")


class SearchFilesTool(Tool):
    name = "search_files"
    description = (
        "Search for a plain text substring across files under a directory "
        "(recursively), returning matching file paths and line numbers. "
        "Not a regex - literal substring match only. The path must be "
        "inside the JARVIS project directory unless the user explicitly "
        "approves a one-time exception. Read-only, no confirmation prompt. "
        "Binary files and housekeeping directories (.venv, .git, "
        "__pycache__, etc.) are skipped automatically."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Literal text to search for (case-sensitive substring match).",
            },
            "path": {
                "type": "string",
                "description": "Directory to search under. Defaults to the project root.",
            },
        },
        "required": ["pattern"],
    }

    MAX_MATCHES = 200
    MAX_OUTPUT_CHARS = 8000

    def run(self, *, pattern: str, path: str = ".") -> ToolResult:
        if not pattern:
            return ToolResult(ok=False, output="Denied: pattern must not be empty.")

        try:
            resolved = request_outside_access(path, confirm_outside_sandbox)
        except SandboxViolation as e:
            return ToolResult(ok=False, output=f"Denied: {e}")

        if not resolved.exists():
            return ToolResult(ok=False, output=f"Path not found: {resolved}")
        if not resolved.is_dir():
            return ToolResult(ok=False, output=f"Not a directory: {resolved}")

        matches: list[str] = []
        truncated = False

        for dirpath, dirnames, filenames in os.walk(resolved):
            # See ListDirectoryTool._run_recursive for why junctions need
            # an explicit sandbox re-check here rather than relying on
            # os.walk's followlinks=False.
            dirnames[:] = sorted(
                d
                for d in dirnames
                if not _should_skip_dir(d) and check(Path(dirpath) / d).inside_sandbox
            )
            for filename in sorted(filenames):
                file_path = Path(dirpath) / filename
                try:
                    text = file_path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue  # binary or unreadable - skip silently

                for line_number, line in enumerate(text.splitlines(), start=1):
                    if pattern in line:
                        rel = file_path.relative_to(resolved)
                        matches.append(f"{rel}:{line_number}: {line.strip()}")
                        if len(matches) >= self.MAX_MATCHES:
                            truncated = True
                            break
                if truncated:
                    break
            if truncated:
                break

        if not matches:
            return ToolResult(ok=True, output=f"No matches for '{pattern}' under {resolved}")

        output = "\n".join(matches)
        if truncated:
            output += f"\n[truncated at {self.MAX_MATCHES} matches]"
        if len(output) > self.MAX_OUTPUT_CHARS:
            output = output[: self.MAX_OUTPUT_CHARS] + "\n[output truncated]"

        return ToolResult(ok=True, output=output)


class ReplaceInFileTool(Tool):
    name = "replace_in_file"
    description = (
        "Replace occurrences of a literal text substring within a single "
        "file. Not a regex - literal substring match only. The path must "
        "be inside the JARVIS project directory unless the user explicitly "
        "approves a one-time exception. Always requires confirmation "
        "before writing, showing how many replacements will be made. UTF-8 "
        "text files only. Reports zero replacements rather than silently "
        "succeeding if the pattern isn't found."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to modify"},
            "search": {
                "type": "string",
                "description": "Literal text to find (case-sensitive substring match).",
            },
            "replacement": {
                "type": "string",
                "description": "Text to replace each match with.",
            },
            "count": {
                "type": "integer",
                "description": (
                    "Maximum number of occurrences to replace, in order from the "
                    "start of the file. Omit to replace all occurrences."
                ),
            },
        },
        "required": ["path", "search", "replacement"],
    }

    def run(
        self,
        *,
        path: str,
        search: str,
        replacement: str,
        count: int | None = None,
    ) -> ToolResult:
        if not search:
            return ToolResult(ok=False, output="Denied: search text must not be empty.")
        if count is not None and count < 1:
            return ToolResult(ok=False, output="Denied: count must be a positive integer.")

        try:
            resolved = request_outside_access(path, confirm_outside_sandbox)
        except SandboxViolation as e:
            return ToolResult(ok=False, output=f"Denied: {e}")
        # KeyboardInterrupt during either approval prompt is intentionally
        # not caught here - see ReadFileTool.run for rationale.

        if not resolved.exists():
            return ToolResult(ok=False, output=f"File not found: {resolved}")
        if not resolved.is_file():
            return ToolResult(ok=False, output=f"Not a file: {resolved}")

        try:
            original = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                ok=False,
                output=f"{resolved} is not valid UTF-8 text; cannot perform a text replacement.",
            )

        occurrences = original.count(search)
        if occurrences == 0:
            return ToolResult(
                ok=True,
                output=f"No occurrences of the given search text found in {resolved}. Nothing changed.",
            )

        replace_limit = -1 if count is None else count
        updated = original.replace(search, replacement, replace_limit)
        actual_replacements = min(occurrences, count) if count is not None else occurrences

        diff_summary = format_unified_diff(str(resolved), original, updated)
        if not confirm_side_effect(
            f"replace {actual_replacements} occurrence(s) of the given text in {resolved}\n\n"
            f"{diff_summary}"
        ):
            return ToolResult(ok=False, output="Denied by user.")

        # Re-read immediately before writing: if the file changed after the
        # diff above was shown and approved, the replacement computed from
        # `original` may no longer make sense (the search text might have
        # moved, been removed, or the surrounding content changed) -
        # refuse rather than apply a plan based on stale content.
        try:
            latest = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            latest = None
        if latest != original:
            return ToolResult(
                ok=False,
                output=(
                    f"Denied: {resolved} changed after the diff above was shown "
                    "and approved - the approval no longer matches the file's "
                    "current content. Re-read the file and try again."
                ),
            )

        resolved.write_text(updated, encoding="utf-8")
        return ToolResult(
            ok=True,
            output=f"Replaced {actual_replacements} occurrence(s) in {resolved}.",
        )


class EditFileLinesTool(Tool):
    name = "edit_file_lines"
    description = (
        "Replace a specific range of lines in a file with new content, "
        "without retyping the rest of the file. Lines are 1-indexed and "
        "the range is inclusive (start_line=5, end_line=5 replaces just "
        "line 5; start_line=1, end_line=3 replaces lines 1 through 3). The "
        "path must be inside the JARVIS project directory unless the user "
        "explicitly approves a one-time exception. Always requires "
        "confirmation, showing the exact lines being replaced and what "
        "they become. Refuses out-of-range line numbers rather than "
        "silently clamping them. UTF-8 text files only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to edit"},
            "start_line": {
                "type": "integer",
                "description": "First line to replace, 1-indexed, inclusive.",
            },
            "end_line": {
                "type": "integer",
                "description": "Last line to replace, 1-indexed, inclusive.",
            },
            "new_content": {
                "type": "string",
                "description": (
                    "Text to replace the given line range with. Use an empty "
                    "string to delete the lines. Should end with a newline if "
                    "the replaced lines should remain separate from what follows, "
                    "matching the file's existing line-ending convention."
                ),
            },
        },
        "required": ["path", "start_line", "end_line", "new_content"],
    }

    def run(
        self,
        *,
        path: str,
        start_line: int,
        end_line: int,
        new_content: str,
    ) -> ToolResult:
        if start_line < 1:
            return ToolResult(ok=False, output="Denied: start_line must be 1 or greater.")
        if end_line < start_line:
            return ToolResult(ok=False, output="Denied: end_line must be >= start_line.")

        try:
            resolved = request_outside_access(path, confirm_outside_sandbox)
        except SandboxViolation as e:
            return ToolResult(ok=False, output=f"Denied: {e}")
        # KeyboardInterrupt during either approval prompt is intentionally
        # not caught here - see ReadFileTool.run for rationale.

        if not resolved.exists():
            return ToolResult(ok=False, output=f"File not found: {resolved}")
        if not resolved.is_file():
            return ToolResult(ok=False, output=f"Not a file: {resolved}")

        try:
            original = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                ok=False,
                output=f"{resolved} is not valid UTF-8 text; cannot edit it by line.",
            )

        # keepends=True preserves each line's exact original line-ending
        # character(s), so lines outside the replaced range are reproduced
        # byte-for-byte, not just content-for-content.
        lines = original.splitlines(keepends=True)
        total_lines = len(lines)

        if start_line > total_lines:
            return ToolResult(
                ok=False,
                output=(
                    f"Denied: start_line {start_line} is beyond the end of the file "
                    f"({total_lines} line(s) total)."
                ),
            )
        if end_line > total_lines:
            return ToolResult(
                ok=False,
                output=(
                    f"Denied: end_line {end_line} is beyond the end of the file "
                    f"({total_lines} line(s) total)."
                ),
            )

        updated_lines = lines[: start_line - 1] + [new_content] + lines[end_line:]
        updated = "".join(updated_lines)

        diff_summary = format_unified_diff(str(resolved), original, updated)
        if not confirm_side_effect(
            f"replace line(s) {start_line}-{end_line} in {resolved}\n\n{diff_summary}"
        ):
            return ToolResult(ok=False, output="Denied by user.")

        # Re-read immediately before writing, same rationale as
        # ReplaceInFileTool: a line range approved against stale content
        # could now point at entirely different lines.
        try:
            latest = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            latest = None
        if latest != original:
            return ToolResult(
                ok=False,
                output=(
                    f"Denied: {resolved} changed after the diff above was shown "
                    "and approved - the approval no longer matches the file's "
                    "current content. Re-read the file and try again."
                ),
            )

        resolved.write_text(updated, encoding="utf-8")
        return ToolResult(
            ok=True,
            output=f"Replaced line(s) {start_line}-{end_line} in {resolved}.",
        )
