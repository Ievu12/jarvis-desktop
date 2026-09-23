"""Tests for jarvis.cli.morning_briefing: the non-interactive entry
point meant for an automated (e.g. Windows Task Scheduler) morning
briefing run. LLMClient, Agent, build_registry, and run_morning_routine
are all mocked - no real Anthropic API call, no real speaker output, no
real Instagram/Gmail/Calendar access. Confirms: it exits 1 (never
raises) when ANTHROPIC_API_KEY is missing without calling
run_morning_routine, exits 0 on a normal run, and sets up Agent with the
same registry/history shape jarvis.cli.main.run_cli_voice does."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.cli import morning_briefing


# --- missing API key: fails clearly, never calls run_morning_routine ----------------


def test_run_returns_1_when_api_key_missing():
    with patch("jarvis.cli.morning_briefing.ANTHROPIC_API_KEY", None):
        with patch("jarvis.cli.morning_briefing.run_morning_routine") as mock_run:
            exit_code = morning_briefing.run()
    assert exit_code == 1
    mock_run.assert_not_called()


def test_run_prints_clear_message_when_api_key_missing(capsys):
    with patch("jarvis.cli.morning_briefing.ANTHROPIC_API_KEY", None):
        with patch("jarvis.cli.morning_briefing.run_morning_routine"):
            morning_briefing.run()
    captured = capsys.readouterr()
    assert "ANTHROPIC_API_KEY" in captured.out


# --- normal run: sets up agent/history, calls run_morning_routine, exits 0 ----------


def test_run_calls_run_morning_routine_with_agent_and_history():
    with patch("jarvis.cli.morning_briefing.ANTHROPIC_API_KEY", "fake-key"):
        with patch("jarvis.cli.morning_briefing.LLMClient"):
            with patch("jarvis.cli.morning_briefing.Agent") as MockAgent:
                with patch("jarvis.cli.morning_briefing.load_history") as mock_load_history:
                    mock_load_history.return_value = MagicMock(warning=None, history=[])
                    with patch("jarvis.cli.morning_briefing.run_morning_routine") as mock_run:
                        exit_code = morning_briefing.run()

    assert exit_code == 0
    mock_run.assert_called_once()
    call_args = mock_run.call_args.args
    assert call_args[0] is MockAgent.return_value
    assert call_args[1] == []


def test_run_never_prints_the_api_key(capsys):
    with patch("jarvis.cli.morning_briefing.ANTHROPIC_API_KEY", "super-secret-anthropic-key"):
        with patch("jarvis.cli.morning_briefing.LLMClient"):
            with patch("jarvis.cli.morning_briefing.Agent"):
                with patch("jarvis.cli.morning_briefing.load_history") as mock_load_history:
                    mock_load_history.return_value = MagicMock(warning=None, history=[])
                    with patch("jarvis.cli.morning_briefing.run_morning_routine"):
                        morning_briefing.run()
    captured = capsys.readouterr()
    assert "super-secret-anthropic-key" not in captured.out


def test_main_calls_sys_exit_with_runs_return_value():
    with patch("jarvis.cli.morning_briefing.run", return_value=0) as mock_run:
        with patch("jarvis.cli.morning_briefing.sys.exit") as mock_exit:
            morning_briefing.main()
    mock_run.assert_called_once()
    mock_exit.assert_called_once_with(0)


# --- never touches Gmail/Calendar/Instagram connectors directly, never LLM-free ------


def test_module_does_not_import_gmail_or_calendar_connectors():
    module_globals = vars(morning_briefing)
    assert "GmailConnector" not in module_globals
    assert "GoogleCalendarConnector" not in module_globals


def test_module_does_not_import_instagram_connector_directly():
    module_globals = vars(morning_briefing)
    assert "InstagramConnector" not in module_globals
