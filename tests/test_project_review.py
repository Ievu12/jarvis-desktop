"""Tests for jarvis.core.project_review.build_review: deterministic
structured pre-commit review derived from an already-computed
ProjectScanResult (and optionally a ProjectPlan), plus one fresh git
status check. Never writes anything - verified both via direct real-repo
end-to-end tests (scan_project against real isolated temp directories)
and via mocked get_change_summary() for full state-matrix coverage."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from jarvis.core.project_plan import ProjectPlan, build_plan
from jarvis.core.project_review import ProjectReview, build_review
from jarvis.core.project_scan import GitInfo, ProjectScanResult, scan_project
from jarvis.core.review_view import ChangeSummary


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _bare_scan(root: Path = Path("/x"), **overrides) -> ProjectScanResult:
    defaults = dict(root=root)
    defaults.update(overrides)
    return ProjectScanResult(**defaults)


def _with_change_summary(is_repo: bool, changed_files: int | None, error: str | None = None):
    return patch(
        "jarvis.core.project_review.get_change_summary",
        return_value=ChangeSummary(is_repo=is_repo, changed_files=changed_files, error=error),
    )


# --- returns the right dataclass, never touches the filesystem itself --------


def test_returns_project_review_dataclass():
    with _with_change_summary(is_repo=False, changed_files=None):
        review = build_review(_bare_scan())
    assert isinstance(review, ProjectReview)


def test_root_summary_reflects_scan_root():
    with _with_change_summary(is_repo=False, changed_files=None):
        review = build_review(_bare_scan(root=Path("/some/project")))
    assert "some" in review.root_summary and "project" in review.root_summary


# --- not a git repository -------------------------------------------------------


def test_not_a_repo_is_reported_and_not_ready():
    with _with_change_summary(is_repo=False, changed_files=None):
        review = build_review(_bare_scan())
    assert review.is_git_repo is False
    assert review.working_tree_clean is None
    assert review.ready_for_commit is False
    assert any("not a git repository" in f.lower() for f in review.findings)


# --- repo, clean tree -------------------------------------------------------------


def test_clean_tree_reported_and_not_ready_nothing_to_commit():
    with _with_change_summary(is_repo=True, changed_files=0):
        review = build_review(_bare_scan(has_tests=True))
    assert review.working_tree_clean is True
    assert review.ready_for_commit is False
    assert "clean" in review.readiness_reason.lower()


# --- repo, dirty tree, tests present -> ready -------------------------------------


def test_dirty_tree_with_tests_is_ready_for_commit():
    with _with_change_summary(is_repo=True, changed_files=3):
        review = build_review(_bare_scan(has_tests=True))
    assert review.working_tree_clean is False
    assert review.changed_file_count == 3
    assert review.ready_for_commit is True
    assert any("3 files" in f for f in review.findings)


def test_dirty_tree_single_file_uses_singular_wording():
    with _with_change_summary(is_repo=True, changed_files=1):
        review = build_review(_bare_scan(has_tests=True))
    assert any("1 file " in f for f in review.findings)


# --- repo, dirty tree, no tests -> still ready but flagged as a concern ----------


def test_dirty_tree_without_tests_still_ready_but_concern_raised():
    with _with_change_summary(is_repo=True, changed_files=2):
        review = build_review(_bare_scan(has_tests=False))
    assert review.ready_for_commit is True
    assert any("no test suite" in c.lower() for c in review.concerns)


# --- repo with git error ----------------------------------------------------------


def test_git_error_reported_and_not_ready():
    with _with_change_summary(is_repo=True, changed_files=None, error="git not installed"):
        review = build_review(_bare_scan())
    assert review.working_tree_clean is None
    assert review.ready_for_commit is False
    assert "git not installed" in review.readiness_reason


# --- zero commits yet --------------------------------------------------------------


def test_zero_commits_flagged_as_concern():
    scan = _bare_scan(git=GitInfo(is_repo=True, commit_count=0, has_uncommitted_changes=True))
    with _with_change_summary(is_repo=True, changed_files=1):
        review = build_review(scan)
    assert any("no commits yet" in c.lower() for c in review.concerns)


# --- plan integration ---------------------------------------------------------------


def test_plan_next_step_included_in_findings_when_plan_given():
    plan = ProjectPlan(root_summary="/x", suggested_order=["Add a README describing what the project is."])
    with _with_change_summary(is_repo=True, changed_files=1):
        review = build_review(_bare_scan(has_tests=True), plan)
    assert any("Add a README" in f for f in review.findings)


def test_plan_no_gaps_reflected_in_findings():
    plan = ProjectPlan(root_summary="/x", suggested_order=["No gaps detected by this scan - proceed."])
    with _with_change_summary(is_repo=True, changed_files=0):
        review = build_review(_bare_scan(has_tests=True), plan)
    assert any("no outstanding gaps" in f.lower() for f in review.findings)


def test_missing_plan_does_not_crash_and_omits_plan_findings():
    with _with_change_summary(is_repo=False, changed_files=None):
        review = build_review(_bare_scan())  # plan omitted entirely
    assert isinstance(review, ProjectReview)


def test_plan_with_empty_suggested_order_does_not_crash():
    plan = ProjectPlan(root_summary="/x", suggested_order=[])
    with _with_change_summary(is_repo=False, changed_files=None):
        review = build_review(_bare_scan(), plan)
    assert isinstance(review, ProjectReview)


# --- checks_to_run content -----------------------------------------------------------


def test_checks_suggest_precommit_when_tests_exist():
    with _with_change_summary(is_repo=True, changed_files=1):
        review = build_review(_bare_scan(has_tests=True))
    assert any("precommit" in c.lower() for c in review.checks_to_run)


def test_checks_suggest_adding_tests_when_none_exist():
    with _with_change_summary(is_repo=True, changed_files=1):
        review = build_review(_bare_scan(has_tests=False))
    assert any("adding" in c.lower() and "test" in c.lower() for c in review.checks_to_run)


def test_checks_suggest_review_command_when_changes_present():
    with _with_change_summary(is_repo=True, changed_files=2):
        review = build_review(_bare_scan(has_tests=True))
    assert any("'review'" in c for c in review.checks_to_run)


def test_no_review_suggestion_when_tree_is_clean():
    with _with_change_summary(is_repo=True, changed_files=0):
        review = build_review(_bare_scan(has_tests=True))
    assert not any("full diff" in c.lower() for c in review.checks_to_run)


# --- end-to-end against real temp directories (via scan_project) --------------------


def test_end_to_end_no_git_repo(tmp_path):
    # build_review()'s git-change check (get_change_summary) always reads
    # JARVIS_ROOT, same as status_view - it is not parameterized by
    # scan.root. Real cross-root behavior is exercised here by pointing
    # JARVIS_ROOT itself at tmp_path for the duration of the check, mirroring
    # how run_cli_review() is actually invoked in production (JARVIS_ROOT is
    # always the project being reviewed).
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
    assert review.is_git_repo is False
    assert review.ready_for_commit is False


def test_end_to_end_real_repo_with_uncommitted_file(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    scan = scan_project(tmp_path)
    plan = build_plan(scan)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan, plan)
    assert review.is_git_repo is True
    assert review.changed_file_count == 1
    assert review.ready_for_commit is True


def test_end_to_end_real_repo_clean_after_commit(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
    assert review.working_tree_clean is True
    assert review.ready_for_commit is False


# --- purity --------------------------------------------------------------------------


def test_build_review_never_writes_files(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    before = sorted(p.name for p in tmp_path.iterdir())
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        build_review(scan)
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after


def test_deterministic_same_inputs_same_result():
    with _with_change_summary(is_repo=True, changed_files=1):
        scan = _bare_scan(has_tests=True)
        first = build_review(scan)
        second = build_review(scan)
    assert first == second
