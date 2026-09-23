"""Tests for jarvis.core.project_commit_view.format_commit_plan: pure
formatting of a CommitPlan, no derivation logic, no side effects."""

from __future__ import annotations

from jarvis.core.project_commit import CommitPlan
from jarvis.core.project_commit_view import format_commit_plan


def _plan(**overrides) -> CommitPlan:
    defaults = dict(
        root_summary="/some/project",
        is_git_repo=True,
        staged_files=["a.py"],
        unstaged_files=[],
        findings=["1 file with uncommitted changes."],
        concerns=[],
        can_commit=True,
        block_reason="Staged changes are present and the pre-commit check passed.",
    )
    defaults.update(overrides)
    return CommitPlan(**defaults)


def test_includes_root_summary():
    output = format_commit_plan(_plan())
    assert "/some/project" in output


def test_all_five_numbered_sections_present():
    output = format_commit_plan(_plan())
    assert "1. Findings:" in output
    assert "2. Staged for commit:" in output
    assert "3. Unstaged (will NOT be committed):" in output
    assert "4. Possible concerns:" in output
    assert "5. Can commit now:" in output


def test_findings_items_listed():
    output = format_commit_plan(_plan(findings=["finding A"]))
    assert "finding A" in output


def test_empty_findings_shows_placeholder():
    output = format_commit_plan(_plan(findings=[]))
    assert "no notable findings" in output.lower()


def test_staged_files_listed_by_name():
    output = format_commit_plan(_plan(staged_files=["README.md", "jarvis/cli/main.py"]))
    assert "README.md" in output
    assert "jarvis/cli/main.py" in output


def test_empty_staged_with_repo_shows_none_placeholder():
    output = format_commit_plan(_plan(staged_files=[], is_git_repo=True))
    assert "(none)" in output


def test_empty_staged_without_repo_shows_not_applicable():
    output = format_commit_plan(_plan(staged_files=[], is_git_repo=False))
    assert "not a git repository" in output.lower()


def test_unstaged_files_listed_with_warning_label():
    output = format_commit_plan(_plan(unstaged_files=["dirty.py"]))
    assert "dirty.py" in output
    assert "will NOT be committed" in output


def test_no_unstaged_shows_none():
    output = format_commit_plan(_plan(unstaged_files=[]))
    lines = output.splitlines()
    section_index = next(i for i, l in enumerate(lines) if l.startswith("3. Unstaged"))
    assert "(none)" in lines[section_index + 1]


def test_concerns_items_listed():
    output = format_commit_plan(_plan(concerns=["concern one"]))
    assert "concern one" in output


def test_empty_concerns_shows_placeholder():
    output = format_commit_plan(_plan(concerns=[]))
    assert "(none)" in output


def test_can_commit_yes_shown():
    output = format_commit_plan(_plan(can_commit=True, block_reason="all good"))
    assert "yes - all good" in output.lower()


def test_can_commit_no_shown():
    output = format_commit_plan(_plan(can_commit=False, block_reason="nothing is staged"))
    assert "no - nothing is staged" in output.lower()


def test_never_auto_commit_disclaimer_present():
    output = format_commit_plan(_plan())
    assert "never runs 'git commit' without your explicit y/n confirmation" in output.lower()
    assert "never runs 'git add'" in output.lower()


def test_output_is_deterministic():
    plan = _plan()
    assert format_commit_plan(plan) == format_commit_plan(plan)
