"""Tests for the 'python' entry in the shell tool allowlist: script-file-
only requirement, rejection of -c/-m/arbitrary flags, script arguments
passed through correctly, sandbox enforcement, approval gating, and a
real end-to-end script execution."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.shell import ALLOWED_COMMANDS, ShellTool


def _approve(*a, **k):
    return "y"


def _deny(*a, **k):
    return "n"


def test_python_is_in_the_allowlist():
    assert "python" in ALLOWED_COMMANDS


# --- script-file-only requirement --------------------------------------------


def test_python_with_no_args_is_denied_without_prompt():
    tool = ShellTool()
    with patch("builtins.input") as mock_input:
        with patch("subprocess.run") as mock_run:
            result = tool.run(command="python", args=[])
    assert not result.ok
    assert "requires a script file path" in result.output
    mock_input.assert_not_called()
    mock_run.assert_not_called()


def test_python_rejects_dash_c_inline_code():
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="python", args=["-c", "print(1)"])
    assert not result.ok
    assert "not a script path" in result.output
    mock_run.assert_not_called()


def test_python_rejects_dash_m_module_execution():
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="python", args=["-m", "http.server"])
    assert not result.ok
    mock_run.assert_not_called()


def test_python_rejects_dash_c_never_reaches_approval_prompt():
    tool = ShellTool()
    with patch("builtins.input") as mock_input:
        tool.run(command="python", args=["-c", "import os; os.system('x')"])
    mock_input.assert_not_called()


def test_python_rejects_non_py_file():
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="python", args=["readme.txt"])
    assert not result.ok
    assert "does not look like a .py file" in result.output
    mock_run.assert_not_called()


def test_python_rejects_any_leading_flag_not_just_dash_c_or_dash_m():
    tool = ShellTool()
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="python", args=["--version"])
    assert not result.ok
    mock_run.assert_not_called()


# --- argv shape and script argument passthrough -----------------------------


def test_python_argv_uses_current_interpreter():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            tool.run(command="python", args=["script.py"])
    argv = mock_run.call_args[0][0]
    assert argv[0] == sys.executable
    assert argv[1] == "script.py"


def test_python_script_arguments_passed_through():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_approve):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            tool.run(command="python", args=["script.py", "--flag", "value"])
    argv = mock_run.call_args[0][0]
    assert argv == [sys.executable, "script.py", "--flag", "value"]


# --- sandbox + approval --------------------------------------------------------


def test_python_script_path_outside_sandbox_denied():
    tool = ShellTool()
    outside = str(JARVIS_ROOT.parent / "some_script.py")
    with patch("subprocess.run") as mock_run:
        result = tool.run(command="python", args=[outside])
    assert not result.ok
    assert "outside the JARVIS sandbox" in result.output
    mock_run.assert_not_called()


def test_python_requires_approval():
    tool = ShellTool()
    with patch("builtins.input", side_effect=_deny):
        with patch("subprocess.run") as mock_run:
            result = tool.run(command="python", args=["script.py"])
    assert not result.ok
    assert "Denied by user" in result.output
    mock_run.assert_not_called()


def test_python_keyboard_interrupt_propagates():
    tool = ShellTool()
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        with patch("subprocess.run") as mock_run:
            with pytest.raises(KeyboardInterrupt):
                tool.run(command="python", args=["script.py"])
    mock_run.assert_not_called()


# --- real end-to-end script execution -----------------------------------------


@pytest.fixture
def scratch_script():
    script_dir = JARVIS_ROOT / ".jarvis" / "python_shell_test_scratch"
    script_dir.mkdir(parents=True, exist_ok=True)
    script = script_dir / "sample.py"
    script.write_text(
        "import sys\n"
        "print('hello from script')\n"
        "print('args:', sys.argv[1:])\n",
        encoding="utf-8",
    )
    yield script
    script.unlink(missing_ok=True)
    script_dir.rmdir()


def test_real_python_execution_runs_the_script(scratch_script):
    tool = ShellTool()
    rel_path = str(scratch_script.relative_to(JARVIS_ROOT))
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(command="python", args=[rel_path, "hello", "world"])

    assert result.ok
    assert "hello from script" in result.output
    assert "['hello', 'world']" in result.output


def test_real_python_execution_reports_traceback_on_error():
    script_dir = JARVIS_ROOT / ".jarvis" / "python_error_test_scratch"
    script_dir.mkdir(parents=True, exist_ok=True)
    script = script_dir / "broken.py"
    script.write_text("raise ValueError('deliberate test failure')\n", encoding="utf-8")

    try:
        tool = ShellTool()
        rel_path = str(script.relative_to(JARVIS_ROOT))
        with patch("builtins.input", side_effect=_approve):
            result = tool.run(command="python", args=[rel_path])

        assert not result.ok
        assert "ValueError" in result.output
        assert "deliberate test failure" in result.output
    finally:
        script.unlink(missing_ok=True)
        script_dir.rmdir()
