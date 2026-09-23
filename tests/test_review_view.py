"""Tests for jarvis.core.review_view.format_review: combined git status +
diff summary for the 'review' CLI command. Read-only, never writes to the
repository. Uses monkeypatch on jarvis.core.review_view.JARVIS_ROOT to
point at isolated temp git repos - never the real project."""

from __future__ import annotations

import subprocess

import pytest

from jarvis.core import review_view


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def test_not_a_git_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    result = review_view.format_review()
    assert "not a git repository" in result


def test_clean_repo_no_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    result = review_view.format_review()
    assert "clean" in result.lower()


def test_untracked_file_shown_in_status(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "new.txt").write_text("content", encoding="utf-8")

    result = review_view.format_review()
    assert "new.txt" in result
    assert "Changed files" in result


def test_untracked_file_does_not_crash_when_no_head_exists(tmp_path, monkeypatch):
    # Before any commit exists, HEAD doesn't resolve - git diff HEAD would
    # fail. format_review() must degrade gracefully, not raise or crash.
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "new.txt").write_text("content", encoding="utf-8")

    result = review_view.format_review()
    assert "new.txt" in result  # status still shown even though diff can't run


def test_modified_tracked_file_shows_diff_content(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    f = tmp_path / "tracked.txt"
    f.write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    f.write_text("original\nadded line\n", encoding="utf-8")

    result = review_view.format_review()
    assert "tracked.txt" in result
    assert "added line" in result
    assert "Diff:" in result


def test_deleted_tracked_file_shown_in_status(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    f = tmp_path / "to_delete.txt"
    f.write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "add", "to_delete.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    f.unlink()

    result = review_view.format_review()
    assert "to_delete.txt" in result


def test_staged_and_unstaged_changes_both_shown(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("a\n", encoding="utf-8")
    f2.write_text("b\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt", "b.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    f1.write_text("a\nstaged change\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    f2.write_text("b\nunstaged change\n", encoding="utf-8")

    result = review_view.format_review()
    assert "a.txt" in result
    assert "b.txt" in result
    assert "staged change" in result
    assert "unstaged change" in result


def test_get_changed_file_names_not_a_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    assert review_view.get_changed_file_names() is None


def test_get_changed_file_names_clean_repo_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    assert review_view.get_changed_file_names() == []


def test_get_changed_file_names_lists_untracked_file(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "new.txt").write_text("content", encoding="utf-8")
    assert review_view.get_changed_file_names() == ["new.txt"]


def test_get_changed_file_names_lists_multiple_files(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    names = review_view.get_changed_file_names()
    assert names is not None
    assert sorted(names) == ["a.txt", "b.txt"]


def test_get_changed_file_names_lists_modified_tracked_file(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    f = tmp_path / "tracked.txt"
    f.write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    f.write_text("original\nadded line\n", encoding="utf-8")

    assert review_view.get_changed_file_names() == ["tracked.txt"]


def test_get_changed_file_names_never_writes_to_the_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "new.txt").write_text("content", encoding="utf-8")

    before = sorted(p.name for p in tmp_path.iterdir())
    review_view.get_changed_file_names()
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after


def test_staged_unstaged_split_not_a_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    assert review_view.get_staged_and_unstaged_file_names() is None


def test_staged_unstaged_split_clean_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    assert review_view.get_staged_and_unstaged_file_names() == ([], [])


def test_staged_unstaged_split_untracked_file_is_unstaged_only(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "new.txt").write_text("content", encoding="utf-8")

    staged, unstaged = review_view.get_staged_and_unstaged_file_names()
    assert staged == []
    assert unstaged == ["new.txt"]


def test_staged_unstaged_split_staged_file_is_staged_only(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "new.txt").write_text("content", encoding="utf-8")
    subprocess.run(["git", "add", "new.txt"], cwd=tmp_path, check=True)

    staged, unstaged = review_view.get_staged_and_unstaged_file_names()
    assert staged == ["new.txt"]
    assert unstaged == []


def test_staged_unstaged_split_modified_tracked_file_is_unstaged_only(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    f = tmp_path / "tracked.txt"
    f.write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    f.write_text("original\nadded line\n", encoding="utf-8")

    staged, unstaged = review_view.get_staged_and_unstaged_file_names()
    assert staged == []
    assert unstaged == ["tracked.txt"]


def test_staged_unstaged_split_file_staged_then_further_modified_appears_in_both(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    f = tmp_path / "tracked.txt"
    f.write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    f.write_text("v2\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    f.write_text("v3\n", encoding="utf-8")

    staged, unstaged = review_view.get_staged_and_unstaged_file_names()
    assert staged == ["tracked.txt"]
    assert unstaged == ["tracked.txt"]


def test_staged_unstaged_split_never_writes_to_the_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "new.txt").write_text("content", encoding="utf-8")

    before = sorted(p.name for p in tmp_path.iterdir())
    review_view.get_staged_and_unstaged_file_names()
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after


# --- get_staged_diff ---------------------------------------------------------------


def test_get_staged_diff_not_a_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    assert review_view.get_staged_diff() is None


def test_get_staged_diff_clean_repo_returns_empty_string(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    assert review_view.get_staged_diff() == ""


def test_get_staged_diff_shows_staged_content(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    f = tmp_path / "new.txt"
    f.write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "new.txt"], cwd=tmp_path, check=True)

    diff = review_view.get_staged_diff()
    assert diff is not None
    assert "hello" in diff
    assert "new.txt" in diff


def test_get_staged_diff_excludes_unstaged_content(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    f = tmp_path / "tracked.txt"
    f.write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    f.write_text("original\nunstaged addition\n", encoding="utf-8")
    # Not staged - get_staged_diff() must not show this change.
    diff = review_view.get_staged_diff()
    assert diff == ""


def test_get_staged_diff_shows_only_staged_portion(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    f1 = tmp_path / "staged.txt"
    f2 = tmp_path / "unstaged.txt"
    f1.write_text("staged content\n", encoding="utf-8")
    f2.write_text("unstaged content\n", encoding="utf-8")
    subprocess.run(["git", "add", "staged.txt"], cwd=tmp_path, check=True)

    diff = review_view.get_staged_diff()
    assert diff is not None
    assert "staged content" in diff
    assert "unstaged content" not in diff


def test_get_staged_diff_never_writes_to_the_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    f = tmp_path / "new.txt"
    f.write_text("content", encoding="utf-8")
    subprocess.run(["git", "add", "new.txt"], cwd=tmp_path, check=True)

    before = sorted(p.name for p in tmp_path.iterdir())
    review_view.get_staged_diff()
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after


def test_review_never_writes_to_the_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    _init_git_repo(tmp_path)
    f = tmp_path / "tracked.txt"
    f.write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    before_log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True
    ).stdout

    review_view.format_review()
    review_view.format_review()

    after_log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert before_log == after_log  # no new commits, nothing changed
