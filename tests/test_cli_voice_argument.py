"""Tests for the 'jarvis voice' CLI argument and the REPL 'voice'
keyword: main() dispatches to run_cli_voice() when sys.argv[1] ==
'voice', which sets up the same LLMClient/Agent/registry/history the
normal REPL uses and then hands control to run_voice_mode() instead of
the typed input() loop. Mirrors test_cli_scan_argument.py's pattern.
LLMClient, Agent, and run_voice_mode are all mocked - no real Anthropic
API call, no real microphone/speaker access."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.cli.main import main, run_cli_voice


def test_run_cli_voice_calls_run_voice_mode_with_agent_and_history():
    with patch("jarvis.cli.main.LLMClient"):
        with patch("jarvis.cli.main.Agent") as MockAgent:
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                with patch("jarvis.cli.main.run_voice_mode") as mock_run_voice_mode:
                    run_cli_voice()

    mock_run_voice_mode.assert_called_once()
    call_args = mock_run_voice_mode.call_args.args
    assert call_args[0] is MockAgent.return_value
    assert call_args[1] == []


def test_main_dispatches_to_voice_when_argv_is_voice(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "voice"])
    with patch("jarvis.cli.main.run_cli_voice") as mock_run_cli_voice:
        main()
    mock_run_cli_voice.assert_called_once()


def test_main_with_no_args_does_not_call_run_cli_voice(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    with patch("jarvis.cli.main.run_cli_voice") as mock_run_cli_voice:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                main()
    mock_run_cli_voice.assert_not_called()


def test_main_with_scan_argument_does_not_call_run_cli_voice(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "scan"])
    with patch("jarvis.cli.main.run_cli_scan"):
        with patch("jarvis.cli.main.run_cli_voice") as mock_run_cli_voice:
            main()
    mock_run_cli_voice.assert_not_called()


# --- REPL 'voice' keyword -------------------------------------------------------


def test_repl_voice_keyword_calls_run_voice_mode(monkeypatch):
    inputs = iter(["voice", "exit"])
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
    with patch("jarvis.cli.main.LLMClient"):
        with patch("jarvis.cli.main.load_history") as mock_load_history:
            mock_load_history.return_value = MagicMock(warning=None, history=[])
            with patch("jarvis.cli.main.run_voice_mode") as mock_run_voice_mode:
                main()
    mock_run_voice_mode.assert_called_once()


def test_repl_returns_to_typed_prompt_after_voice_mode_returns(monkeypatch):
    # run_voice_mode() returning (person said a stop phrase, or Ctrl+C
    # inside it) must fall back to the normal typed REPL loop, not exit
    # the whole program.
    inputs = iter(["voice", "exit"])
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
    with patch("jarvis.cli.main.LLMClient"):
        with patch("jarvis.cli.main.load_history") as mock_load_history:
            mock_load_history.return_value = MagicMock(warning=None, history=[])
            with patch("jarvis.cli.main.run_voice_mode"):
                main()  # must reach 'exit' and return normally, not hang/crash
