"""Tests for the CLI's 'history [N]' command parsing (jarvis.cli.main).
Only exercises the parsing/dispatch helper directly - not the full REPL
loop - to keep this fast and focused."""

from __future__ import annotations

from unittest.mock import patch

from jarvis.cli.main import _handle_history_command


def test_bare_history_uses_default_limit():
    with patch("jarvis.cli.main.format_history") as mock_format:
        mock_format.return_value = "some output"
        _handle_history_command("history")
    mock_format.assert_called_once()
    _, kwargs = mock_format.call_args
    from jarvis.core.history_view import DEFAULT_ENTRY_LIMIT

    assert kwargs["limit"] == DEFAULT_ENTRY_LIMIT


def test_history_with_numeric_argument_uses_that_limit():
    with patch("jarvis.cli.main.format_history") as mock_format:
        mock_format.return_value = "some output"
        _handle_history_command("history 5")
    _, kwargs = mock_format.call_args
    assert kwargs["limit"] == 5


def test_history_with_invalid_argument_does_not_call_format_history(capsys):
    with patch("jarvis.cli.main.format_history") as mock_format:
        _handle_history_command("history abc")
    mock_format.assert_not_called()
    captured = capsys.readouterr()
    assert "Usage" in captured.out


def test_history_with_zero_or_negative_clamped_to_at_least_one():
    with patch("jarvis.cli.main.format_history") as mock_format:
        mock_format.return_value = "x"
        _handle_history_command("history -5")
    _, kwargs = mock_format.call_args
    assert kwargs["limit"] >= 1
