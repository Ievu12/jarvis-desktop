"""Tests for the 'pytest' entry in the shell tool allowlist: correct
interpreter invocation, safe-flag restriction (including the -k value
handling), approval gating, and that it's actually reachable through
ShellTool the same way other allowlisted commands are."""

from __future__ import annotations

import sys
from unittest.mock import patch

from jarvis.config import JARVIS_ROOT
from jarvis.tools.shell import ALLOWED_COMMANDS, ShellTool


def _approve(*a, **k):
    return "y"


def _deny(*a, **k):
    return "n"


def test_pytest_is_in_the_allowlist():
    assert "pytest" in ALLOWED_COMMANDS


def test_pytest_invoked_via_current_interpreter():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            tool.run(command="pytest", args=[])
    argv = mock_run.call_args[0][0]
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pytest"]


def test_pytest_requires_approval():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_deny):
        with patch("subprocess.run") as mock_run:
            result = tool.run(command="pytest", args=[])
    assert not result.ok
    assert "Denied by user" in result.output
    mock_run.assert_not_called()


def test_pytest_disallowed_flag_denied_without_running():
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="pytest", args=["--cov"])
    assert not result.ok
    assert "not permitted" in result.output
    mock_run.assert_not_called()


def test_pytest_disallowed_flag_never_reaches_approval_prompt():
    tool = ShellTool()
    with patch("builtins.input") as mock_input:
        tool.run(command="pytest", args=["-p", "no:cacheprovider"])
    mock_input.assert_not_called()


def test_pytest_allows_safe_flags():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            result = tool.run(command="pytest", args=["-v", "-x"])
    assert result.ok
    argv = mock_run.call_args[0][0]
    assert "-v" in argv
    assert "-x" in argv


def test_pytest_dash_k_value_is_not_treated_as_a_flag():
    # -k's value (e.g. "test_foo") does not start with '-' so it passes
    # through the flag check unexamined - only flags are validated.
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            result = tool.run(command="pytest", args=["-k", "test_something"])
    assert result.ok
    argv = mock_run.call_args[0][0]
    assert "-k" in argv
    assert "test_something" in argv


def test_pytest_dash_k_with_disallowed_value_that_looks_like_a_flag_is_rejected():
    # If someone tries to smuggle a disallowed flag in as if it were -k's
    # value's *next* token, it's still validated as its own arg position.
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="pytest", args=["-k", "test_x", "--cov"])
    assert not result.ok
    mock_run.assert_not_called()


def test_pytest_path_argument_outside_sandbox_denied():
    tool = ShellTool()
    outside = str(JARVIS_ROOT.parent / "some_other_project" / "test_x.py")
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="pytest", args=[outside])
    assert not result.ok
    assert "outside the JARVIS sandbox" in result.output
    mock_run.assert_not_called()


def test_pytest_real_execution_runs_against_this_projects_own_suite():
    # No mocking of subprocess.run here - a genuine end-to-end check that
    # the shell tool can actually invoke this project's test suite. Scoped
    # to a single fast, deterministic test file to keep this quick.
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(command="pytest", args=["tests/test_secrets.py", "-q"])
    assert result.ok
    assert "passed" in result.output.lower()
