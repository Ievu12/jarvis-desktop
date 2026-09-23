"""Tests for EmailConnector's OAuth path: preferring stored OAuth tokens
over a password when both are present, building the XOAUTH2 SASL string
correctly, refusing to proceed with expired tokens (since automatic
refresh is not implemented yet - see jarvis.integrations.oauth), and
leaving the original password path completely unaffected when no tokens
are stored. No real network access (imaplib.IMAP4_SSL is mocked) and no
real OS keychain access (TokenStore.load()/is_configured() are mocked
directly) - this file never touches the actual keyring backend, unlike
test_oauth.py which deliberately does against disposable service names."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from jarvis.integrations.base import CredentialError
from jarvis.integrations.connectors.email import EmailConnector
from jarvis.integrations.oauth import OAuthTokens


def _tokens(**overrides) -> OAuthTokens:
    defaults = dict(
        access_token="access-abc",
        refresh_token="refresh-xyz",
        expires_at=time.time() + 3600,
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    defaults.update(overrides)
    return OAuthTokens(**defaults)


@pytest.fixture
def host_and_address_env(monkeypatch):
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("EMAIL_ADDRESS", "user@example.com")
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv("EMAIL_IMAP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)


# --- is_configured: OAuth alone is sufficient ----------------------------------------


def test_is_configured_true_with_oauth_tokens_and_no_password(host_and_address_env):
    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=_tokens()):
        assert EmailConnector().is_configured() is True


def test_is_configured_false_when_neither_password_nor_oauth(no_env):
    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=None):
        assert EmailConnector().is_configured() is False


# --- _connect() prefers OAuth when both are present -----------------------------------


def test_oauth_used_when_tokens_present_even_with_password_also_set(monkeypatch):
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("EMAIL_ADDRESS", "user@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "some-password")

    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b"INBOX"])

    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=_tokens()):
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            EmailConnector().execute("check_connection")

    mock_conn.authenticate.assert_called_once()
    mock_conn.login.assert_not_called()


def test_password_path_used_when_no_tokens_stored(host_and_address_env, monkeypatch):
    monkeypatch.setenv("EMAIL_PASSWORD", "some-password")
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b"INBOX"])

    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=None):
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            EmailConnector().execute("check_connection")

    mock_conn.login.assert_called_once_with("user@example.com", "some-password")
    mock_conn.authenticate.assert_not_called()


# --- XOAUTH2 string construction -----------------------------------------------------


def test_authenticate_called_with_xoauth2_mechanism(host_and_address_env):
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [])

    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=_tokens()):
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            EmailConnector().execute("check_connection")

    args = mock_conn.authenticate.call_args[0]
    assert args[0] == "XOAUTH2"


def test_xoauth2_callback_produces_correctly_formatted_string(host_and_address_env):
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [])

    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=_tokens(access_token="my-access-token")):
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            EmailConnector().execute("check_connection")

    callback = mock_conn.authenticate.call_args[0][1]
    result = callback(b"")  # imaplib passes a server challenge; XOAUTH2's initial response ignores it
    assert result == b"user=user@example.com\x01auth=Bearer my-access-token\x01\x01"


def test_access_token_never_appears_in_result_output(host_and_address_env):
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b"INBOX"])

    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=_tokens(access_token="super-secret-token")):
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            result = EmailConnector().execute("check_connection")

    assert "super-secret-token" not in result.output


# --- missing host/address alongside stored tokens -------------------------------------


def test_oauth_tokens_present_but_host_missing_raises_credential_error(no_env):
    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=_tokens()):
        with pytest.raises(CredentialError):
            EmailConnector().execute("check_connection")


# --- expired tokens: refuse rather than attempt a real refresh -----------------------


def test_expired_tokens_raise_credential_error_not_attempt_refresh(host_and_address_env):
    expired = _tokens(expires_at=time.time() - 3600)
    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=expired):
        with patch("imaplib.IMAP4_SSL") as mock_imap_cls:
            with pytest.raises(CredentialError, match="expired"):
                EmailConnector().execute("check_connection")
    mock_imap_cls.assert_not_called()  # never even attempts to connect with an expired token


def test_expired_tokens_never_call_refresh_access_token(host_and_address_env):
    expired = _tokens(expires_at=time.time() - 3600)
    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=expired):
        with patch("jarvis.integrations.oauth.refresh_access_token") as mock_refresh:
            with pytest.raises(CredentialError):
                EmailConnector().execute("check_connection")
    mock_refresh.assert_not_called()


# --- IMAP-level auth failure on the OAuth path is reported as CredentialError -----------


def test_oauth_authenticate_failure_raises_credential_error(host_and_address_env):
    import imaplib

    mock_conn = MagicMock()
    mock_conn.authenticate.side_effect = imaplib.IMAP4.error("XOAUTH2 rejected")

    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=_tokens()):
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            with pytest.raises(CredentialError):
                EmailConnector().execute("check_connection")
    mock_conn.logout.assert_called_once()


# --- all four actions still work identically via the OAuth path -----------------------


def test_list_recent_emails_works_via_oauth_path(host_and_address_env):
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.search.return_value = ("OK", [b""])

    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=_tokens()):
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            result = EmailConnector().execute("list_recent_emails")

    assert result.ok is True
    mock_conn.authenticate.assert_called_once()


# --- disconnect() clears stored OAuth tokens ------------------------------------------


def test_disconnect_calls_token_store_clear():
    with patch("jarvis.integrations.connectors.email.TokenStore.clear", return_value=True) as mock_clear:
        result = EmailConnector().disconnect()
    mock_clear.assert_called_once()
    assert result is True


def test_disconnect_returns_false_when_nothing_stored():
    with patch("jarvis.integrations.connectors.email.TokenStore.clear", return_value=False):
        result = EmailConnector().disconnect()
    assert result is False


def test_disconnect_never_touches_env_var_credentials(host_and_address_env, monkeypatch):
    monkeypatch.setenv("EMAIL_PASSWORD", "some-password")
    with patch("jarvis.integrations.connectors.email.TokenStore.clear", return_value=True):
        EmailConnector().disconnect()
    import os

    assert os.environ.get("EMAIL_PASSWORD") == "some-password"
    assert os.environ.get("EMAIL_IMAP_HOST") == "imap.example.com"


def test_disconnect_makes_no_network_call():
    with patch("jarvis.integrations.connectors.email.TokenStore.clear", return_value=True):
        with patch("imaplib.IMAP4_SSL") as mock_imap_cls:
            EmailConnector().disconnect()
    mock_imap_cls.assert_not_called()
