"""Tests for the REPL's 'komanda: <text>' prefix
(jarvis.cli.main._handle_command_console_command /
_COMMAND_CONSOLE_PREFIX): routes natural-language (Lithuanian-friendly)
commands through jarvis.core.command_console.handle_command, entirely
separate from the free-form LLM agent conversation path. Mirrors the
lightweight style of other REPL handler functions in this file (none of
which have dedicated unit tests beyond what their underlying formatted
view already covers) while still exercising this handler's own logic:
prefix stripping and the empty-text usage message."""

from __future__ import annotations

from unittest.mock import patch

from jarvis.cli.main import _COMMAND_CONSOLE_PREFIX, _handle_command_console_command


def test_prefix_constant_is_komanda_colon():
    assert _COMMAND_CONSOLE_PREFIX == "komanda:"


def test_strips_prefix_and_forwards_text_to_handle_command(capsys):
    with patch("jarvis.cli.main.handle_command") as mock_handle:
        mock_handle.return_value = "formatted output"
        _handle_command_console_command("komanda: write a README")

    mock_handle.assert_called_once_with("write a README")
    captured = capsys.readouterr()
    assert "formatted output" in captured.out


def test_strips_leading_whitespace_after_prefix(capsys):
    with patch("jarvis.cli.main.handle_command") as mock_handle:
        mock_handle.return_value = "x"
        _handle_command_console_command("komanda:    ištrink seną failą")

    mock_handle.assert_called_once_with("ištrink seną failą")


def test_empty_text_after_prefix_shows_usage_and_does_not_call_handle_command(capsys):
    with patch("jarvis.cli.main.handle_command") as mock_handle:
        _handle_command_console_command("komanda:")

    mock_handle.assert_not_called()
    captured = capsys.readouterr()
    assert "usage" in captured.out.lower()


def test_whitespace_only_text_after_prefix_shows_usage(capsys):
    with patch("jarvis.cli.main.handle_command") as mock_handle:
        _handle_command_console_command("komanda:    ")

    mock_handle.assert_not_called()
    captured = capsys.readouterr()
    assert "usage" in captured.out.lower()


def test_does_not_require_api_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _handle_command_console_command("komanda: post an update to Instagram")  # must not raise
    captured = capsys.readouterr()
    assert "instagram" in captured.out.lower()


# --- integration with main()'s REPL loop -----------------------------------------------------


def test_main_repl_routes_komanda_prefix_to_handler(monkeypatch):
    from unittest.mock import MagicMock

    inputs = iter(["komanda: write a README", "exit"])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(inputs))
    monkeypatch.setattr("sys.argv", ["jarvis"])

    with patch("jarvis.cli.main._handle_command_console_command") as mock_handler:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                from jarvis.cli.main import main

                main()

    mock_handler.assert_called_once_with("komanda: write a README")


def test_main_repl_is_case_insensitive_for_komanda_prefix(monkeypatch):
    from unittest.mock import MagicMock

    inputs = iter(["Komanda: write a README", "exit"])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(inputs))
    monkeypatch.setattr("sys.argv", ["jarvis"])

    with patch("jarvis.cli.main._handle_command_console_command") as mock_handler:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                from jarvis.cli.main import main

                main()

    mock_handler.assert_called_once_with("Komanda: write a README")


def test_main_repl_does_not_route_plain_conversation_to_command_console(monkeypatch):
    from unittest.mock import MagicMock

    inputs = iter(["list the files in this project", "exit"])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(inputs))
    monkeypatch.setattr("sys.argv", ["jarvis"])

    with patch("jarvis.cli.main._handle_command_console_command") as mock_handler:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.Agent") as mock_agent_cls:
                with patch("jarvis.cli.main.load_history") as mock_load_history:
                    mock_load_history.return_value = MagicMock(warning=None, history=[])
                    mock_agent_cls.return_value.step.return_value = "ok"
                    from jarvis.cli.main import main

                    main()

    mock_handler.assert_not_called()
