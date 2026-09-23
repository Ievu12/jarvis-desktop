"""Tests for jarvis.integrations.connectors.email.EmailConnector: the
first real Connector implementation, IMAP-based, read-only v1 (check
connection, account info, list headers, read one message). No real
network access - imaplib.IMAP4_SSL is mocked throughout. No real OS
keychain access either - TokenStore.load() is forced to return None for
every test in this file via the autouse no_stored_oauth_tokens fixture,
so these password-path tests are never affected by whatever OAuth tokens
(if any) happen to be stored on the machine running them; OAuth-path
behavior has its own dedicated test file
(test_email_connector_oauth.py). Confirms: no write action exists,
is_configured() reflects env vars accurately, credentials are never read
from anywhere but the environment, and every action degrades gracefully
(CredentialError when unconfigured, ExternalActionResult(ok=False, ...)
for IMAP/network failures)."""

from __future__ import annotations

import imaplib
from unittest.mock import MagicMock, patch

import pytest

from jarvis.integrations.base import CredentialError, RiskLevel
from jarvis.integrations.connectors.email import EmailConnector


@pytest.fixture(autouse=True)
def no_stored_oauth_tokens():
    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=None):
        yield


@pytest.fixture
def configured_env(monkeypatch):
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("EMAIL_ADDRESS", "user@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "app-specific-password")


@pytest.fixture
def unconfigured_env(monkeypatch):
    monkeypatch.delenv("EMAIL_IMAP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)


# --- is_configured ----------------------------------------------------------------


def test_is_configured_true_when_all_vars_set(configured_env):
    assert EmailConnector().is_configured() is True


def test_is_configured_false_when_unset(unconfigured_env):
    assert EmailConnector().is_configured() is False


def test_is_configured_false_when_partially_set(monkeypatch):
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    assert EmailConnector().is_configured() is False


# --- action list: only read-only actions exist -------------------------------------


def test_all_actions_are_read_only(configured_env):
    connector = EmailConnector()
    for action in connector.actions:
        assert action.risk_level == RiskLevel.READ_ONLY


def test_expected_four_actions_present(configured_env):
    connector = EmailConnector()
    names = {a.name for a in connector.actions}
    assert names == {"check_connection", "get_account_info", "list_recent_emails", "read_email"}


def test_no_write_action_exists(configured_env):
    connector = EmailConnector()
    names = {a.name for a in connector.actions}
    for forbidden in ("send_email", "delete_email", "move_email", "mark_as_read"):
        assert forbidden not in names


def test_service_name_is_email(configured_env):
    assert EmailConnector().service_name == "email"


# --- execute() without credentials raises CredentialError --------------------------


def test_execute_unconfigured_raises_credential_error(unconfigured_env):
    connector = EmailConnector()
    with pytest.raises(CredentialError):
        connector.execute("check_connection")


def test_execute_never_calls_imaplib_when_unconfigured(unconfigured_env):
    connector = EmailConnector()
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        with pytest.raises(CredentialError):
            connector.execute("check_connection")
    mock_imap.assert_not_called()


# --- execute() with an unknown action ------------------------------------------------


def test_execute_unknown_action_returns_error_result(configured_env):
    connector = EmailConnector()
    result = connector.execute("nonexistent_action")
    assert result.ok is False
    assert "Unknown action" in result.output


# --- check_connection ------------------------------------------------------------------


def test_check_connection_success(configured_env):
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b"(\\HasNoChildren) \"/\" INBOX"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        result = EmailConnector().execute("check_connection")

    assert result.ok is True
    assert "Connection OK" in result.output
    mock_conn.login.assert_called_once_with("user@example.com", "app-specific-password")
    mock_conn.logout.assert_called_once()


def test_check_connection_login_failure_raises_credential_error(configured_env):
    mock_conn = MagicMock()
    mock_conn.login.side_effect = imaplib.IMAP4.error("authentication failed")

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        with pytest.raises(CredentialError):
            EmailConnector().execute("check_connection")
    mock_conn.logout.assert_called_once()


def test_check_connection_network_error_returns_error_result_not_exception(configured_env):
    with patch("imaplib.IMAP4_SSL", side_effect=OSError("connection refused")):
        result = EmailConnector().execute("check_connection")
    assert result.ok is False
    assert "connection refused" in result.output.lower() or "error" in result.output.lower()


# --- get_account_info --------------------------------------------------------------------


def test_get_account_info_returns_address_and_count(configured_env):
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
    mock_conn.select.return_value = ("OK", [b"42"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        result = EmailConnector().execute("get_account_info")

    assert result.ok is True
    assert "user@example.com" in result.output
    assert "42" in result.output
    mock_conn.select.assert_called_once_with("INBOX", readonly=True)


def test_get_account_info_custom_mailbox(configured_env):
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [])
    mock_conn.select.return_value = ("OK", [b"5"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        EmailConnector().execute("get_account_info", mailbox="Sent")

    mock_conn.select.assert_called_once_with("Sent", readonly=True)


def test_get_account_info_always_uses_readonly_select(configured_env):
    # Confirms the connector never opens a mailbox in a way that could
    # mark messages as read or otherwise mutate them - readonly=True is
    # non-negotiable for a read-only connector.
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [])
    mock_conn.select.return_value = ("OK", [b"0"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        EmailConnector().execute("get_account_info")

    _, kwargs = mock_conn.select.call_args
    assert kwargs.get("readonly") is True


# --- list_recent_emails --------------------------------------------------------------------


def test_list_recent_emails_empty_mailbox(configured_env):
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.search.return_value = ("OK", [b""])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        result = EmailConnector().execute("list_recent_emails")

    assert result.ok is True
    assert "No messages" in result.output


def test_list_recent_emails_returns_headers_not_body(configured_env):
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.search.return_value = ("OK", [b"1"])
    header_bytes = b"From: sender@example.com\r\nSubject: Test Subject\r\nDate: Mon, 1 Jan 2026 00:00:00 +0000\r\n\r\n"
    mock_conn.fetch.return_value = ("OK", [(b"1 (BODY[HEADER])", header_bytes)])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        result = EmailConnector().execute("list_recent_emails")

    assert result.ok is True
    assert "sender@example.com" in result.output
    assert "Test Subject" in result.output
    # fetch must request headers only (BODY.PEEK[HEADER...]), never the
    # full body - this is the mechanism that keeps listing separate from
    # reading a message's content.
    fetch_args = mock_conn.fetch.call_args[0]
    assert "HEADER" in fetch_args[1]


def test_list_recent_emails_uses_readonly_select(configured_env):
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.search.return_value = ("OK", [b""])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        EmailConnector().execute("list_recent_emails")

    _, kwargs = mock_conn.select.call_args
    assert kwargs.get("readonly") is True


def test_list_recent_emails_respects_limit(configured_env):
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"5"])
    mock_conn.search.return_value = ("OK", [b"1 2 3 4 5"])
    header_bytes = b"From: a@example.com\r\nSubject: S\r\nDate: D\r\n\r\n"
    mock_conn.fetch.return_value = ("OK", [(b"x", header_bytes)])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        EmailConnector().execute("list_recent_emails", limit=2)

    assert mock_conn.fetch.call_count == 2


def test_list_recent_emails_limit_capped_at_max(configured_env):
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"100"])
    ids = " ".join(str(i) for i in range(1, 101)).encode()
    mock_conn.search.return_value = ("OK", [ids])
    header_bytes = b"From: a@example.com\r\nSubject: S\r\nDate: D\r\n\r\n"
    mock_conn.fetch.return_value = ("OK", [(b"x", header_bytes)])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        EmailConnector().execute("list_recent_emails", limit=1000)

    assert mock_conn.fetch.call_count == 50  # _MAX_LIST_LIMIT


def test_list_recent_emails_mailbox_open_failure(configured_env):
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("NO", [b"mailbox does not exist"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        result = EmailConnector().execute("list_recent_emails", mailbox="Nonexistent")

    assert result.ok is False


# --- read_email --------------------------------------------------------------------------


def test_read_email_returns_headers_and_body(configured_env):
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"1"])
    raw_message = (
        b"From: sender@example.com\r\n"
        b"Subject: Hello\r\n"
        b"Date: Mon, 1 Jan 2026 00:00:00 +0000\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"This is the body text."
    )
    mock_conn.fetch.return_value = ("OK", [(b"1 (BODY[])", raw_message)])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        result = EmailConnector().execute("read_email", message_id="1")

    assert result.ok is True
    assert "sender@example.com" in result.output
    assert "Hello" in result.output
    assert "This is the body text." in result.output


def test_read_email_uses_readonly_select(configured_env):
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.fetch.return_value = ("OK", [(b"1", b"From: a@example.com\r\n\r\nbody")])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        EmailConnector().execute("read_email", message_id="1")

    _, kwargs = mock_conn.select.call_args
    assert kwargs.get("readonly") is True


def test_read_email_uses_peek_never_marks_as_read(configured_env):
    # BODY.PEEK[] (not BODY[]) is required to avoid the IMAP server
    # marking the message as \Seen as a side effect of reading it - a
    # read-only connector must not have this incidental write effect.
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.fetch.return_value = ("OK", [(b"1", b"From: a@example.com\r\n\r\nbody")])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        EmailConnector().execute("read_email", message_id="1")

    fetch_args = mock_conn.fetch.call_args[0]
    assert "PEEK" in fetch_args[1]


def test_read_email_not_found(configured_env):
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.fetch.return_value = ("NO", [])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        result = EmailConnector().execute("read_email", message_id="999")

    assert result.ok is False
    assert "not found" in result.output.lower()


def test_read_email_missing_message_id_argument_raises(configured_env):
    mock_conn = MagicMock()
    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        with pytest.raises(TypeError):
            EmailConnector().execute("read_email")  # message_id is required


# --- always logs out, even on error paths --------------------------------------------------


def test_logout_called_even_when_select_fails(configured_env):
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("NO", [b"error"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        EmailConnector().execute("list_recent_emails")

    mock_conn.logout.assert_called_once()


# --- credentials never leak into results ---------------------------------------------------


def test_password_never_appears_in_any_result_output(configured_env):
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b"(\\HasNoChildren) \"/\" INBOX"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        result = EmailConnector().execute("check_connection")

    assert "app-specific-password" not in result.output
