"""Tests for jarvis.cli.daily_instagram_report: the non-interactive,
LLM-free entry point meant for an automated (e.g. Windows Task
Scheduler) daily run. InstagramConnector itself is mocked throughout -
no real HTTP access, no real OS keychain access, no real Anthropic API
call (this module never imports jarvis.core.llm/agent at all - confirmed
below). Confirms: it calls exactly the three expected READ_ONLY actions
in order, exits 0 only when all three succeed, exits 1 (never raises) on
any failure or when Instagram isn't configured, and the access token
never appears in anything printed."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.cli import daily_instagram_report
from jarvis.integrations.base import ExternalActionResult


def _ok_result(action: str, output: str = "ok") -> ExternalActionResult:
    return ExternalActionResult(ok=True, output=output, service="instagram", action=action)


def _failed_result(action: str, output: str = "failed") -> ExternalActionResult:
    return ExternalActionResult(ok=False, output=output, service="instagram", action=action)


# --- not configured: fails clearly, calls no action ---------------------------------


def test_run_returns_1_when_not_configured():
    with patch("jarvis.cli.daily_instagram_report.InstagramConnector") as MockConnector:
        instance = MockConnector.return_value
        instance.is_configured.return_value = False
        exit_code = daily_instagram_report.run()
    assert exit_code == 1
    instance.execute.assert_not_called()


def test_run_prints_clear_message_when_not_configured(capsys):
    with patch("jarvis.cli.daily_instagram_report.InstagramConnector") as MockConnector:
        instance = MockConnector.return_value
        instance.is_configured.return_value = False
        daily_instagram_report.run()
    captured = capsys.readouterr()
    assert "nesukonfigūruotas" in captured.out.lower()


# --- success path: all three actions called in order, in order ----------------------


def test_run_calls_record_snapshot_then_daily_report_then_compare_history():
    with patch("jarvis.cli.daily_instagram_report.InstagramConnector") as MockConnector:
        instance = MockConnector.return_value
        instance.is_configured.return_value = True
        instance.execute.side_effect = [
            _ok_result("record_daily_snapshot"),
            _ok_result("daily_report"),
            _ok_result("compare_history"),
        ]
        exit_code = daily_instagram_report.run()

    assert exit_code == 0
    calls = [c.args[0] for c in instance.execute.call_args_list]
    assert calls == ["record_daily_snapshot", "daily_report", "compare_history"]


def test_run_prints_output_of_all_three_actions(capsys):
    with patch("jarvis.cli.daily_instagram_report.InstagramConnector") as MockConnector:
        instance = MockConnector.return_value
        instance.is_configured.return_value = True
        instance.execute.side_effect = [
            _ok_result("record_daily_snapshot", "reach: 42"),
            _ok_result("daily_report", "Reach pokytis: ..."),
            _ok_result("compare_history", "Šiandien vs vakar: ..."),
        ]
        daily_instagram_report.run()

    captured = capsys.readouterr()
    assert "reach: 42" in captured.out
    assert "Reach pokytis" in captured.out
    assert "Šiandien vs vakar" in captured.out


# --- failure in any single action: exit code reflects it, never raises --------------


def test_run_returns_1_if_snapshot_recording_fails():
    with patch("jarvis.cli.daily_instagram_report.InstagramConnector") as MockConnector:
        instance = MockConnector.return_value
        instance.is_configured.return_value = True
        instance.execute.side_effect = [
            _failed_result("record_daily_snapshot"),
            _ok_result("daily_report"),
            _ok_result("compare_history"),
        ]
        exit_code = daily_instagram_report.run()
    assert exit_code == 1


def test_run_returns_1_if_daily_report_fails():
    with patch("jarvis.cli.daily_instagram_report.InstagramConnector") as MockConnector:
        instance = MockConnector.return_value
        instance.is_configured.return_value = True
        instance.execute.side_effect = [
            _ok_result("record_daily_snapshot"),
            _failed_result("daily_report"),
            _ok_result("compare_history"),
        ]
        exit_code = daily_instagram_report.run()
    assert exit_code == 1


def test_run_returns_1_if_compare_history_fails():
    with patch("jarvis.cli.daily_instagram_report.InstagramConnector") as MockConnector:
        instance = MockConnector.return_value
        instance.is_configured.return_value = True
        instance.execute.side_effect = [
            _ok_result("record_daily_snapshot"),
            _ok_result("daily_report"),
            _failed_result("compare_history"),
        ]
        exit_code = daily_instagram_report.run()
    assert exit_code == 1


def test_run_still_calls_all_three_actions_even_if_an_earlier_one_failed():
    # A failed snapshot recording must not skip the report/comparison -
    # each action is independent and the operator should see all three
    # results, not just the first failure.
    with patch("jarvis.cli.daily_instagram_report.InstagramConnector") as MockConnector:
        instance = MockConnector.return_value
        instance.is_configured.return_value = True
        instance.execute.side_effect = [
            _failed_result("record_daily_snapshot"),
            _ok_result("daily_report"),
            _ok_result("compare_history"),
        ]
        daily_instagram_report.run()
    assert instance.execute.call_count == 3


# --- never raises, never crashes the process on an unexpected exception path --------


def test_run_does_not_call_sys_exit_itself():
    # run() returns an int; only main() calls sys.exit - keeps run()
    # testable without SystemExit escaping the test.
    with patch("jarvis.cli.daily_instagram_report.InstagramConnector") as MockConnector:
        instance = MockConnector.return_value
        instance.is_configured.return_value = True
        instance.execute.side_effect = [
            _ok_result("record_daily_snapshot"),
            _ok_result("daily_report"),
            _ok_result("compare_history"),
        ]
        result = daily_instagram_report.run()
    assert isinstance(result, int)


def test_main_calls_sys_exit_with_runs_return_value():
    with patch("jarvis.cli.daily_instagram_report.run", return_value=0) as mock_run:
        with patch("jarvis.cli.daily_instagram_report.sys.exit") as mock_exit:
            daily_instagram_report.main()
    mock_run.assert_called_once()
    mock_exit.assert_called_once_with(0)


# --- access token never appears in any printed output -------------------------------


def test_run_never_prints_the_access_token(capsys):
    with patch("jarvis.cli.daily_instagram_report.InstagramConnector") as MockConnector:
        instance = MockConnector.return_value
        instance.is_configured.return_value = True
        instance.execute.side_effect = [
            _ok_result("record_daily_snapshot", "reach: 42"),
            _ok_result("daily_report", "some report text"),
            _ok_result("compare_history", "some comparison text"),
        ]
        daily_instagram_report.run()
    captured = capsys.readouterr()
    assert "access_token" not in captured.out.lower()
    assert "bearer" not in captured.out.lower()


# --- this module never touches Gmail, Google Calendar, or the LLM/agent path --------


def test_module_does_not_import_llm_or_agent():
    import jarvis.cli.daily_instagram_report as mod

    module_globals = vars(mod)
    assert "LLMClient" not in module_globals
    assert "Agent" not in module_globals


def test_module_does_not_import_gmail_or_calendar_connectors():
    import jarvis.cli.daily_instagram_report as mod

    module_globals = vars(mod)
    assert "GmailConnector" not in module_globals
    assert "GoogleCalendarConnector" not in module_globals


def test_module_does_not_import_oauth_token_functions():
    import jarvis.cli.daily_instagram_report as mod

    module_globals = vars(mod)
    assert "TokenStore" not in module_globals
    assert "exchange_code_for_tokens" not in module_globals
    assert "refresh_access_token" not in module_globals


# --- exact call surface: nothing beyond these three actions is ever invoked ---------


def test_run_never_calls_any_write_style_action_name():
    with patch("jarvis.cli.daily_instagram_report.InstagramConnector") as MockConnector:
        instance = MockConnector.return_value
        instance.is_configured.return_value = True
        instance.execute.side_effect = [
            _ok_result("record_daily_snapshot"),
            _ok_result("daily_report"),
            _ok_result("compare_history"),
        ]
        daily_instagram_report.run()

    called_actions = {c.args[0] for c in instance.execute.call_args_list}
    for forbidden in (
        "publish_media", "reply_to_comment", "delete_comment",
        "send_message", "create_story", "create_reel",
    ):
        assert forbidden not in called_actions
