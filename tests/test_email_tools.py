"""Tests for jarvis.tools.email_tools: the four agent-facing Tool
wrappers around EmailConnector. Confirms each tool delegates correctly,
none requires approval (all READ_ONLY), and all degrade gracefully with a
clear message when email isn't configured - never attempting a network
call or crashing. EmailConnector itself is mocked; no real IMAP access."""

from __future__ import annotations

from unittest.mock import patch

from jarvis.integrations.base import CredentialError, ExternalActionResult
from jarvis.tools.email_tools import (
    CheckEmailConnectionTool,
    GetEmailAccountInfoTool,
    ListRecentEmailsTool,
    ReadEmailTool,
)


# --- not configured: clear message, no network attempt -----------------------------


def test_check_connection_not_configured_returns_clear_message():
    with patch("jarvis.tools.email_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = CheckEmailConnectionTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()
    mock_connector.execute.assert_not_called()


def test_get_account_info_not_configured():
    with patch("jarvis.tools.email_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = GetEmailAccountInfoTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_list_recent_emails_not_configured():
    with patch("jarvis.tools.email_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ListRecentEmailsTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_read_email_not_configured():
    with patch("jarvis.tools.email_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ReadEmailTool().run(message_id="1")
    assert result.ok is False
    assert "not configured" in result.output.lower()


# --- successful delegation to the connector -----------------------------------------


def test_check_connection_delegates_to_connector():
    with patch("jarvis.tools.email_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="Connection OK", service="email", action="check_connection"
        )
        result = CheckEmailConnectionTool().run()
    assert result.ok is True
    assert result.output == "Connection OK"
    mock_connector.execute.assert_called_once_with("check_connection")


def test_get_account_info_delegates_with_mailbox_argument():
    with patch("jarvis.tools.email_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="info", service="email", action="get_account_info"
        )
        GetEmailAccountInfoTool().run(mailbox="Sent")
    mock_connector.execute.assert_called_once_with("get_account_info", mailbox="Sent")


def test_get_account_info_defaults_to_inbox():
    with patch("jarvis.tools.email_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="info", service="email", action="get_account_info"
        )
        GetEmailAccountInfoTool().run()
    mock_connector.execute.assert_called_once_with("get_account_info", mailbox="INBOX")


def test_list_recent_emails_delegates_with_limit_and_mailbox():
    with patch("jarvis.tools.email_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="email", action="list_recent_emails"
        )
        ListRecentEmailsTool().run(mailbox="Sent", limit=5)
    mock_connector.execute.assert_called_once_with("list_recent_emails", mailbox="Sent", limit=5)


def test_read_email_delegates_with_message_id():
    with patch("jarvis.tools.email_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="body", service="email", action="read_email"
        )
        ReadEmailTool().run(message_id="42")
    mock_connector.execute.assert_called_once_with("read_email", message_id="42", mailbox="INBOX")


# --- CredentialError raised by execute() is caught, not propagated -----------------


def test_credential_error_from_execute_is_caught_gracefully():
    with patch("jarvis.tools.email_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.side_effect = CredentialError("login rejected")
        result = CheckEmailConnectionTool().run()
    assert result.ok is False
    assert "login rejected" in result.output


# --- no approval required for any of the four (all READ_ONLY) -----------------------


def test_none_of_the_four_tools_calls_confirm_side_effect():
    with patch("jarvis.tools.email_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="ok", service="email", action="x"
        )
        with patch("builtins.input") as mock_input:
            CheckEmailConnectionTool().run()
            GetEmailAccountInfoTool().run()
            ListRecentEmailsTool().run()
            ReadEmailTool().run(message_id="1")
    mock_input.assert_not_called()


# --- tool metadata -----------------------------------------------------------------------


def test_all_four_tools_have_distinct_names():
    names = {
        CheckEmailConnectionTool().name,
        GetEmailAccountInfoTool().name,
        ListRecentEmailsTool().name,
        ReadEmailTool().name,
    }
    assert names == {
        "check_email_connection", "get_email_account_info",
        "list_recent_emails", "read_email",
    }


def test_read_email_requires_message_id_in_schema():
    schema = ReadEmailTool().input_schema
    assert schema["required"] == ["message_id"]


def test_other_three_tools_have_no_required_fields():
    for tool in (CheckEmailConnectionTool(), GetEmailAccountInfoTool(), ListRecentEmailsTool()):
        assert "required" not in tool.input_schema or tool.input_schema.get("required") == []
