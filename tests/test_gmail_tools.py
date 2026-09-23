"""Tests for jarvis.tools.gmail_tools: the five agent-facing Tool
wrappers around GmailConnector. Confirms each tool delegates correctly,
none requires approval (all READ_ONLY), and all degrade gracefully with a
clear message when Gmail isn't configured - never attempting a network
call or crashing. GmailConnector itself is mocked; no real HTTP access,
no real OS keychain access."""

from __future__ import annotations

from unittest.mock import patch

from jarvis.integrations.base import CredentialError, ExternalActionResult
from jarvis.tools.gmail_tools import (
    GetGmailMessageContentTool,
    GetGmailMessageTool,
    ListRecentGmailMessagesTool,
    ListUnreadGmailMessagesTool,
    SearchGmailMessagesTool,
)


# --- not configured: clear message, no network attempt -----------------------------


def test_list_recent_not_configured_returns_clear_message():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ListRecentGmailMessagesTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()
    mock_connector.execute.assert_not_called()


def test_list_unread_not_configured():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ListUnreadGmailMessagesTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_search_not_configured():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = SearchGmailMessagesTool().run(query="from:alice@example.com")
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_get_message_not_configured():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = GetGmailMessageTool().run(message_id="m1")
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_get_message_content_not_configured():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = GetGmailMessageContentTool().run(message_id="m1")
    assert result.ok is False
    assert "not configured" in result.output.lower()
    mock_connector.execute.assert_not_called()


def test_not_configured_message_explains_oauth_is_the_only_path_not_a_password():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ListRecentGmailMessagesTool().run()
    lowered = result.output.lower()
    # The message may mention "password"/"api key" only to explicitly rule
    # them out (e.g. "no password or API key path") - it must never invite
    # the user to type or set one, and must point at OAuth instead.
    assert "oauth" in lowered
    assert "set your password" not in lowered
    assert "enter your password" not in lowered


# --- successful delegation to the connector -----------------------------------------


def test_list_recent_delegates_with_limit():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="gmail", action="list_recent_messages"
        )
        ListRecentGmailMessagesTool().run(limit=5)
    mock_connector.execute.assert_called_once_with("list_recent_messages", limit=5)


def test_list_recent_defaults_to_ten():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="gmail", action="list_recent_messages"
        )
        ListRecentGmailMessagesTool().run()
    mock_connector.execute.assert_called_once_with("list_recent_messages", limit=10)


def test_list_unread_delegates_with_limit():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="gmail", action="list_unread_messages"
        )
        ListUnreadGmailMessagesTool().run(limit=3)
    mock_connector.execute.assert_called_once_with("list_unread_messages", limit=3)


def test_search_delegates_with_query_and_limit():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="gmail", action="search_messages"
        )
        SearchGmailMessagesTool().run(query="subject:invoice", limit=7)
    mock_connector.execute.assert_called_once_with("search_messages", query="subject:invoice", limit=7)


def test_get_message_delegates_with_message_id():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="From: a\nSubject: b", service="gmail", action="get_message"
        )
        result = GetGmailMessageTool().run(message_id="m42")
    assert result.ok is True
    assert result.output == "From: a\nSubject: b"
    mock_connector.execute.assert_called_once_with("get_message", message_id="m42")


def test_get_message_content_delegates_with_message_id():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="From: a\nSubject: b\n\nFull body text.", service="gmail",
            action="get_message_full_content",
        )
        result = GetGmailMessageContentTool().run(message_id="m42")
    assert result.ok is True
    assert result.output == "From: a\nSubject: b\n\nFull body text."
    mock_connector.execute.assert_called_once_with("get_message_full_content", message_id="m42")


# --- CredentialError raised by execute() is caught, not propagated -----------------


def test_credential_error_from_execute_is_caught_gracefully():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.side_effect = CredentialError("tokens expired")
        result = ListRecentGmailMessagesTool().run()
    assert result.ok is False
    assert "tokens expired" in result.output


# --- no approval required for any of the five (all READ_ONLY) -----------------------


def test_none_of_the_five_tools_calls_confirm_side_effect():
    with patch("jarvis.tools.gmail_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="ok", service="gmail", action="x"
        )
        with patch("builtins.input") as mock_input:
            ListRecentGmailMessagesTool().run()
            ListUnreadGmailMessagesTool().run()
            SearchGmailMessagesTool().run(query="test")
            GetGmailMessageTool().run(message_id="1")
            GetGmailMessageContentTool().run(message_id="1")
    mock_input.assert_not_called()


# --- tool metadata -----------------------------------------------------------------------


def test_all_five_tools_have_distinct_names():
    names = {
        ListRecentGmailMessagesTool().name,
        ListUnreadGmailMessagesTool().name,
        SearchGmailMessagesTool().name,
        GetGmailMessageTool().name,
        GetGmailMessageContentTool().name,
    }
    assert names == {
        "list_recent_gmail_messages", "list_unread_gmail_messages",
        "search_gmail_messages", "get_gmail_message", "get_gmail_message_content",
    }


def test_search_requires_query_in_schema():
    schema = SearchGmailMessagesTool().input_schema
    assert schema["required"] == ["query"]


def test_get_message_requires_message_id_in_schema():
    schema = GetGmailMessageTool().input_schema
    assert schema["required"] == ["message_id"]


def test_get_message_content_requires_message_id_in_schema():
    schema = GetGmailMessageContentTool().input_schema
    assert schema["required"] == ["message_id"]


def test_other_two_tools_have_no_required_fields():
    for tool in (ListRecentGmailMessagesTool(), ListUnreadGmailMessagesTool()):
        assert "required" not in tool.input_schema or tool.input_schema.get("required") == []


def test_no_tool_named_after_a_write_action():
    names = {
        ListRecentGmailMessagesTool().name,
        ListUnreadGmailMessagesTool().name,
        SearchGmailMessagesTool().name,
        GetGmailMessageTool().name,
        GetGmailMessageContentTool().name,
    }
    for forbidden in (
        "send_gmail_message", "delete_gmail_message", "archive_gmail_message",
        "mark_gmail_message_read", "create_gmail_draft", "save_gmail_draft",
    ):
        assert forbidden not in names
