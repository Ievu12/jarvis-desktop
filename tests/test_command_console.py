"""Tests for jarvis.core.command_console.handle_command: pure
orchestration of the existing TaskPlanner -> TaskRunner chain. No new
reasoning, no new safety rule - these tests verify the wiring (blocked
tasks never reach TaskRunner, an approve_fn is forwarded as-is, real
side effects never occur) rather than re-testing TaskPlanner/TaskRunner's
own behavior, which already has its own test suites."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.core.command_console import handle_command


# --- blocked requests never reach TaskRunner -----------------------------------------------------


def test_blocked_request_never_invokes_task_runner():
    with patch("jarvis.core.command_console.TaskRunner") as mock_runner_cls:
        output = handle_command("post an update to Instagram")

    mock_runner_cls.assert_not_called()
    assert "instagram" in output.lower()
    assert "cannot proceed" in output.lower()


def test_blocked_shell_request_never_invokes_task_runner():
    with patch("jarvis.core.command_console.TaskRunner") as mock_runner_cls:
        output = handle_command("run a shell command to list files")

    mock_runner_cls.assert_not_called()
    assert "shell" in output.lower()


def test_lithuanian_blocked_request_never_invokes_task_runner():
    with patch("jarvis.core.command_console.TaskRunner") as mock_runner_cls:
        output = handle_command("paskelbk įrašą Instagram paskyroje")

    mock_runner_cls.assert_not_called()
    assert "instagram" in output.lower()


# --- supported requests reach TaskRunner, approve_fn forwarded -----------------------------------------------------


def test_supported_request_invokes_task_runner_with_forwarded_approve_fn():
    def _approve(step):
        return True

    with patch("jarvis.core.command_console.TaskRunner") as mock_runner_cls:
        mock_runner = mock_runner_cls.return_value
        mock_runner.run.return_value = "a task run"
        with patch("jarvis.core.command_console.format_task_run", return_value="formatted") as mock_format:
            output = handle_command("write a README describing the project", approve_fn=_approve)

    mock_runner_cls.assert_called_once_with(approve_fn=_approve)
    mock_format.assert_called_once_with("a task run")
    assert output == "formatted"


def test_no_approve_fn_forwards_none_so_task_runner_uses_its_own_default():
    with patch("jarvis.core.command_console.TaskRunner") as mock_runner_cls:
        mock_runner_cls.return_value.run.return_value = "a task run"
        with patch("jarvis.core.command_console.format_task_run", return_value="x"):
            handle_command("write a README describing the project")

    mock_runner_cls.assert_called_once_with(approve_fn=None)


# --- real, non-mocked end-to-end scenarios -----------------------------------------------------


def _always_approve(step):
    return True


def _always_deny(step):
    return False


def test_end_to_end_approved_step_completes():
    output = handle_command("write a README describing the project", approve_fn=_always_approve)
    assert "completed" in output.lower()
    assert "all steps completed" in output.lower()


def test_end_to_end_denied_step_stops_run():
    output = handle_command("write a README describing the project", approve_fn=_always_deny)
    assert "blocked" in output.lower()
    assert "stopped early" in output.lower()


def test_end_to_end_blocked_request_reports_reason_without_prompting():
    # If TaskRunner were reached, an approve_fn that raises would blow up -
    # so a clean, non-raising result here demonstrates it was never called.
    def _boom(step):
        raise AssertionError("must not prompt for a blocked request")

    output = handle_command("post an update to Facebook", approve_fn=_boom)
    assert "facebook" in output.lower()
    assert "cannot proceed" in output.lower()


def test_end_to_end_lithuanian_delete_request_completes():
    # Risk-level correctness for Lithuanian keywords is covered by
    # test_task_safety.py/test_task_intent.py directly - this only
    # confirms the Lithuanian request is recognized as supported (not
    # blocked/unknown) and flows all the way through to a completed run.
    output = handle_command("ištrink seną juodraščio failą", approve_fn=_always_approve)
    assert "completed" in output.lower()
    assert "all steps completed" in output.lower()


def test_end_to_end_never_calls_a_mutating_subprocess_command(monkeypatch):
    import subprocess

    real_run = subprocess.run

    def _guarded(argv, *a, **k):
        joined = " ".join(argv) if isinstance(argv, list) else str(argv)
        forbidden = ("commit", "push", "rm ", "delete", "init")
        if any(word in joined.lower() for word in forbidden):
            raise AssertionError(f"handle_command must never run a mutating command: {argv}")
        return real_run(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", _guarded)
    handle_command("ištrink seną juodraščio failą ir padaryk commit'ą", approve_fn=_always_approve)


def test_returns_a_string():
    output = handle_command("write a README describing the project", approve_fn=_always_approve)
    assert isinstance(output, str)
