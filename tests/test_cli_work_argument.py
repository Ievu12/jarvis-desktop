"""Tests for the 'jarvis work' CLI argument: main() dispatches to
run_cli_work() when sys.argv[1] == 'work', which reuses scan_project()
and build_plan() without any API key or REPL. Mirrors
test_cli_plan_argument.py's approach. Also verifies 'jarvis scan' and
'jarvis plan' continue to work correctly alongside the new 'work'
argument (existing behavior preserved)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.cli.main import main, run_cli_plan, run_cli_scan, run_cli_work


def test_run_cli_work_reuses_scan_project_and_build_plan(capsys):
    with patch("jarvis.cli.main.scan_project") as mock_scan:
        with patch("jarvis.cli.main.build_plan") as mock_build_plan:
            with patch("jarvis.cli.main.build_work_item") as mock_build_work:
                with patch("jarvis.cli.main.format_work_item") as mock_format:
                    mock_scan.return_value = "a scan result"
                    mock_build_plan.return_value = "a plan object"
                    mock_build_work.return_value = "a work item"
                    mock_format.return_value = "formatted work output"
                    run_cli_work()

    mock_scan.assert_called_once()
    mock_build_plan.assert_called_once_with("a scan result")
    mock_build_work.assert_called_once_with("a plan object")
    mock_format.assert_called_once_with("a work item")
    captured = capsys.readouterr()
    assert "formatted work output" in captured.out


def test_run_cli_work_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("jarvis.cli.main.scan_project"), patch("jarvis.cli.main.build_plan"), patch(
        "jarvis.cli.main.build_work_item"
    ), patch("jarvis.cli.main.format_work_item", return_value="x"):
        run_cli_work()  # must not raise


def test_main_dispatches_to_work_when_argv_is_work(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "work"])
    with patch("jarvis.cli.main.run_cli_work") as mock_run_work:
        main()
    mock_run_work.assert_called_once()


def test_main_does_not_start_repl_when_working(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "work"])
    with patch("jarvis.cli.main.run_cli_work"):
        with patch("jarvis.cli.main.LLMClient") as mock_llm:
            main()
    mock_llm.assert_not_called()


def test_main_work_does_not_call_run_cli_scan_or_plan(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "work"])
    with patch("jarvis.cli.main.run_cli_work"):
        with patch("jarvis.cli.main.run_cli_scan") as mock_run_scan:
            with patch("jarvis.cli.main.run_cli_plan") as mock_run_plan:
                main()
    mock_run_scan.assert_not_called()
    mock_run_plan.assert_not_called()


def test_main_scan_does_not_call_run_cli_work(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "scan"])
    with patch("jarvis.cli.main.run_cli_scan"):
        with patch("jarvis.cli.main.run_cli_work") as mock_run_work:
            main()
    mock_run_work.assert_not_called()


def test_main_plan_does_not_call_run_cli_work(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "plan"])
    with patch("jarvis.cli.main.run_cli_plan"):
        with patch("jarvis.cli.main.run_cli_work") as mock_run_work:
            main()
    mock_run_work.assert_not_called()


def test_main_with_no_args_does_not_call_run_cli_work(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    with patch("jarvis.cli.main.run_cli_work") as mock_run_work:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                main()
    mock_run_work.assert_not_called()


def test_work_reuses_the_exact_same_scan_project_function_as_scan_and_plan():
    import jarvis.cli.main as main_module

    assert run_cli_scan.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_plan.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_work.__globals__["scan_project"] is main_module.scan_project


def test_work_reuses_the_exact_same_build_plan_function_as_plan():
    import jarvis.cli.main as main_module

    assert run_cli_plan.__globals__["build_plan"] is main_module.build_plan
    assert run_cli_work.__globals__["build_plan"] is main_module.build_plan
