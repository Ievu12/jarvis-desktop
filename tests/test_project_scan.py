"""Tests for jarvis.core.project_scan.scan_project: deterministic,
read-only project analysis. No LLM, no API key, never modifies the
project. Uses isolated tmp_path directories throughout - never the real
JARVIS project."""

from __future__ import annotations

import subprocess

import pytest

from jarvis.core.project_scan import scan_project


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


# --- basic structure detection ------------------------------------------------


def test_empty_directory_scan_does_not_raise(tmp_path):
    result = scan_project(tmp_path)
    assert result.root == tmp_path
    assert result.top_level_entries == []


def test_top_level_entries_lists_files_and_dirs(tmp_path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "subdir").mkdir()

    result = scan_project(tmp_path)
    assert "a.py" in result.top_level_entries
    assert "subdir/" in result.top_level_entries


def test_housekeeping_directories_excluded_from_top_level(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "real_file.py").write_text("x", encoding="utf-8")

    result = scan_project(tmp_path)
    assert ".venv/" not in result.top_level_entries
    assert "__pycache__/" not in result.top_level_entries
    assert ".git/" not in result.top_level_entries
    assert "real_file.py" in result.top_level_entries


# --- README detection ----------------------------------------------------------


def test_readme_detected_when_present(tmp_path):
    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")
    result = scan_project(tmp_path)
    assert "README.md" in result.readme_files


def test_no_readme_reported_as_a_note(tmp_path):
    result = scan_project(tmp_path)
    assert result.readme_files == []
    assert any("readme" in note.lower() for note in result.notes)


def test_never_invents_a_readme_that_does_not_exist(tmp_path):
    result = scan_project(tmp_path)
    assert result.readme_files == []


# --- manifest file detection --------------------------------------------------


def test_detects_multiple_manifest_types(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    result = scan_project(tmp_path)
    assert "pyproject.toml" in result.manifest_files
    assert "package.json" in result.manifest_files


def test_no_manifest_reported_as_a_note(tmp_path):
    result = scan_project(tmp_path)
    assert result.manifest_files == []
    assert any("manifest" in note.lower() for note in result.notes)


# --- .gitignore / JARVIS.md ---------------------------------------------------


def test_gitignore_detected(tmp_path):
    (tmp_path / ".gitignore").write_text("*.pyc", encoding="utf-8")
    result = scan_project(tmp_path)
    assert result.has_gitignore is True


def test_gitignore_absent(tmp_path):
    result = scan_project(tmp_path)
    assert result.has_gitignore is False


def test_jarvis_md_detected(tmp_path):
    (tmp_path / "JARVIS.md").write_text("notes", encoding="utf-8")
    result = scan_project(tmp_path)
    assert result.has_jarvis_md is True


def test_jarvis_md_absent(tmp_path):
    result = scan_project(tmp_path)
    assert result.has_jarvis_md is False


# --- test detection --------------------------------------------------------------


def test_detects_conventional_test_directory(tmp_path):
    (tmp_path / "tests").mkdir()
    result = scan_project(tmp_path)
    assert result.has_tests is True
    assert "tests/" in result.test_locations


def test_detects_top_level_test_files(tmp_path):
    (tmp_path / "test_thing.py").write_text("def test_x(): pass", encoding="utf-8")
    result = scan_project(tmp_path)
    assert result.has_tests is True
    assert "test_thing.py" in result.test_locations


def test_no_tests_reported_as_a_note(tmp_path):
    result = scan_project(tmp_path)
    assert result.has_tests is False
    assert any("test" in note.lower() for note in result.notes)


# --- git detection ---------------------------------------------------------------


def test_not_a_git_repo(tmp_path):
    result = scan_project(tmp_path)
    assert result.git.is_repo is False
    assert any("git repository" in note.lower() for note in result.notes)


def test_git_repo_with_commits(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=tmp_path, check=True)

    result = scan_project(tmp_path)
    assert result.git.is_repo is True
    assert result.git.commit_count == 1
    assert result.git.latest_commit_summary is not None
    assert "initial commit" in result.git.latest_commit_summary


def test_git_repo_with_uncommitted_changes(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)
    (tmp_path / "file.txt").write_text("modified", encoding="utf-8")

    result = scan_project(tmp_path)
    assert result.git.has_uncommitted_changes is True


def test_git_repo_clean_tree(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    result = scan_project(tmp_path)
    assert result.git.has_uncommitted_changes is False


def test_git_repo_with_zero_commits(tmp_path):
    _init_git_repo(tmp_path)
    result = scan_project(tmp_path)
    assert result.git.is_repo is True
    assert result.git.commit_count == 0
    assert result.git.latest_commit_summary is None


# --- never modifies the project -----------------------------------------------


def test_scan_never_writes_anything(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("# hi", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")

    files_before = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    scan_project(tmp_path)
    scan_project(tmp_path)
    files_after = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())

    assert files_before == files_after


def test_scan_never_raises_on_a_realistic_mixed_project(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("*.pyc", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("pass", encoding="utf-8")

    scan_project(tmp_path)  # must not raise
