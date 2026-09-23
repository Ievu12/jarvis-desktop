"""Tests for the sandboxed shell tool: allowlist enforcement, no shell
interpretation, path-argument sandboxing, PowerShell quote-injection
safety, approval gating, and timeout behavior. Only allowlisted read-only
commands are ever actually executed, and only against files this test
suite creates inside JARVIS_ROOT - never outside it."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.shell import ALLOWED_COMMANDS, ShellTool


@pytest.fixture
def scratch_file():
    path = JARVIS_ROOT / ".jarvis" / "shell_test_scratch.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("hello from the shell tool test\n", encoding="utf-8")
    yield path
    path.unlink(missing_ok=True)


def _approve(*args, **kwargs):
    return "y"


def _deny(*args, **kwargs):
    return "n"


# --- allowlist enforcement ---------------------------------------------------


def test_unknown_command_is_denied_without_running_anything():
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="powershell", args=["-Command", "whoami"])
    assert not result.ok
    assert "not on the shell tool allowlist" in result.output
    mock_run.assert_not_called()


def test_unknown_command_never_reaches_approval_prompt():
    tool = ShellTool()
    with patch("builtins.input") as mock_input:
        tool.run(command="del", args=["x"])
    mock_input.assert_not_called()


def test_all_advertised_commands_are_in_the_allowlist():
    tool = ShellTool()
    schema_enum = tool.input_schema["properties"]["command"]["enum"]
    assert set(schema_enum) == set(ALLOWED_COMMANDS.keys())


# --- no shell interpretation --------------------------------------------------


def test_subprocess_called_with_shell_false():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            tool.run(command="ls", args=["."])
    _, kwargs = mock_run.call_args
    assert kwargs["shell"] is False


def test_argv_is_a_list_not_a_joined_string():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            tool.run(command="ls", args=["."])
    argv = mock_run.call_args[0][0]
    assert isinstance(argv, list)
    assert all(isinstance(part, str) for part in argv)


# --- PowerShell quote-injection safety ---------------------------------------


def test_powershell_single_quote_breakout_is_neutralized():
    tool = ShellTool()
    malicious = "x'; Write-Output 'INJECTED'; '"
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(command="cat", args=[malicious])
    # The injected command must never actually execute as a separate
    # PowerShell statement - it must appear only as literal text (if at
    # all) inside an error message about a bogus path.
    assert not result.ok
    assert "Cannot find path" in result.output or "does not exist" in result.output


def test_cat_requires_one_argument():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            result = tool.run(command="cat", args=[])
    assert not result.ok
    assert "requires exactly one file path argument" in result.output
    mock_run.assert_not_called()


# --- path arguments must resolve inside the sandbox --------------------------


def test_path_argument_outside_sandbox_is_denied():
    tool = ShellTool()
    outside_path = str(JARVIS_ROOT.parent / "outside_secret.txt")
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="cat", args=[outside_path])
    assert not result.ok
    assert "outside the JARVIS sandbox" in result.output
    mock_run.assert_not_called()


def test_path_argument_outside_sandbox_never_reaches_approval_prompt():
    tool = ShellTool()
    outside_path = str(JARVIS_ROOT.parent / "outside_secret.txt")
    with patch("builtins.input") as mock_input:
        tool.run(command="cat", args=[outside_path])
    mock_input.assert_not_called()


def test_dotdot_traversal_argument_is_denied():
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="cat", args=["../../outside.txt"])
    assert not result.ok
    mock_run.assert_not_called()


def test_path_argument_inside_sandbox_is_allowed(scratch_file):
    tool = ShellTool()
    rel_path = str(scratch_file.relative_to(JARVIS_ROOT))
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(command="cat", args=[rel_path])
    assert result.ok
    assert "hello from the shell tool test" in result.output


# --- git flag restriction -----------------------------------------------------


def test_git_log_rejects_disallowed_flag():
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="git-log", args=["--format=%x00custom"])
    assert not result.ok
    assert "not permitted" in result.output
    mock_run.assert_not_called()


def test_git_status_allows_safe_flag():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            result = tool.run(command="git-status", args=["-s"])
    assert result.ok
    argv = mock_run.call_args[0][0]
    assert argv == ["git", "status", "-s"]


# --- approval gating -----------------------------------------------------------


def test_denial_at_approval_prompt_prevents_execution():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_deny):
        with patch("subprocess.run") as mock_run:
            result = tool.run(command="ls", args=["."])
    assert not result.ok
    assert "Denied by user" in result.output
    mock_run.assert_not_called()


def test_keyboard_interrupt_at_approval_prompt_propagates_and_blocks_execution():
    tool = ShellTool()
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        with patch("subprocess.run") as mock_run:
            with pytest.raises(KeyboardInterrupt):
                tool.run(command="ls", args=["."])
    mock_run.assert_not_called()


def test_every_invocation_prompts_even_repeated_same_command():
    tool = ShellTool()
    with patch("builtins.input", side_effect=[_approve(), _approve()]) as mock_input:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            tool.run(command="ls", args=["."])
            tool.run(command="ls", args=["."])
    assert mock_input.call_count == 2


# --- timeout ------------------------------------------------------------------


def test_timeout_is_capped_at_hard_maximum():
    from jarvis.tools.shell import MAX_TIMEOUT_SECONDS

    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            tool.run(command="ls", args=["."], timeout_seconds=99999)
    _, kwargs = mock_run.call_args
    assert kwargs["timeout"] == MAX_TIMEOUT_SECONDS


def test_default_timeout_used_when_not_specified():
    from jarvis.tools.shell import DEFAULT_TIMEOUT_SECONDS

    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            tool.run(command="ls", args=["."])
    _, kwargs = mock_run.call_args
    assert kwargs["timeout"] == DEFAULT_TIMEOUT_SECONDS


def test_timeout_expired_is_reported_as_failure_not_crash():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ls", timeout=30)):
            result = tool.run(command="ls", args=["."])
    assert not result.ok
    assert "timed out" in result.output.lower()


# --- working directory pinning -------------------------------------------------


def test_cwd_is_always_jarvis_root():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            tool.run(command="ls", args=["."])
    _, kwargs = mock_run.call_args
    assert kwargs["cwd"] == JARVIS_ROOT


# --- output capping -------------------------------------------------------------


def test_output_is_truncated_beyond_cap():
    from jarvis.tools.shell import MAX_OUTPUT_CHARS

    tool = ShellTool()
    huge_output = "x" * (MAX_OUTPUT_CHARS + 5000)
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = huge_output
            mock_run.return_value.stderr = ""
            result = tool.run(command="ls", args=["."])
    assert len(result.output) <= MAX_OUTPUT_CHARS + 100  # allow for the truncation note
    assert "truncated" in result.output


# --- real end-to-end execution (no mocking of subprocess) ----------------------


def test_real_ls_execution_inside_sandbox():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(command="ls", args=["."])
    assert result.ok
    assert "JARVIS" in result.output or len(result.output) > 0


def test_real_cat_execution_reads_actual_file_content(scratch_file):
    tool = ShellTool()
    rel_path = str(scratch_file.relative_to(JARVIS_ROOT))
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(command="cat", args=[rel_path])
    assert result.ok
    assert result.output.strip() == "hello from the shell tool test"
