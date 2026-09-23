"""Tests for the REPL 'morning' keyword: main() dispatches to
run_morning_routine(agent, history) exactly like the 'voice' keyword
dispatches to run_voice_mode, then returns to the normal typed REPL
loop. Mirrors test_cli_voice_argument.py's REPL-keyword tests.
LLMClient and run_morning_routine are mocked - no real Anthropic API
call, no real speaker output."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.cli.main import main


def test_repl_morning_keyword_calls_run_morning_routine(monkeypatch):
    inputs = iter(["morning", "exit"])
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
    with patch("jarvis.cli.main.LLMClient"):
        with patch("jarvis.cli.main.load_history") as mock_load_history:
            mock_load_history.return_value = MagicMock(warning=None, history=[])
            with patch("jarvis.cli.main.run_morning_routine") as mock_run_morning:
                main()
    mock_run_morning.assert_called_once()


def test_repl_returns_to_typed_prompt_after_morning_routine_returns(monkeypatch):
    inputs = iter(["morning", "exit"])
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
    with patch("jarvis.cli.main.LLMClient"):
        with patch("jarvis.cli.main.load_history") as mock_load_history:
            mock_load_history.return_value = MagicMock(warning=None, history=[])
            with patch("jarvis.cli.main.run_morning_routine"):
                main()  # must reach 'exit' and return normally, not hang/crash


def test_morning_keyword_never_calls_run_voice_mode(monkeypatch):
    inputs = iter(["morning", "exit"])
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
    with patch("jarvis.cli.main.LLMClient"):
        with patch("jarvis.cli.main.load_history") as mock_load_history:
            mock_load_history.return_value = MagicMock(warning=None, history=[])
            with patch("jarvis.cli.main.run_morning_routine"):
                with patch("jarvis.cli.main.run_voice_mode") as mock_run_voice_mode:
                    main()
    mock_run_voice_mode.assert_not_called()
