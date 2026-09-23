"""Tests for jarvis.core.project_precommit.build_precommit_check:
deterministic final pre-commit verdict derived from an already-computed
ProjectReview, plus one fresh read-only listing of changed file names.
Never runs tests, never writes anything, never mutates git - verified
both via direct real-repo end-to-end tests (scan_project/build_review
against real isolated temp directories) and via mocked
get_changed_file_names() for full state-matrix coverage."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from jarvis.core.project_precommit import PrecommitCheck, build_precommit_check
from jarvis.core.project_review import ProjectReview, build_review
from jarvis.core.project_scan import ProjectScanResult, scan_project
from jarvis.core.review_view import ChangeSummary


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _review(**overrides) -> ProjectReview:
    defaults = dict(
        root_summary="/x",
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


def _with_changed_file_names(names: list[str] | None):
    return patch("jarvis.core.project_precommit.get_changed_file_names", return_value=names)


# --- returns the right dataclass -----------------------------------------------


def test_returns_precommit_check_dataclass():
    with _with_changed_file_names(["a.py", "b.py"]):
        check = build_precommit_check(_review())
    assert isinstance(check, PrecommitCheck)


def test_carries_forward_root_summary_from_review():
    with _with_changed_file_names([]):
        check = build_precommit_check(_review(root_summary="/some/project"))
    assert check.root_summary == "/some/project"


# --- changed file names ----------------------------------------------------------


def test_changed_files_listed_by_name():
    with _with_changed_file_names(["README.md", "jarvis/cli/main.py"]):
        check = build_precommit_check(_review())
    assert check.changed_files == ["README.md", "jarvis/cli/main.py"]


def test_no_changed_files_returns_empty_list_not_none():
    with _with_changed_file_names([]):
        check = build_precommit_check(_review(changed_file_count=0, working_tree_clean=True))
    assert check.changed_files == []


def test_none_from_get_changed_file_names_becomes_empty_list():
    # get_changed_file_names() returns None when not a repo or on error -
    # build_precommit_check must normalize that to [], never propagate None.
    with _with_changed_file_names(None):
        check = build_precommit_check(_review(is_git_repo=False, working_tree_clean=None))
    assert check.changed_files == []


# --- fields carried through unchanged from the review ------------------------------


def test_findings_carried_through_from_review():
    with _with_changed_file_names([]):
        check = build_precommit_check(_review(findings=["finding A", "finding B"]))
    assert check.findings == ["finding A", "finding B"]


def test_concerns_carried_through_from_review():
    with _with_changed_file_names([]):
        check = build_precommit_check(_review(concerns=["concern A"]))
    assert check.concerns == ["concern A"]


def test_readiness_carried_through_from_review():
    with _with_changed_file_names([]):
        check = build_precommit_check(_review(ready_for_commit=True, readiness_reason="all good"))
    assert check.ready_for_commit is True
    assert check.readiness_reason == "all good"


def test_not_ready_carried_through_from_review():
    with _with_changed_file_names([]):
        check = build_precommit_check(_review(ready_for_commit=False, readiness_reason="clean tree"))
    assert check.ready_for_commit is False
    assert check.readiness_reason == "clean tree"


def test_working_tree_clean_carried_through():
    with _with_changed_file_names([]):
        check = build_precommit_check(_review(working_tree_clean=True))
    assert check.working_tree_clean is True


def test_is_git_repo_carried_through():
    with _with_changed_file_names([]):
        check = build_precommit_check(_review(is_git_repo=False))
    assert check.is_git_repo is False


# --- always appends the "never commit automatically" check -------------------------


def test_never_auto_commit_check_always_present():
    with _with_changed_file_names([]):
        check = build_precommit_check(_review(checks_to_run=[]))
    assert any("never run 'git commit' automatically" in c.lower() for c in check.checks_to_run)


def test_review_checks_preserved_alongside_new_one():
    with _with_changed_file_names([]):
        check = build_precommit_check(_review(checks_to_run=["existing check one"]))
    assert "existing check one" in check.checks_to_run
    assert len(check.checks_to_run) == 2


# --- purity: never runs subprocess beyond the read-only git status check ------------


def test_build_precommit_check_never_writes_files(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    before = sorted(p.name for p in tmp_path.iterdir())
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
        build_precommit_check(review)
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after


def test_build_precommit_check_never_calls_git_commit(monkeypatch):
    import subprocess as sp

    real_run = sp.run

    def _guarded_run(argv, *a, **k):
        if isinstance(argv, list) and "commit" in argv:
            raise AssertionError("build_precommit_check must never invoke git commit")
        return real_run(argv, *a, **k)

    monkeypatch.setattr(sp, "run", _guarded_run)
    with _with_changed_file_names(["a.py"]):
        build_precommit_check(_review())


# --- end-to-end against real temp directories (via scan_project/build_review) -------


def test_end_to_end_no_git_repo(tmp_path):
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
        check = build_precommit_check(review)
    assert check.is_git_repo is False
    assert check.changed_files == []
    assert check.ready_for_commit is False


def test_end_to_end_real_repo_with_uncommitted_file(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
        check = build_precommit_check(review)
    assert check.is_git_repo is True
    assert check.changed_files == ["notes.txt"]
    assert check.ready_for_commit is True


def test_end_to_end_real_repo_clean_after_commit(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
        check = build_precommit_check(review)
    assert check.working_tree_clean is True
    assert check.changed_files == []
    assert check.ready_for_commit is False


def test_end_to_end_multiple_changed_files(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
        check = build_precommit_check(review)
    assert sorted(check.changed_files) == ["a.txt", "b.txt"]


# --- determinism ---------------------------------------------------------------------


def test_deterministic_same_inputs_same_result():
    with _with_changed_file_names(["a.py"]):
        review = _review()
        first = build_precommit_check(review)
        second = build_precommit_check(review)
    assert first == second
