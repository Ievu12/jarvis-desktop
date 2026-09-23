"""Tests for the 'git-commit' entry in the shell tool allowlist: mandatory
message requirement, forbidden flags (--amend, -a/--all, --no-verify,
etc.), rejection of any argument shape besides exactly -m "<message>",
approval gating, and a real end-to-end commit against an isolated git
repo created inside JARVIS_ROOT's own scratch area."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from unittest.mock import patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.shell import ALLOWED_COMMANDS, ShellTool, _git_add, _git_commit


def _force_remove_readonly(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _approve(*a, **k):
    return "y"


def _deny(*a, **k):
    return "n"


def test_git_commit_is_in_the_allowlist():
    assert "git-commit" in ALLOWED_COMMANDS


# --- mandatory message requirement -------------------------------------------


def test_git_commit_with_no_args_is_denied_without_prompt():
    tool = ShellTool()
    with patch("builtins.input") as mock_input:
        with patch("subprocess.run") as mock_run:
            result = tool.run(command="git-commit", args=[])
    assert not result.ok
    assert "requires a commit message" in result.output
    mock_input.assert_not_called()
    mock_run.assert_not_called()


def test_git_commit_dash_m_with_no_following_message_denied():
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="git-commit", args=["-m"])
    assert not result.ok
    assert "requires a commit message argument" in result.output
    mock_run.assert_not_called()


def test_git_commit_empty_message_denied():
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="git-commit", args=["-m", "   "])
    assert not result.ok
    assert "must not be empty" in result.output
    mock_run.assert_not_called()


def test_git_commit_empty_message_never_reaches_approval_prompt():
    tool = ShellTool()
    with patch("builtins.input") as mock_input:
        tool.run(command="git-commit", args=["-m", ""])
    mock_input.assert_not_called()


# --- forbidden flags -----------------------------------------------------------


@pytest.mark.parametrize(
    "flag", ["--amend", "-a", "--all", "--no-verify", "-n", "-p", "--patch", "-e", "--edit"]
)
def test_git_commit_rejects_forbidden_flags(flag):
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="git-commit", args=["-m", "a message", flag])
    assert not result.ok
    mock_run.assert_not_called()


def test_git_commit_forbidden_flag_never_reaches_approval_prompt():
    tool = ShellTool()
    with patch("builtins.input") as mock_input:
        tool.run(command="git-commit", args=["-m", "msg", "--amend"])
    mock_input.assert_not_called()


def test_git_commit_rejects_unexpected_extra_argument():
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="git-commit", args=["-m", "msg", "some_file.txt"])
    assert not result.ok
    assert "only" in result.output.lower()
    mock_run.assert_not_called()


# --- argv shape ------------------------------------------------------------------


def test_git_commit_argv_shape():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            tool.run(command="git-commit", args=["-m", "fix the bug"])
    argv = mock_run.call_args[0][0]
    assert argv == ["git", "commit", "-m", "fix the bug"]


def test_git_commit_message_with_spaces_preserved_as_single_argument():
    argv = _git_commit(["-m", "a message with several words in it"])
    assert argv[-1] == "a message with several words in it"
    assert len(argv) == 4  # never split into multiple argv tokens


# --- commit message is not treated as a filesystem path -----------------------


def test_git_commit_message_resembling_a_path_is_not_sandbox_checked():
    # A message that happens to look path-like must not trigger the
    # generic path-boundary check or an outside-sandbox approval prompt -
    # it's free text, not a filesystem argument.
    tool = ShellTool()
    message = "see C:\\Users\\someone\\Desktop\\other-project\\notes.txt for context"
    with patch("builtins.input", side_effect=_approve) as mock_input:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            result = tool.run(command="git-commit", args=["-m", message])
    assert result.ok
    # Exactly one approval prompt (the normal side-effect confirmation) -
    # not a second one for an outside-sandbox path exception.
    assert mock_input.call_count == 1
    argv = mock_run.call_args[0][0]
    assert argv == ["git", "commit", "-m", message]


# --- approval + interrupt -------------------------------------------------------


def test_git_commit_requires_approval():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_deny):
        with patch("subprocess.run") as mock_run:
            result = tool.run(command="git-commit", args=["-m", "msg"])
    assert not result.ok
    assert "Denied by user" in result.output
    mock_run.assert_not_called()


def test_git_commit_keyboard_interrupt_propagates():
    tool = ShellTool()
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        with patch("subprocess.run") as mock_run:
            with pytest.raises(KeyboardInterrupt):
                tool.run(command="git-commit", args=["-m", "msg"])
    mock_run.assert_not_called()


# --- real end-to-end commit against an isolated git repo -----------------------


@pytest.fixture
def scratch_git_repo():
    repo_dir = JARVIS_ROOT / ".jarvis" / "git_commit_test_scratch"
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    yield repo_dir
    if repo_dir.exists():
        shutil.rmtree(repo_dir, onerror=_force_remove_readonly)


def test_real_git_commit_creates_a_commit(scratch_git_repo):
    target = scratch_git_repo / "new_file.txt"
    target.write_text("hello", encoding="utf-8")

    add_argv = _git_add(["new_file.txt"])
    subprocess.run(add_argv, cwd=scratch_git_repo, check=True)

    commit_argv = _git_commit(["-m", "add new_file.txt"])
    result = subprocess.run(commit_argv, cwd=scratch_git_repo, capture_output=True, text=True)
    assert result.returncode == 0

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=scratch_git_repo, capture_output=True, text=True
    )
    assert "add new_file.txt" in log.stdout


def test_real_git_commit_fails_gracefully_with_nothing_staged(scratch_git_repo):
    # No prior git-add - git itself should reject this with a normal
    # non-zero exit, not a crash.
    commit_argv = _git_commit(["-m", "nothing to commit"])
    result = subprocess.run(commit_argv, cwd=scratch_git_repo, capture_output=True, text=True)
    assert result.returncode != 0


def test_real_git_commit_working_tree_unaffected_besides_git_state(scratch_git_repo):
    target = scratch_git_repo / "tracked.txt"
    target.write_text("original content", encoding="utf-8")
    subprocess.run(_git_add(["tracked.txt"]), cwd=scratch_git_repo, check=True)
    subprocess.run(_git_commit(["-m", "initial"]), cwd=scratch_git_repo, check=True)

    # File content must be exactly as written - commit must not modify
    # working-tree file contents in any way.
    assert target.read_text(encoding="utf-8") == "original content"
