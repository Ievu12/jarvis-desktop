"""Tests for jarvis.tools.calendar_tools: the three agent-facing Tool
wrappers around GoogleCalendarConnector. Confirms each tool delegates
correctly, none requires approval (all READ_ONLY), and all degrade
gracefully with a clear message when Google Calendar isn't configured -
never attempting a network call or crashing. GoogleCalendarConnector
itself is mocked; no real HTTP access, no real OS keychain access."""

from __future__ import annotations

from unittest.mock import patch

from jarvis.integrations.base import CredentialError, ExternalActionResult
from jarvis.tools.calendar_tools import (
    GetCalendarEventTool,
    ListTodaysCalendarEventsTool,
    ListUpcomingCalendarEventsTool,
)


# --- not configured: clear message, no network attempt -----------------------------


def test_list_todays_not_configured_returns_clear_message():
    with patch("jarvis.tools.calendar_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ListTodaysCalendarEventsTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()
    mock_connector.execute.assert_not_called()


def test_list_upcoming_not_configured():
    with patch("jarvis.tools.calendar_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ListUpcomingCalendarEventsTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_get_event_not_configured():
    with patch("jarvis.tools.calendar_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = GetCalendarEventTool().run(event_id="evt_1")
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_not_configured_message_explains_oauth_is_the_only_path_not_a_password():
    with patch("jarvis.tools.calendar_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ListTodaysCalendarEventsTool().run()
    lowered = result.output.lower()
    assert "oauth" in lowered
    assert "set your password" not in lowered
    assert "enter your password" not in lowered


# --- successful delegation to the connector -----------------------------------------


def test_list_todays_delegates_with_limit():
    with patch("jarvis.tools.calendar_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="google_calendar", action="list_todays_events"
        )
        ListTodaysCalendarEventsTool().run(limit=5)
    mock_connector.execute.assert_called_once_with("list_todays_events", limit=5)


def test_list_todays_defaults_to_ten():
    with patch("jarvis.tools.calendar_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="google_calendar", action="list_todays_events"
        )
        ListTodaysCalendarEventsTool().run()
    mock_connector.execute.assert_called_once_with("list_todays_events", limit=10)


def test_list_upcoming_delegates_with_limit():
    with patch("jarvis.tools.calendar_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="google_calendar", action="list_upcoming_events"
        )
        ListUpcomingCalendarEventsTool().run(limit=3)
    mock_connector.execute.assert_called_once_with("list_upcoming_events", limit=3)


def test_get_event_delegates_with_event_id():
    with patch("jarvis.tools.calendar_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="Summary: Meeting", service="google_calendar", action="get_event"
        )
        result = GetCalendarEventTool().run(event_id="evt_42")
    assert result.ok is True
    assert result.output == "Summary: Meeting"
    mock_connector.execute.assert_called_once_with("get_event", event_id="evt_42")


# --- CredentialError raised by execute() is caught, not propagated -----------------


def test_credential_error_from_execute_is_caught_gracefully():
    with patch("jarvis.tools.calendar_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.side_effect = CredentialError("tokens expired")
        result = ListTodaysCalendarEventsTool().run()
    assert result.ok is False
    assert "tokens expired" in result.output


# --- no approval required for any of the three (all READ_ONLY) -----------------------


def test_none_of_the_three_tools_calls_confirm_side_effect():
    with patch("jarvis.tools.calendar_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="ok", service="google_calendar", action="x"
        )
        with patch("builtins.input") as mock_input:
            ListTodaysCalendarEventsTool().run()
            ListUpcomingCalendarEventsTool().run()
            GetCalendarEventTool().run(event_id="1")
    mock_input.assert_not_called()


# --- tool metadata -----------------------------------------------------------------------


def test_all_three_tools_have_distinct_names():
    names = {
        ListTodaysCalendarEventsTool().name,
        ListUpcomingCalendarEventsTool().name,
        GetCalendarEventTool().name,
    }
    assert names == {
        "list_todays_calendar_events", "list_upcoming_calendar_events", "get_calendar_event",
    }


def test_get_event_requires_event_id_in_schema():
    schema = GetCalendarEventTool().input_schema
    assert schema["required"] == ["event_id"]


def test_other_two_tools_have_no_required_fields():
    for tool in (ListTodaysCalendarEventsTool(), ListUpcomingCalendarEventsTool()):
        assert "required" not in tool.input_schema or tool.input_schema.get("required") == []


def test_no_tool_named_after_a_write_action():
    names = {
        ListTodaysCalendarEventsTool().name,
        ListUpcomingCalendarEventsTool().name,
        GetCalendarEventTool().name,
    }
    for forbidden in (
        "create_calendar_event", "delete_calendar_event", "update_calendar_event",
        "respond_to_calendar_invite",
    ):
        assert forbidden not in names


def test_calendar_tools_use_a_separate_connector_instance_from_gmail():
    # Sanity check that calendar_tools._connector is a GoogleCalendarConnector,
    # never GmailConnector - the two must never share state or credentials.
    from jarvis.integrations.connectors.google_calendar import GoogleCalendarConnector
    from jarvis.tools.calendar_tools import _connector as calendar_connector

    assert isinstance(calendar_connector, GoogleCalendarConnector)
    assert calendar_connector.service_name == "google_calendar"
