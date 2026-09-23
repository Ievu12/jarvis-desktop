"""Tests for the 'git-add' entry in the shell tool allowlist: explicit-
paths-only requirement, forbidden flags/wildcards, approval gating, and a
real end-to-end stage against an isolated git repo created inside
JARVIS_ROOT's own scratch area."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from unittest.mock import patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.shell import ALLOWED_COMMANDS, ShellTool


def _force_remove_readonly(func, path, exc_info):
    # git internals (.git/objects/**, packed refs) are often written
    # read-only on Windows; rmtree needs write permission to delete them.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _approve(*a, **k):
    return "y"


def _deny(*a, **k):
    return "n"


def test_git_add_is_in_the_allowlist():
    assert "git-add" in ALLOWED_COMMANDS


# --- explicit-paths-only requirement ----------------------------------------


def test_git_add_with_no_args_is_denied_without_prompt():
    tool = ShellTool()
    with patch("builtins.input") as mock_input:
        with patch("subprocess.run") as mock_run:
            result = tool.run(command="git-add", args=[])
    assert not result.ok
    assert "requires at least one explicit file path" in result.output
    mock_input.assert_not_called()
    mock_run.assert_not_called()


@pytest.mark.parametrize("token", [".", "*", "**", "./", ":/"])
def test_git_add_rejects_wildcard_and_whole_tree_tokens(token):
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="git-add", args=[token])
    assert not result.ok
    mock_run.assert_not_called()


# --- forbidden flags ----------------------------------------------------------


@pytest.mark.parametrize("flag", ["-A", "--all", "-u", "--update", "-p", "--patch", "-f", "--force"])
def test_git_add_rejects_forbidden_flags(flag):
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="git-add", args=["file.txt", flag])
    assert not result.ok
    mock_run.assert_not_called()


def test_git_add_forbidden_flag_never_reaches_approval_prompt():
    tool = ShellTool()
    with patch("builtins.input") as mock_input:
        tool.run(command="git-add", args=["file.txt", "--force"])
    mock_input.assert_not_called()


def test_git_add_unknown_flag_rejected_with_no_flags_allowed_message():
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="git-add", args=["file.txt", "--some-unknown-flag"])
    assert not result.ok
    assert "no flags are allowed" in result.output.lower()
    mock_run.assert_not_called()


# --- argv shape ---------------------------------------------------------------


def test_git_add_argv_uses_double_dash_separator():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            tool.run(command="git-add", args=["file.txt"])
    argv = mock_run.call_args[0][0]
    assert argv == ["git", "add", "--", "file.txt"]


def test_git_add_supports_multiple_paths():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            tool.run(command="git-add", args=["a.txt", "b.txt"])
    argv = mock_run.call_args[0][0]
    assert argv == ["git", "add", "--", "a.txt", "b.txt"]


# --- sandbox + approval --------------------------------------------------------


def test_git_add_path_outside_sandbox_denied():
    tool = ShellTool()
    outside = str(JARVIS_ROOT.parent / "outside.txt")
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="git-add", args=[outside])
    assert not result.ok
    assert "outside the JARVIS sandbox" in result.output
    mock_run.assert_not_called()


def test_git_add_requires_approval():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_deny):
        with patch("subprocess.run") as mock_run:
            result = tool.run(command="git-add", args=["file.txt"])
    assert not result.ok
    assert "Denied by user" in result.output
    mock_run.assert_not_called()


def test_git_add_keyboard_interrupt_propagates():
    tool = ShellTool()
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        with patch("subprocess.run") as mock_run:
            with pytest.raises(KeyboardInterrupt):
                tool.run(command="git-add", args=["file.txt"])
    mock_run.assert_not_called()


# --- real end-to-end stage against an isolated git repo -----------------------


@pytest.fixture
def scratch_git_repo():
    repo_dir = JARVIS_ROOT / ".jarvis" / "git_add_test_scratch"
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    yield repo_dir
    if repo_dir.exists():
        shutil.rmtree(repo_dir, onerror=_force_remove_readonly)


def test_real_git_add_stages_a_file(scratch_git_repo, monkeypatch):
    # The shell tool pins cwd to the real JARVIS_ROOT, not this scratch
    # repo, so drive git directly with cwd=scratch_git_repo to prove the
    # underlying git-add argv shape actually stages a file - the argv
    # construction itself is exercised via the tool in the tests above.
    target = scratch_git_repo / "new_file.txt"
    target.write_text("hello", encoding="utf-8")

    from jarvis.tools.shell import _git_add

    argv = _git_add(["new_file.txt"])
    result = subprocess.run(argv, cwd=scratch_git_repo, capture_output=True, text=True)
    assert result.returncode == 0

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=scratch_git_repo, capture_output=True, text=True
    )
    assert "A  new_file.txt" in status.stdout
