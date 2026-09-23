"""Tests for the 'jarvis run "<description>"' CLI argument: main()
dispatches to run_cli_run() when sys.argv[1] == 'run', which builds a
Task via TaskPlanner (same as 'jarvis task') and, unless already blocked,
hands it to TaskRunner for controlled, approval-gated execution (using
the safe DryRunExecutor only - no real side effect). Mirrors
test_cli_task_argument.py's approach. Also verifies 'jarvis scan'/'plan'/
'work'/'task' continue to work correctly alongside the new 'run' argument
(existing behavior preserved)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.cli.main import (
    main,
    run_cli_plan,
    run_cli_run,
    run_cli_scan,
    run_cli_task,
    run_cli_work,
)


def test_run_cli_run_wires_task_planner_and_task_runner_when_not_blocked(capsys):
    with patch("jarvis.cli.main.TaskPlanner") as mock_planner_cls:
        with patch("jarvis.cli.main.TaskRunner") as mock_runner_cls:
            with patch("jarvis.cli.main.format_task_run") as mock_format:
                mock_planner = mock_planner_cls.return_value
                mock_task = MagicMock(status="pending_approval")
                mock_planner.plan.return_value = mock_task
                mock_runner = mock_runner_cls.return_value
                mock_runner.run.return_value = "a task run"
                mock_format.return_value = "formatted task run output"

                run_cli_run("write a README")

    mock_planner_cls.assert_called_once_with()
    mock_planner.plan.assert_called_once_with("write a README")
    mock_runner_cls.assert_called_once_with()
    mock_runner.run.assert_called_once_with(mock_task)
    mock_format.assert_called_once_with("a task run")
    captured = capsys.readouterr()
    assert "formatted task run output" in captured.out


def test_run_cli_run_skips_task_runner_entirely_when_blocked(capsys):
    with patch("jarvis.cli.main.TaskPlanner") as mock_planner_cls:
        with patch("jarvis.cli.main.TaskRunner") as mock_runner_cls:
            with patch("jarvis.cli.main.format_task") as mock_format_task:
                mock_planner = mock_planner_cls.return_value
                mock_task = MagicMock(status="blocked")
                mock_planner.plan.return_value = mock_task
                mock_format_task.return_value = "formatted blocked task"

                run_cli_run("post an update to Instagram")

    mock_runner_cls.assert_not_called()
    mock_format_task.assert_called_once_with(mock_task)
    captured = capsys.readouterr()
    assert "formatted blocked task" in captured.out


def test_run_cli_run_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run_cli_run("post an update to Instagram")  # blocked path, must not raise


def test_main_dispatches_to_run_when_argv_is_run(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "run", "write a README"])
    with patch("jarvis.cli.main.run_cli_run") as mock_run:
        main()
    mock_run.assert_called_once_with("write a README")


def test_main_run_with_no_description_shows_usage_and_does_not_call_run_cli_run(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "run"])
    with patch("jarvis.cli.main.run_cli_run") as mock_run:
        main()
    mock_run.assert_not_called()
    captured = capsys.readouterr()
    assert "usage" in captured.out.lower()


def test_main_run_with_blank_description_shows_usage(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "run", "   "])
    with patch("jarvis.cli.main.run_cli_run") as mock_run:
        main()
    mock_run.assert_not_called()
    captured = capsys.readouterr()
    assert "usage" in captured.out.lower()


def test_main_does_not_start_repl_when_running_run(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "run", "post an update to Instagram"])
    with patch("jarvis.cli.main.run_cli_run"):
        with patch("jarvis.cli.main.LLMClient") as mock_llm:
            main()
    mock_llm.assert_not_called()


def test_main_run_does_not_call_run_cli_scan_plan_work_or_task(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "run", "post an update to Instagram"])
    with patch("jarvis.cli.main.run_cli_run"):
        with patch("jarvis.cli.main.run_cli_scan") as mock_run_scan:
            with patch("jarvis.cli.main.run_cli_plan") as mock_run_plan:
                with patch("jarvis.cli.main.run_cli_work") as mock_run_work:
                    with patch("jarvis.cli.main.run_cli_task") as mock_run_task:
                        main()
    mock_run_scan.assert_not_called()
    mock_run_plan.assert_not_called()
    mock_run_work.assert_not_called()
    mock_run_task.assert_not_called()


def test_main_task_does_not_call_run_cli_run(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "task", "write a README"])
    with patch("jarvis.cli.main.run_cli_task"):
        with patch("jarvis.cli.main.run_cli_run") as mock_run:
            main()
    mock_run.assert_not_called()


def test_main_with_no_args_does_not_call_run_cli_run(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    with patch("jarvis.cli.main.run_cli_run") as mock_run:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                main()
    mock_run.assert_not_called()


def test_run_reuses_the_exact_same_scan_project_function_as_scan_plan_work_and_task():
    import jarvis.cli.main as main_module
    import jarvis.core.task_planner as task_planner_module

    assert run_cli_scan.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_plan.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_work.__globals__["scan_project"] is main_module.scan_project
    # 'jarvis run' reuses TaskPlanner (used internally by 'jarvis task' too),
    # which reuses the exact same scan_project function - not a separate
    # copy or reimplementation.
    assert task_planner_module.scan_project is main_module.scan_project


# --- real, non-mocked end-to-end smoke tests --------------------------------


def test_run_cli_run_end_to_end_blocks_external_service_without_any_prompt(capsys, monkeypatch):
    # No approval prompt should ever happen for a blocked task - if it did,
    # this would hang/raise on EOFError from the patched input.
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=AssertionError("must not prompt")))
    run_cli_run("post an update to Instagram")
    captured = capsys.readouterr()
    assert "instagram" in captured.out.lower()
    assert "cannot proceed" in captured.out.lower()


def test_run_cli_run_end_to_end_blocks_shell_execution_without_any_prompt(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=AssertionError("must not prompt")))
    run_cli_run("run a shell command to list files")
    captured = capsys.readouterr()
    assert "shell" in captured.out.lower()
    assert "cannot proceed" in captured.out.lower()


def test_run_cli_run_end_to_end_approved_step_completes(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", MagicMock(return_value="y"))
    run_cli_run("write a README describing the project")
    captured = capsys.readouterr()
    assert "completed" in captured.out.lower()
    assert "all steps completed" in captured.out.lower()


def test_run_cli_run_end_to_end_denied_step_stops_run(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", MagicMock(return_value="n"))
    run_cli_run("write a README describing the project")
    captured = capsys.readouterr()
    assert "blocked" in captured.out.lower()
    assert "stopped early" in captured.out.lower()


def test_run_cli_run_end_to_end_never_calls_a_mutating_subprocess_command(monkeypatch):
    import subprocess

    real_run = subprocess.run

    def _guarded(argv, *a, **k):
        joined = " ".join(argv) if isinstance(argv, list) else str(argv)
        forbidden = ("commit", "push", "rm ", "delete", "init")
        if any(word in joined.lower() for word in forbidden):
            raise AssertionError(f"run_cli_run must never run a mutating command: {argv}")
        return real_run(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", _guarded)
    monkeypatch.setattr("builtins.input", MagicMock(return_value="y"))
    run_cli_run("delete the old draft file and commit it, then push")


def test_run_cli_run_never_calls_llmclient(monkeypatch):
    monkeypatch.setattr("builtins.input", MagicMock(return_value="y"))
    with patch("jarvis.cli.main.LLMClient") as mock_llm:
        run_cli_run("write a README describing the project")
    mock_llm.assert_not_called()
