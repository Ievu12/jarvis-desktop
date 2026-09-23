"""Deterministic, structured project scan: a read-only survey of the
project directory producing a plain-data result object, with no LLM
involvement and no API key required. Distinct from the CLI's LLM-driven
'scan' REPL command (SCAN_PROMPT in jarvis.cli.main), which asks the
model to survey the project conversationally and propose a JARVIS.md
draft - this module is the deterministic counterpart: same spirit
(understand the project), but produces a typed, structured result rather
than free-form prose, specifically so a future 'plan' command (or any
other consumer) can inspect scan findings programmatically instead of
re-parsing a chat response.

Never modifies the project: only os.walk/Path.exists/read_text and a
handful of read-only git subcommands (status/log --oneline), the same
git-invocation pattern already used by jarvis.core.review_view.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_GIT_TIMEOUT_SECONDS = 15

# Conventionally-meaningful files worth checking for by name. Never
# invented/assumed - only reported if actually present.
_MANIFEST_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "pom.xml",
    "build.gradle",
)

_README_FILES = ("README.md", "README.rst", "README.txt", "README")

_TEST_DIR_NAMES = ("tests", "test", "spec", "__tests__")

# Housekeeping/noise directories excluded from the top-level listing and
# from the "has tests" search, consistent with the skip-list already used
# by jarvis.tools.fs's recursive listing/search tools.
_SKIP_DIR_NAMES = {".venv", "venv", ".git", "__pycache__", ".jarvis", ".pytest_cache", "node_modules"}


@dataclass
class GitInfo:
    is_repo: bool
    commit_count: int | None = None
    latest_commit_summary: str | None = None
    has_uncommitted_changes: bool | None = None
    error: str | None = None


@dataclass
class ProjectScanResult:
    """Structured findings from a project scan. Every field is derived
    purely from what's actually on disk / in git - nothing here is
    inferred, guessed, or invented. Designed to be consumed by a future
    'plan' command as well as rendered for a human via
    format_scan_result()."""

    root: Path
    top_level_entries: list[str] = field(default_factory=list)
    readme_files: list[str] = field(default_factory=list)
    manifest_files: list[str] = field(default_factory=list)
    has_gitignore: bool = False
    has_jarvis_md: bool = False
    has_tests: bool = False
    test_locations: list[str] = field(default_factory=list)
    git: GitInfo = field(default_factory=lambda: GitInfo(is_repo=False))
    notes: list[str] = field(default_factory=list)


def _run_git(root: Path, args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
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


def _gather_git_info(root: Path) -> GitInfo:
    if not (root / ".git").exists():
        return GitInfo(is_repo=False)

    info = GitInfo(is_repo=True)

    log_ok, log_output = _run_git(root, ["log", "--oneline"])
    if log_ok:
        lines = [line for line in log_output.splitlines() if line.strip()]
        info.commit_count = len(lines)
        if lines:
            info.latest_commit_summary = lines[0].strip()
    elif "does not have any commits yet" in log_output:
        # A freshly-initialized repo with no commits is an expected,
        # normal state - not an error. git log exits non-zero here, but
        # this should be reported as zero commits, not a failure.
        info.commit_count = 0
    else:
        info.error = log_output

    status_ok, status_output = _run_git(root, ["status", "--short"])
    if status_ok:
        info.has_uncommitted_changes = bool(status_output.strip())
    elif info.error is None:
        info.error = status_output

    return info


def _find_test_locations(root: Path) -> list[str]:
    """Look for conventional test directories and test_*.py / *_test.py
    files at the top level and inside conventional test directories,
    without a full recursive walk of the whole project (this is a quick
    signal, not an exhaustive search - jarvis's search_files tool already
    covers exhaustive search)."""
    locations: list[str] = []

    for name in _TEST_DIR_NAMES:
        candidate = root / name
        if candidate.is_dir():
            locations.append(f"{name}/")

    try:
        for entry in root.iterdir():
            if entry.is_file() and (
                entry.name.startswith("test_") or entry.name.endswith("_test.py")
            ):
                locations.append(entry.name)
    except OSError:
        pass

    return locations


def scan_project(root: Path) -> ProjectScanResult:
    """Perform a read-only scan of `root` and return structured findings.
    Never raises for expected conditions (missing files, not a git repo,
    git not installed) - those are reflected in the result's fields, not
    exceptions. Never writes or modifies anything under `root`.
    """
    result = ProjectScanResult(root=root)

    try:
        entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        result.notes.append(f"Could not list the project directory: {e}")
        entries = []

    for entry in entries:
        if entry.name in _SKIP_DIR_NAMES:
            continue
        result.top_level_entries.append(entry.name + ("/" if entry.is_dir() else ""))

    for name in _README_FILES:
        if (root / name).is_file():
            result.readme_files.append(name)

    for name in _MANIFEST_FILES:
        if (root / name).is_file():
            result.manifest_files.append(name)

    result.has_gitignore = (root / ".gitignore").is_file()
    result.has_jarvis_md = (root / "JARVIS.md").is_file()

    result.test_locations = _find_test_locations(root)
    result.has_tests = bool(result.test_locations)

    result.git = _gather_git_info(root)

    if not result.manifest_files:
        result.notes.append("No recognized package/dependency manifest found.")
    if not result.readme_files:
        result.notes.append("No README file found.")
    if not result.has_tests:
        result.notes.append("No test directory or test files found at the top level.")
    if not result.git.is_repo:
        result.notes.append("Not a git repository yet.")

    return result
