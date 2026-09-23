"""Tests for the 'git-init' entry in the shell tool allowlist: no
arguments accepted, refuses if already a git repository, approval gating,
and a real end-to-end initialization against an isolated temp directory
(monkeypatching JARVIS_ROOT so both the .git-exists check and the actual
subprocess cwd point at the isolated directory, never the real project)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.tools.shell import ALLOWED_COMMANDS, ShellTool, _git_init


def _approve(*a, **k):
    return "y"


def _deny(*a, **k):
    return "n"


def test_git_init_is_in_the_allowlist():
    assert "git-init" in ALLOWED_COMMANDS


# --- no-arguments requirement -------------------------------------------------


@pytest.mark.parametrize("args", [["--bare"], ["-b", "main"], ["--template=x"], ["anything"]])
def test_git_init_rejects_any_arguments(args, tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.tools.shell.JARVIS_ROOT", tmp_path)
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="git-init", args=args)
    assert not result.ok
    assert "does not accept any arguments" in result.output
    mock_run.assert_not_called()


def test_git_init_with_args_never_reaches_approval_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.tools.shell.JARVIS_ROOT", tmp_path)
    tool = ShellTool()
    with patch("builtins.input") as mock_input:
        tool.run(command="git-init", args=["--bare"])
    mock_input.assert_not_called()


# --- refuses if already a repo ------------------------------------------------


def test_git_init_refuses_when_already_a_git_repo(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.tools.shell.JARVIS_ROOT", tmp_path)
    (tmp_path / ".git").mkdir()

    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="git-init", args=[])
    assert not result.ok
    assert "already a git repository" in result.output
    mock_run.assert_not_called()


def test_git_init_already_a_repo_never_reaches_approval_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.tools.shell.JARVIS_ROOT", tmp_path)
    (tmp_path / ".git").mkdir()

    tool = ShellTool()
    with patch("builtins.input") as mock_input:
        tool.run(command="git-init", args=[])
    mock_input.assert_not_called()


# --- argv shape ---------------------------------------------------------------


def test_git_init_argv_shape(tmp_path, monkeypatch):
    # _git_init() itself checks JARVIS_ROOT/.git (see its own
    # docstring/implementation) - isolated to tmp_path here like every
    # other test in this file, so this test's outcome doesn't depend on
    # whether the real JARVIS project directory happens to be a git repo
    # or not.
    monkeypatch.setattr("jarvis.tools.shell.JARVIS_ROOT", tmp_path)
    argv = _git_init([])
    assert argv == ["git", "init"]


# --- approval + interrupt -------------------------------------------------------


def test_git_init_requires_approval(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.tools.shell.JARVIS_ROOT", tmp_path)
    tool = ShellTool()
    with patch("builtins.input", side_effect=_deny):
        with patch("subprocess.run") as mock_run:
            result = tool.run(command="git-init", args=[])
    assert not result.ok
    assert "Denied by user" in result.output
    mock_run.assert_not_called()


def test_git_init_keyboard_interrupt_propagates(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.tools.shell.JARVIS_ROOT", tmp_path)
    tool = ShellTool()
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        with patch("subprocess.run") as mock_run:
            with pytest.raises(KeyboardInterrupt):
                tool.run(command="git-init", args=[])
    mock_run.assert_not_called()


# --- real end-to-end initialization -------------------------------------------


def test_real_git_init_creates_a_git_repo(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.tools.shell.JARVIS_ROOT", tmp_path)
    assert not (tmp_path / ".git").exists()

    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(command="git-init", args=[])

    assert result.ok
    assert (tmp_path / ".git").is_dir()


def test_real_git_init_second_call_is_refused_not_reinitialized(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.tools.shell.JARVIS_ROOT", tmp_path)
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        first = tool.run(command="git-init", args=[])
    assert first.ok

    with patch("builtins.input") as mock_input:
        second = tool.run(command="git-init", args=[])
    assert not second.ok
    assert "already a git repository" in second.output
    mock_input.assert_not_called()
