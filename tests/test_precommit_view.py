"""Tests for jarvis.core.precommit_view.format_precommit: combines
review_view's diff summary with a real test run and pass/fail status.
Uses monkeypatch on JARVIS_ROOT (in both review_view and
precommit_view, since precommit_view calls format_review() which reads
review_view's own module-level JARVIS_ROOT) to point at isolated temp
git repos - never the real project. Also verifies it reuses the shell
tool's own pytest argv-building logic rather than a second, divergent
invocation path."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from jarvis.core import precommit_view, review_view


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    monkeypatch.setattr(precommit_view, "JARVIS_ROOT", tmp_path)
    return tmp_path


def test_precommit_includes_diff_summary(isolated_root):
    _init_git_repo(isolated_root)
    (isolated_root / "test_x.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")

    result = precommit_view.format_precommit()
    assert "test_x.py" in result  # from the diff/status summary


def test_precommit_reports_passed_when_tests_pass(isolated_root):
    _init_git_repo(isolated_root)
    (isolated_root / "test_x.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")

    result = precommit_view.format_precommit()
    assert "Test run: PASSED" in result


def test_precommit_reports_failed_with_details_when_tests_fail(isolated_root):
    _init_git_repo(isolated_root)
    (isolated_root / "test_x.py").write_text(
        "def test_fail():\n    assert False, 'deliberate failure'\n", encoding="utf-8"
    )

    result = precommit_view.format_precommit()
    assert "Test run: FAILED" in result
    assert "deliberate failure" in result


def test_precommit_combines_both_sections_with_separator(isolated_root):
    _init_git_repo(isolated_root)
    (isolated_root / "test_x.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")

    result = precommit_view.format_precommit()
    diff_part, _, test_part = result.partition("-" * 40)
    assert diff_part.strip()
    assert "Test run:" in test_part


def test_precommit_never_raises_when_not_a_git_repo(isolated_root):
    # No git init - format_precommit must still run the test step and
    # report gracefully, not crash.
    (isolated_root / "test_x.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    result = precommit_view.format_precommit()
    assert "not a git repository" in result
    assert "Test run:" in result  # tests still ran despite no git repo


def test_precommit_reuses_shell_tools_pytest_argv_builder(isolated_root):
    # Confirms the exact same argv-building function the model's 'pytest'
    # shell command uses is what precommit calls - not a second, divergent
    # invocation path.
    _init_git_repo(isolated_root)
    (isolated_root / "test_x.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")

    with patch("jarvis.core.precommit_view._pytest", wraps=precommit_view._pytest) as mock_pytest:
        precommit_view.format_precommit()
    mock_pytest.assert_called_once_with([])


def test_precommit_never_writes_to_the_repo(isolated_root):
    _init_git_repo(isolated_root)
    f = isolated_root / "tracked.txt"
    f.write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=isolated_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=isolated_root, check=True)

    before_log = subprocess.run(
        ["git", "log", "--oneline"], cwd=isolated_root, capture_output=True, text=True
    ).stdout

    precommit_view.format_precommit()

    after_log = subprocess.run(
        ["git", "log", "--oneline"], cwd=isolated_root, capture_output=True, text=True
    ).stdout
    assert before_log == after_log


def test_precommit_test_timeout_reported_gracefully(isolated_root):
    _init_git_repo(isolated_root)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=30)):
        result = precommit_view._run_tests()
    assert "timed out" in result.lower()
