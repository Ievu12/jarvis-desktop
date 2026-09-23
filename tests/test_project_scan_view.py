"""Tests for jarvis.core.project_scan_view.format_scan_result: pure
formatting of a ProjectScanResult, no scanning logic, no side effects."""

from __future__ import annotations

from pathlib import Path

from jarvis.core.project_scan import GitInfo, ProjectScanResult
from jarvis.core.project_scan_view import format_scan_result


def test_includes_project_root():
    result = ProjectScanResult(root=Path("/some/project"))
    output = format_scan_result(result)
    assert "/some/project" in output.replace("\\", "/")


def test_empty_top_level_shows_placeholder():
    result = ProjectScanResult(root=Path("/x"))
    output = format_scan_result(result)
    assert "empty or unreadable" in output.lower()


def test_top_level_entries_listed():
    result = ProjectScanResult(root=Path("/x"), top_level_entries=["a.py", "src/"])
    output = format_scan_result(result)
    assert "a.py" in output
    assert "src/" in output


def test_readme_shown_when_present():
    result = ProjectScanResult(root=Path("/x"), readme_files=["README.md"])
    output = format_scan_result(result)
    assert "README.md" in output


def test_readme_shown_as_none_found_when_absent():
    result = ProjectScanResult(root=Path("/x"), readme_files=[])
    output = format_scan_result(result)
    assert "none found" in output.lower()


def test_manifest_files_listed():
    result = ProjectScanResult(root=Path("/x"), manifest_files=["pyproject.toml", "package.json"])
    output = format_scan_result(result)
    assert "pyproject.toml" in output
    assert "package.json" in output


def test_gitignore_present_and_absent():
    present = ProjectScanResult(root=Path("/x"), has_gitignore=True)
    absent = ProjectScanResult(root=Path("/x"), has_gitignore=False)
    assert "present" in format_scan_result(present).lower()
    assert "not present" in format_scan_result(absent).lower()


def test_not_a_git_repo_shown():
    result = ProjectScanResult(root=Path("/x"), git=GitInfo(is_repo=False))
    output = format_scan_result(result)
    assert "not a git repository" in output.lower()


def test_git_repo_with_commits_shown():
    result = ProjectScanResult(
        root=Path("/x"),
        git=GitInfo(
            is_repo=True,
            commit_count=3,
            latest_commit_summary="abc123 latest change",
            has_uncommitted_changes=False,
        ),
    )
    output = format_scan_result(result)
    assert "3 commits" in output
    assert "abc123 latest change" in output
    assert "clean" in output.lower()


def test_git_repo_single_commit_uses_singular():
    result = ProjectScanResult(
        root=Path("/x"), git=GitInfo(is_repo=True, commit_count=1, has_uncommitted_changes=False)
    )
    output = format_scan_result(result)
    assert "1 commit." in output
    assert "1 commits" not in output


def test_git_repo_with_uncommitted_changes_shown():
    result = ProjectScanResult(
        root=Path("/x"),
        git=GitInfo(is_repo=True, commit_count=2, has_uncommitted_changes=True),
    )
    output = format_scan_result(result)
    assert "uncommitted changes" in output.lower()


def test_git_error_shown():
    result = ProjectScanResult(root=Path("/x"), git=GitInfo(is_repo=True, error="something failed"))
    output = format_scan_result(result)
    assert "something failed" in output


def test_notes_section_shown_when_present():
    result = ProjectScanResult(root=Path("/x"), notes=["No tests found.", "No README file found."])
    output = format_scan_result(result)
    assert "No tests found." in output
    assert "No README file found." in output


def test_no_notes_section_when_empty():
    result = ProjectScanResult(root=Path("/x"), notes=[])
    output = format_scan_result(result)
    assert "Notes:" not in output
