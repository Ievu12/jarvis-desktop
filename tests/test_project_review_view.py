"""Tests for jarvis.core.project_review_view.format_review_result: pure
formatting of a ProjectReview, no derivation logic, no side effects."""

from __future__ import annotations

from jarvis.core.project_review import ProjectReview
from jarvis.core.project_review_view import format_review_result


def _review(**overrides) -> ProjectReview:
    defaults = dict(
        root_summary="/some/project",
        is_git_repo=True,
        working_tree_clean=False,
        changed_file_count=2,
        findings=["2 files with uncommitted changes."],
        concerns=[],
        checks_to_run=["Run 'precommit' in the JARVIS REPL to run the test suite before committing."],
        ready_for_commit=True,
        readiness_reason="Changes are present and a test suite exists.",
    )
    defaults.update(overrides)
    return ProjectReview(**defaults)


def test_includes_root_summary():
    review = _review()
    output = format_review_result(review)
    assert "/some/project" in output


def test_all_four_numbered_sections_present():
    output = format_review_result(_review())
    assert "1. Findings:" in output
    assert "2. Possible concerns:" in output
    assert "3. What to check:" in output
    assert "4. Ready for commit:" in output


def test_findings_items_listed():
    output = format_review_result(_review(findings=["finding A", "finding B"]))
    assert "finding A" in output
    assert "finding B" in output


def test_empty_findings_shows_placeholder():
    output = format_review_result(_review(findings=[]))
    assert "no notable findings" in output.lower()


def test_concerns_items_listed():
    output = format_review_result(_review(concerns=["concern one"]))
    assert "concern one" in output


def test_empty_concerns_shows_placeholder():
    output = format_review_result(_review(concerns=[]))
    assert "(none)" in output


def test_checks_items_listed():
    output = format_review_result(_review(checks_to_run=["check one", "check two"]))
    assert "check one" in output
    assert "check two" in output


def test_empty_checks_shows_placeholder():
    output = format_review_result(_review(checks_to_run=[]))
    assert "nothing further to check" in output.lower()


def test_ready_for_commit_yes_shown():
    output = format_review_result(_review(ready_for_commit=True, readiness_reason="all good"))
    assert "yes" in output.lower()
    assert "all good" in output


def test_ready_for_commit_no_shown():
    output = format_review_result(_review(ready_for_commit=False, readiness_reason="working tree is clean"))
    assert "no - working tree is clean" in output.lower()


def test_output_is_deterministic():
    review = _review()
    assert format_review_result(review) == format_review_result(review)
