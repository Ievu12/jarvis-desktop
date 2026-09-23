"""Tests for the 'jarvis plan' CLI argument: main() dispatches to
run_cli_plan() when sys.argv[1] == 'plan', which reuses scan_project()
and derives a plan from it without any API key or REPL. Mirrors
test_cli_scan_argument.py's approach. Also verifies 'jarvis scan'
continues to work correctly alongside the new 'plan' argument (existing
behavior preserved)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.cli.main import main, run_cli_plan, run_cli_scan


def test_run_cli_plan_reuses_scan_project(capsys):
    with patch("jarvis.cli.main.scan_project") as mock_scan:
        with patch("jarvis.cli.main.build_plan") as mock_build_plan:
            with patch("jarvis.cli.main.format_plan") as mock_format:
                mock_scan.return_value = "a scan result"
                mock_build_plan.return_value = "a plan object"
                mock_format.return_value = "formatted plan output"
                run_cli_plan()

    mock_scan.assert_called_once()
    mock_build_plan.assert_called_once_with("a scan result")
    mock_format.assert_called_once_with("a plan object")
    captured = capsys.readouterr()
    assert "formatted plan output" in captured.out


def test_run_cli_plan_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("jarvis.cli.main.scan_project"), patch("jarvis.cli.main.build_plan"), patch(
        "jarvis.cli.main.format_plan", return_value="x"
    ):
        run_cli_plan()  # must not raise


def test_main_dispatches_to_plan_when_argv_is_plan(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "plan"])
    with patch("jarvis.cli.main.run_cli_plan") as mock_run_plan:
        main()
    mock_run_plan.assert_called_once()


def test_main_does_not_start_repl_when_planning(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "plan"])
    with patch("jarvis.cli.main.run_cli_plan"):
        with patch("jarvis.cli.main.LLMClient") as mock_llm:
            main()
    mock_llm.assert_not_called()


def test_main_plan_does_not_call_run_cli_scan(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "plan"])
    with patch("jarvis.cli.main.run_cli_plan"):
        with patch("jarvis.cli.main.run_cli_scan") as mock_run_scan:
            main()
    mock_run_scan.assert_not_called()


def test_main_scan_does_not_call_run_cli_plan(monkeypatch):
    # Existing 'scan' argument must still work exactly as before and must
    # never touch the new plan path.
    monkeypatch.setattr("sys.argv", ["jarvis", "scan"])
    with patch("jarvis.cli.main.run_cli_scan"):
        with patch("jarvis.cli.main.run_cli_plan") as mock_run_plan:
            main()
    mock_run_plan.assert_not_called()


def test_main_with_no_args_does_not_call_run_cli_plan(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    with patch("jarvis.cli.main.run_cli_plan") as mock_run_plan:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                main()
    mock_run_plan.assert_not_called()


def test_plan_reuses_the_exact_same_scan_project_function_as_scan_command():
    # Both run_cli_scan and run_cli_plan must import/call the identical
    # scan_project symbol - confirms no duplicated/divergent scanning
    # logic exists between the two entry points.
    import jarvis.cli.main as main_module

    assert run_cli_scan.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_plan.__globals__["scan_project"] is main_module.scan_project
