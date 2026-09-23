"""Formats a combined git status + diff summary for the CLI's 'review'
command: a single, human-readable view of everything changed in the
project right now, before deciding whether to commit it. Read-only -
never writes to the repository. Runs git directly (not through the
approval-gated shell tool) since this is a human-initiated CLI
convenience, not a model action - there is nothing here for the user to
approve, they are the one asking to see it.
"""

from __future__ import annotations

import subprocess

from jarvis.config import JARVIS_ROOT

_GIT_TIMEOUT_SECONDS = 15


def _run_git(args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=JARVIS_ROOT,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return False, "git is not installed or not on PATH."
    except subprocess.TimeoutExpired:
        return False, f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SECONDS}s."
    except OSError as e:
        return False, f"Failed to run git: {e}"

    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "git command failed.").strip()

    return True, result.stdout


class ChangeSummary:
    """A lightweight, one-line-friendly summary of uncommitted changes,
    for callers (like the 'status' command) that just need a headline
    rather than the full review. is_repo=False means JARVIS_ROOT isn't a
    git repository at all; changed_files is None when the status check
    itself failed (git missing, timeout, etc.) - distinct from a
    successful check that found zero changes (changed_files == 0)."""

    def __init__(self, is_repo: bool, changed_files: int | None, error: str | None = None):
        self.is_repo = is_repo
        self.changed_files = changed_files
        self.error = error


def get_change_summary() -> ChangeSummary:
    """Cheap check of how many files have uncommitted changes, without
    computing a diff. Reuses the same git invocation as format_review()'s
    status step - no second, divergent way of checking."""
    git_dir = JARVIS_ROOT / ".git"
    if not git_dir.exists():
        return ChangeSummary(is_repo=False, changed_files=None)

    status_ok, status_output = _run_git(["status", "--short"])
    if not status_ok:
        return ChangeSummary(is_repo=True, changed_files=None, error=status_output)

    changed_lines = [line for line in status_output.splitlines() if line.strip()]
    return ChangeSummary(is_repo=True, changed_files=len(changed_lines))


def get_changed_file_names() -> list[str] | None:
    """Return the list of changed file paths (from `git status --short`),
    or None if they can't be determined (not a repo, or the status check
    failed). Reuses the same git invocation as get_change_summary() /
    format_review() - no second, divergent way of checking. Each entry is
    the path portion of a `git status --short` line (the two-character
    status code and following space stripped)."""
    git_dir = JARVIS_ROOT / ".git"
    if not git_dir.exists():
        return None

    status_ok, status_output = _run_git(["status", "--short"])
    if not status_ok:
        return None

    names: list[str] = []
    for line in status_output.splitlines():
        if not line.strip():
            continue
        names.append(line[3:].strip() if len(line) > 3 else line.strip())
    return names


def get_staged_and_unstaged_file_names() -> tuple[list[str], list[str]] | None:
    """Return (staged, unstaged) changed file name lists, derived from the
    two status-code columns of `git status --short` porcelain output (the
    first column is the index/staged state, the second is the worktree/
    unstaged state - see `git help status`). A file with changes in both
    columns (e.g. staged then further modified) appears in both lists.
    Untracked files ('??') are reported as unstaged only - they are not
    yet staged. Returns None if this can't be determined (not a repo, or
    the status check failed). Reuses the same git invocation as
    get_change_summary() / get_changed_file_names() - no second,
    divergent way of checking.
    """
    git_dir = JARVIS_ROOT / ".git"
    if not git_dir.exists():
        return None

    status_ok, status_output = _run_git(["status", "--short"])
    if not status_ok:
        return None

    staged: list[str] = []
    unstaged: list[str] = []
    for line in status_output.splitlines():
        if not line.strip() or len(line) < 4:
            continue
        index_code, worktree_code, name = line[0], line[1], line[3:].strip()
        if index_code == "?" and worktree_code == "?":
            unstaged.append(name)
            continue
        if index_code != " ":
            staged.append(name)
        if worktree_code != " ":
            unstaged.append(name)

    return staged, unstaged


def get_staged_diff() -> str | None:
    """Return the diff of currently staged changes (`git diff --cached`),
    or None if it can't be determined (not a repo, or the diff command
    failed - e.g. no commits yet, so there's no HEAD to diff against).
    An empty string is a valid, distinct result: nothing is staged.
    Read-only - reuses the same git invocation pattern as the rest of this
    module. Used to give an LLM-based commit message suggestion real
    content to work from, not just a list of file names.
    """
    git_dir = JARVIS_ROOT / ".git"
    if not git_dir.exists():
        return None

    diff_ok, diff_output = _run_git(["diff", "--cached"])
    if not diff_ok:
        return None
    return diff_output


def format_review() -> str:
    """Return a combined status + diff summary of the project's current
    uncommitted changes (staged and unstaged). Never raises - any git
    failure (not a repo, git not installed, timeout) is reported as
    ordinary output text, not an exception.
    """
    git_dir = JARVIS_ROOT / ".git"
    if not git_dir.exists():
        return f"{JARVIS_ROOT} is not a git repository yet - nothing to review."

    status_ok, status_output = _run_git(["status", "--short"])
    if not status_ok:
        return f"Could not get git status: {status_output}"

    if not status_output.strip():
        return "No uncommitted changes - the working tree is clean."

    diff_ok, diff_output = _run_git(["diff", "HEAD"])
    if not diff_ok:
        # Status succeeded but diff failed (e.g. no commits yet, so HEAD
        # doesn't exist) - still show status, note the diff issue.
        return (
            "Changed files:\n"
            f"{status_output}\n"
            f"(Could not produce a diff against HEAD: {diff_output})"
        )

    parts = ["Changed files:", status_output.rstrip()]
    if diff_output.strip():
        parts.append("")
        parts.append("Diff:")
        parts.append(diff_output.rstrip())
    else:
        parts.append("")
        parts.append("(No diff content - changes may be to untracked or newly staged files only.)")

    return "\n".join(parts)
