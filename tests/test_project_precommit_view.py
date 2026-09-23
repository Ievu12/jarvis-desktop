"""Tests for jarvis.core.project_precommit_view.format_precommit_check:
pure formatting of a PrecommitCheck, no derivation logic, no side
effects."""

from __future__ import annotations

from jarvis.core.project_precommit import PrecommitCheck
from jarvis.core.project_precommit_view import format_precommit_check


def _check(**overrides) -> PrecommitCheck:
    defaults = dict(
        root_summary="/some/project",
        is_git_repo=True,
        working_tree_clean=False,
        changed_files=["a.py", "b.py"],
        findings=["2 files with uncommitted changes."],
        concerns=[],
        checks_to_run=["Never run 'git commit' automatically - only a human should decide to commit."],
        ready_for_commit=True,
        readiness_reason="Changes are present and a test suite exists.",
    )
    defaults.update(overrides)
    return PrecommitCheck(**defaults)


def test_includes_root_summary():
    output = format_precommit_check(_check())
    assert "/some/project" in output


def test_all_five_numbered_sections_present():
    output = format_precommit_check(_check())
    assert "1. Findings:" in output
    assert "2. Changed files:" in output
    assert "3. Possible concerns:" in output
    assert "4. Checks to run before committing:" in output
    assert "5. Ready for commit:" in output


def test_findings_items_listed():
    output = format_precommit_check(_check(findings=["finding A"]))
    assert "finding A" in output


def test_empty_findings_shows_placeholder():
    output = format_precommit_check(_check(findings=[]))
    assert "no notable findings" in output.lower()


def test_changed_files_listed_by_name():
    output = format_precommit_check(_check(changed_files=["README.md", "jarvis/cli/main.py"]))
    assert "README.md" in output
    assert "jarvis/cli/main.py" in output


def test_empty_changed_files_with_clean_repo_shows_clean_placeholder():
    output = format_precommit_check(
        _check(changed_files=[], is_git_repo=True, working_tree_clean=True)
    )
    assert "clean" in output.lower()


def test_empty_changed_files_without_repo_shows_not_applicable_placeholder():
    output = format_precommit_check(
        _check(changed_files=[], is_git_repo=False, working_tree_clean=None)
    )
    assert "not a git repository" in output.lower()


def test_concerns_items_listed():
    output = format_precommit_check(_check(concerns=["concern one"]))
    assert "concern one" in output


def test_empty_concerns_shows_placeholder():
    output = format_precommit_check(_check(concerns=[]))
    assert "(none)" in output


def test_checks_items_listed():
    output = format_precommit_check(_check(checks_to_run=["check one", "check two"]))
    assert "check one" in output
    assert "check two" in output


def test_ready_for_commit_yes_shown():
    output = format_precommit_check(_check(ready_for_commit=True, readiness_reason="all good"))
    assert "yes - all good" in output.lower()


def test_ready_for_commit_no_shown():
    output = format_precommit_check(_check(ready_for_commit=False, readiness_reason="working tree is clean"))
    assert "no - working tree is clean" in output.lower()


def test_never_auto_commit_disclaimer_present():
    output = format_precommit_check(_check())
    assert "never runs git commit" in output.lower()


def test_output_is_deterministic():
    check = _check()
    assert format_precommit_check(check) == format_precommit_check(check)
