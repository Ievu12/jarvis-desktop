"""Tests for jarvis.core.project_commit.build_commit_plan: deterministic
commit plan derived from an already-computed PrecommitCheck, plus one
fresh read-only staged/unstaged file split. Never runs git commit, never
writes anything - verified both via direct real-repo end-to-end tests
(scan_project/build_review/build_precommit_check against real isolated
temp directories) and via mocked get_staged_and_unstaged_file_names() for
full state-matrix coverage."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from jarvis.core.project_commit import CommitPlan, build_commit_plan
from jarvis.core.project_precommit import PrecommitCheck, build_precommit_check
from jarvis.core.project_review import build_review
from jarvis.core.project_scan import scan_project


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _check(**overrides) -> PrecommitCheck:
    defaults = dict(
        root_summary="/x",
        is_git_repo=True,
        working_tree_clean=False,
        changed_files=["a.py"],
        findings=["1 file with uncommitted changes."],
        concerns=[],
        checks_to_run=["Never run 'git commit' automatically - only a human should decide to commit."],
        ready_for_commit=True,
        readiness_reason="Changes are present and a test suite exists.",
    )
    defaults.update(overrides)
    return PrecommitCheck(**defaults)


def _with_staged_unstaged(value: tuple[list[str], list[str]] | None):
    return patch("jarvis.core.project_commit.get_staged_and_unstaged_file_names", return_value=value)


# --- returns the right dataclass -----------------------------------------------


def test_returns_commit_plan_dataclass():
    with _with_staged_unstaged((["a.py"], [])):
        plan = build_commit_plan(_check())
    assert isinstance(plan, CommitPlan)


def test_carries_forward_root_summary_from_check():
    with _with_staged_unstaged(([], [])):
        plan = build_commit_plan(_check(root_summary="/some/project"))
    assert plan.root_summary == "/some/project"


# --- not a git repository --------------------------------------------------------


def test_not_a_repo_cannot_commit():
    with _with_staged_unstaged(None):
        plan = build_commit_plan(_check(is_git_repo=False, ready_for_commit=False))
    assert plan.can_commit is False
    assert "not a git repository" in plan.block_reason.lower()


# --- precommit check did not pass -------------------------------------------------


def test_precommit_not_ready_blocks_commit_even_with_staged_files():
    with _with_staged_unstaged((["a.py"], [])):
        plan = build_commit_plan(
            _check(ready_for_commit=False, readiness_reason="Working tree is already clean.")
        )
    assert plan.can_commit is False
    assert plan.block_reason == "Working tree is already clean."


# --- nothing staged ----------------------------------------------------------------


def test_no_staged_no_unstaged_blocks_commit():
    with _with_staged_unstaged(([], [])):
        plan = build_commit_plan(_check())
    assert plan.can_commit is False
    assert "nothing is staged" in plan.block_reason.lower()


def test_unstaged_only_blocks_commit_with_specific_reason():
    with _with_staged_unstaged(([], ["a.py"])):
        plan = build_commit_plan(_check())
    assert plan.can_commit is False
    assert "stage the files" in plan.block_reason.lower()


# --- staged files present, everything else green -> can commit ----------------------


def test_staged_files_with_passing_precommit_allows_commit():
    with _with_staged_unstaged((["a.py", "b.py"], [])):
        plan = build_commit_plan(_check())
    assert plan.can_commit is True
    assert plan.staged_files == ["a.py", "b.py"]


def test_staged_and_unstaged_both_reported_separately():
    with _with_staged_unstaged((["staged.py"], ["unstaged.py"])):
        plan = build_commit_plan(_check())
    assert plan.can_commit is True
    assert plan.staged_files == ["staged.py"]
    assert plan.unstaged_files == ["unstaged.py"]


# --- None from get_staged_and_unstaged_file_names normalizes to empty lists ---------


def test_none_from_helper_becomes_empty_lists_not_crash():
    with _with_staged_unstaged(None):
        plan = build_commit_plan(_check(is_git_repo=False, ready_for_commit=False))
    assert plan.staged_files == []
    assert plan.unstaged_files == []


# --- fields carried through from the check ------------------------------------------


def test_findings_and_concerns_carried_through():
    with _with_staged_unstaged((["a.py"], [])):
        plan = build_commit_plan(_check(findings=["finding A"], concerns=["concern A"]))
    assert plan.findings == ["finding A"]
    assert plan.concerns == ["concern A"]


# --- purity: never runs git add or git commit ---------------------------------------


def test_build_commit_plan_never_calls_git_add_or_commit(monkeypatch):
    real_run = subprocess.run

    def _guarded_run(argv, *a, **k):
        if isinstance(argv, list) and any(x in argv for x in ("add", "commit")):
            raise AssertionError("build_commit_plan must never invoke git add or git commit")
        return real_run(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", _guarded_run)
    with _with_staged_unstaged((["a.py"], [])):
        build_commit_plan(_check())


def test_build_commit_plan_never_writes_files(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    before = sorted(p.name for p in tmp_path.iterdir())
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
        check = build_precommit_check(review)
        build_commit_plan(check)
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after


# --- end-to-end against real temp directories ----------------------------------------


def test_end_to_end_no_git_repo(tmp_path):
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
        check = build_precommit_check(review)
        plan = build_commit_plan(check)
    assert plan.is_git_repo is False
    assert plan.can_commit is False


def test_end_to_end_untracked_file_not_staged_blocks_commit(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
        check = build_precommit_check(review)
        plan = build_commit_plan(check)
    assert plan.staged_files == []
    assert plan.unstaged_files == ["notes.txt"]
    assert plan.can_commit is False


def test_end_to_end_staged_file_allows_commit(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    subprocess.run(["git", "add", "notes.txt"], cwd=tmp_path, check=True)
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
        check = build_precommit_check(review)
        plan = build_commit_plan(check)
    assert plan.staged_files == ["notes.txt"]
    assert plan.can_commit is True


def test_end_to_end_clean_tree_after_commit_blocks_further_commit(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)
    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
        check = build_precommit_check(review)
        plan = build_commit_plan(check)
    assert plan.staged_files == []
    assert plan.can_commit is False


def test_end_to_end_mixed_staged_and_unstaged_changes_to_same_file(tmp_path):
    _init_git_repo(tmp_path)
    f = tmp_path / "tracked.txt"
    f.write_text("v1\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    f.write_text("v2\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    f.write_text("v3\n")  # further unstaged edit on top of the staged one

    scan = scan_project(tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        review = build_review(scan)
        check = build_precommit_check(review)
        plan = build_commit_plan(check)
    assert plan.staged_files == ["tracked.txt"]
    assert plan.unstaged_files == ["tracked.txt"]
    assert plan.can_commit is True


# --- determinism -----------------------------------------------------------------------


def test_deterministic_same_inputs_same_result():
    with _with_staged_unstaged((["a.py"], [])):
        check = _check()
        first = build_commit_plan(check)
        second = build_commit_plan(check)
    assert first == second
