"""Tests for the 'jarvis scan' CLI argument (distinct from the 'scan'
REPL keyword tested in test_cli_scan_command.py): main() dispatches to
run_cli_scan() when sys.argv[1] == 'scan', which performs the
deterministic scan and prints it without entering the REPL or requiring
an API key. Verifies the no-args REPL path is unaffected (existing
behavior preserved)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.cli.main import main, run_cli_scan


def test_run_cli_scan_prints_formatted_result(capsys):
    with patch("jarvis.cli.main.scan_project") as mock_scan:
        with patch("jarvis.cli.main.format_scan_result") as mock_format:
            mock_scan.return_value = "a scan result object"
            mock_format.return_value = "formatted output here"
            run_cli_scan()

    mock_format.assert_called_once_with("a scan result object")
    captured = capsys.readouterr()
    assert "formatted output here" in captured.out


def test_run_cli_scan_does_not_require_api_key(monkeypatch):
    # No ANTHROPIC_API_KEY set at all - run_cli_scan must still work,
    # since it performs no LLM call.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("jarvis.cli.main.scan_project"), patch("jarvis.cli.main.format_scan_result", return_value="x"):
        run_cli_scan()  # must not raise


def test_main_dispatches_to_scan_when_argv_is_scan(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "scan"])
    with patch("jarvis.cli.main.run_cli_scan") as mock_run_scan:
        main()
    mock_run_scan.assert_called_once()


def test_main_does_not_start_repl_when_scanning(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "scan"])
    with patch("jarvis.cli.main.run_cli_scan"):
        with patch("jarvis.cli.main.LLMClient") as mock_llm:
            main()
    mock_llm.assert_not_called()  # REPL setup (which constructs LLMClient) never ran


def test_main_with_no_args_does_not_call_run_cli_scan(monkeypatch):
    # Existing no-args REPL behavior must be unaffected by the new
    # argument handling - it should never touch run_cli_scan.
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    with patch("jarvis.cli.main.run_cli_scan") as mock_run_scan:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                main()
    mock_run_scan.assert_not_called()


def test_main_with_unrelated_argument_falls_through_to_repl(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "--version"])
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    with patch("jarvis.cli.main.run_cli_scan") as mock_run_scan:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                main()
    mock_run_scan.assert_not_called()
