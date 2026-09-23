"""Tests for the 'jarvis task "<description>"' CLI argument: main()
dispatches to run_cli_task() when sys.argv[1] == 'task', which builds a
Task via TaskPlanner (jarvis.core.task_planner) - itself reusing
scan_project()/build_plan() (same as 'jarvis work') plus
parse_task_intent()/build_execution_plan() internally - without any API
key or REPL, and never runs shell/git/file-mutating code itself. Mirrors
test_cli_work_argument.py's approach. Also verifies 'jarvis scan'/'plan'/
'work' continue to work correctly alongside the new 'task' argument
(existing behavior preserved)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.cli.main import (
    main,
    run_cli_plan,
    run_cli_scan,
    run_cli_task,
    run_cli_work,
)


def test_run_cli_task_wires_task_planner_and_format_task(capsys):
    with patch("jarvis.cli.main.TaskPlanner") as mock_planner_cls:
        with patch("jarvis.cli.main.format_task") as mock_format:
            mock_planner = mock_planner_cls.return_value
            mock_planner.plan.return_value = "a task"
            mock_format.return_value = "formatted task output"
            run_cli_task("write a README")

    mock_planner_cls.assert_called_once_with()
    mock_planner.plan.assert_called_once_with("write a README")
    mock_format.assert_called_once_with("a task")
    captured = capsys.readouterr()
    assert "formatted task output" in captured.out


def test_run_cli_task_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run_cli_task("write a README")  # must not raise


def test_main_dispatches_to_task_when_argv_is_task(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "task", "write a README"])
    with patch("jarvis.cli.main.run_cli_task") as mock_run_task:
        main()
    mock_run_task.assert_called_once_with("write a README")


def test_main_task_with_no_description_shows_usage_and_does_not_call_run_cli_task(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "task"])
    with patch("jarvis.cli.main.run_cli_task") as mock_run_task:
        main()
    mock_run_task.assert_not_called()
    captured = capsys.readouterr()
    assert "usage" in captured.out.lower()


def test_main_task_with_blank_description_shows_usage(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "task", "   "])
    with patch("jarvis.cli.main.run_cli_task") as mock_run_task:
        main()
    mock_run_task.assert_not_called()
    captured = capsys.readouterr()
    assert "usage" in captured.out.lower()


def test_main_does_not_start_repl_when_running_task(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "task", "write a README"])
    with patch("jarvis.cli.main.run_cli_task"):
        with patch("jarvis.cli.main.LLMClient") as mock_llm:
            main()
    mock_llm.assert_not_called()


def test_main_task_does_not_call_run_cli_scan_plan_or_work(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "task", "write a README"])
    with patch("jarvis.cli.main.run_cli_task"):
        with patch("jarvis.cli.main.run_cli_scan") as mock_run_scan:
            with patch("jarvis.cli.main.run_cli_plan") as mock_run_plan:
                with patch("jarvis.cli.main.run_cli_work") as mock_run_work:
                    main()
    mock_run_scan.assert_not_called()
    mock_run_plan.assert_not_called()
    mock_run_work.assert_not_called()


def test_main_work_does_not_call_run_cli_task(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "work"])
    with patch("jarvis.cli.main.run_cli_work"):
        with patch("jarvis.cli.main.run_cli_task") as mock_run_task:
            main()
    mock_run_task.assert_not_called()


def test_main_with_no_args_does_not_call_run_cli_task(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    with patch("jarvis.cli.main.run_cli_task") as mock_run_task:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                main()
    mock_run_task.assert_not_called()


def test_task_reuses_the_exact_same_scan_project_function_as_scan_plan_and_work():
    import jarvis.cli.main as main_module
    import jarvis.core.task_planner as task_planner_module

    assert run_cli_scan.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_plan.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_work.__globals__["scan_project"] is main_module.scan_project
    # TaskPlanner (used internally by run_cli_task) reuses the exact same
    # scan_project function too - not a separate copy or reimplementation.
    assert task_planner_module.scan_project is main_module.scan_project


def test_task_reuses_the_exact_same_build_plan_function_as_plan_and_work():
    import jarvis.cli.main as main_module
    import jarvis.core.task_planner as task_planner_module

    assert run_cli_plan.__globals__["build_plan"] is main_module.build_plan
    assert run_cli_work.__globals__["build_plan"] is main_module.build_plan
    assert task_planner_module.build_plan is main_module.build_plan


# --- real, non-mocked end-to-end smoke tests --------------------------------


def test_run_cli_task_end_to_end_blocks_external_service(capsys):
    run_cli_task("post an update to Instagram")
    captured = capsys.readouterr()
    assert "instagram" in captured.out.lower()
    assert "cannot proceed" in captured.out.lower()


def test_run_cli_task_end_to_end_blocks_shell_execution(capsys):
    run_cli_task("run a shell command to list files")
    captured = capsys.readouterr()
    assert "shell" in captured.out.lower()
    assert "cannot proceed" in captured.out.lower()


def test_run_cli_task_end_to_end_never_calls_subprocess(monkeypatch):
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("run_cli_task must never call subprocess directly for task analysis")

    # scan_project legitimately calls subprocess (read-only git inspection) -
    # only forbid it from ever running a mutating/shell-adjacent command by
    # checking argv doesn't include any write/delete-capable verb.
    real_run = subprocess.run

    def _guarded(argv, *a, **k):
        joined = " ".join(argv) if isinstance(argv, list) else str(argv)
        forbidden = ("commit", "push", "rm ", "delete")
        if any(word in joined.lower() for word in forbidden):
            raise AssertionError(f"run_cli_task must never run a mutating command: {argv}")
        return real_run(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", _guarded)
    run_cli_task("delete the old draft file and commit it")
