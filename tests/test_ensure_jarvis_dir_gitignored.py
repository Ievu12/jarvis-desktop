"""Tests for jarvis.core.secrets.ensure_jarvis_dir_gitignored: only acts
inside a git repo, creates .gitignore if missing, appends to an existing
one without disturbing its content, is idempotent, and never raises for
expected conditions. All fixtures use isolated tmp_path directories -
never the real JARVIS project."""

from __future__ import annotations

import subprocess

import pytest

from jarvis.core.secrets import ensure_jarvis_dir_gitignored


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def test_not_a_git_repo_returns_none_and_writes_nothing(tmp_path):
    result = ensure_jarvis_dir_gitignored(tmp_path)
    assert result is None
    assert not (tmp_path / ".gitignore").exists()


def test_git_repo_with_no_gitignore_creates_one(tmp_path):
    _init_git_repo(tmp_path)
    result = ensure_jarvis_dir_gitignored(tmp_path)

    assert result is not None
    assert "gitignore" in result.lower()
    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    assert ".jarvis/" in gitignore.read_text(encoding="utf-8")


def test_git_repo_with_existing_gitignore_appends_entry(tmp_path):
    _init_git_repo(tmp_path)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.pyc\n__pycache__/\n", encoding="utf-8")

    result = ensure_jarvis_dir_gitignored(tmp_path)

    assert result is not None
    content = gitignore.read_text(encoding="utf-8")
    assert "*.pyc" in content  # original content preserved
    assert "__pycache__/" in content
    assert ".jarvis/" in content


def test_existing_gitignore_without_trailing_newline_still_appended_cleanly(tmp_path):
    _init_git_repo(tmp_path)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.pyc", encoding="utf-8")  # no trailing newline

    ensure_jarvis_dir_gitignored(tmp_path)

    content = gitignore.read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip()]
    assert "*.pyc" in lines
    assert ".jarvis/" in lines


def test_already_ignored_via_existing_entry_is_idempotent_no_op(tmp_path):
    _init_git_repo(tmp_path)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".jarvis/\n", encoding="utf-8")

    result = ensure_jarvis_dir_gitignored(tmp_path)

    assert result is None  # already covered, nothing to do
    # File content unchanged (still exactly one .jarvis/ line).
    content = gitignore.read_text(encoding="utf-8")
    assert content.count(".jarvis/") == 1


def test_second_call_after_first_write_is_idempotent(tmp_path):
    _init_git_repo(tmp_path)

    first = ensure_jarvis_dir_gitignored(tmp_path)
    second = ensure_jarvis_dir_gitignored(tmp_path)

    assert first is not None
    assert second is None
    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert content.count(".jarvis/") == 1  # no duplicate entry


def test_broader_ignore_pattern_that_covers_jarvis_is_recognized(tmp_path):
    # If .jarvis/ is already covered by a broader pattern (e.g. via
    # 'git check-ignore' recognizing it through some other rule), no
    # redundant entry should be added.
    _init_git_repo(tmp_path)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".*/\n", encoding="utf-8")  # matches any dot-directory

    result = ensure_jarvis_dir_gitignored(tmp_path)

    assert result is None
    content = gitignore.read_text(encoding="utf-8")
    assert ".jarvis/" not in content  # no entry added since already covered


def test_never_truncates_or_overwrites_existing_gitignore_content(tmp_path):
    _init_git_repo(tmp_path)
    gitignore = tmp_path / ".gitignore"
    original = "node_modules/\n.env\n*.log\nbuild/\n"
    gitignore.write_text(original, encoding="utf-8")

    ensure_jarvis_dir_gitignored(tmp_path)

    content = gitignore.read_text(encoding="utf-8")
    for line in original.splitlines():
        assert line in content


def test_returns_none_when_jarvis_dir_does_not_exist_yet_but_still_protects(tmp_path):
    # .jarvis/ doesn't need to exist yet for the gitignore entry to be
    # added proactively - it protects against future creation too.
    _init_git_repo(tmp_path)
    assert not (tmp_path / ".jarvis").exists()

    result = ensure_jarvis_dir_gitignored(tmp_path)

    assert result is not None
    assert ".jarvis/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
