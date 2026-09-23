"""Tests for jarvis.integrations.connectors.gmail.GmailConnector: the
fourth real Connector implementation, REST API + OAuth-only auth,
read-only v1 (list_recent_messages, list_unread_messages,
search_messages, get_message). No real network access -
urllib.request.urlopen is mocked throughout. No real OS keychain access
either - TokenStore.load() is mocked directly, never the real keyring
backend (mirrors test_google_calendar_connector.py). Confirms: no write
action exists, is_configured() reflects ONLY stored OAuth tokens (no
password/env-var fallback, unlike EmailConnector), expired tokens are
refused rather than refreshed, every action degrades gracefully, and the
access token never appears in any output, error message, or exception
text."""

from __future__ import annotations

import json
import time
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from jarvis.integrations.base import CredentialError, RiskLevel
from jarvis.integrations.connectors.gmail import GmailConnector
from jarvis.integrations.oauth import OAuthTokens


def _tokens(**overrides) -> OAuthTokens:
    defaults = dict(
        access_token="superSecretGmailAccessTokenValue12345",
        refresh_token="superSecretGmailRefreshTokenValue12345",
        expires_at=time.time() + 3600,
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    defaults.update(overrides)
    return OAuthTokens(**defaults)


def _mock_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm


def _with_tokens(tokens: OAuthTokens | None):
    return patch("jarvis.integrations.connectors.gmail.TokenStore.load", return_value=tokens)


def _message_list_payload(ids: list[str]) -> dict:
    return {"messages": [{"id": i} for i in ids]}


def _message_detail_payload(
    message_id: str, *, from_: str = "alice@example.com", subject: str = "Hello",
    date: str = "Mon, 1 Jan 2026 10:00:00 +0000", snippet: str = "A short preview...",
) -> dict:
    return {
        "id": message_id,
        "snippet": snippet,
        "payload": {
            "headers": [
                {"name": "From", "value": from_},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date},
            ]
        },
    }


# --- is_configured: OAuth-only, no password/env-var fallback ------------------------


def test_is_configured_true_when_tokens_stored():
    with _with_tokens(_tokens()):
        assert GmailConnector().is_configured() is True


def test_is_configured_false_when_no_tokens_stored():
    with _with_tokens(None):
        assert GmailConnector().is_configured() is False


def test_is_configured_ignores_environment_variables(monkeypatch):
    # There must be NO env-var credential path at all for this connector -
    # setting arbitrary env vars must have zero effect on is_configured().
    monkeypatch.setenv("GMAIL_API_KEY", "some-value")
    monkeypatch.setenv("GMAIL_PASSWORD", "some-other-value")
    with _with_tokens(None):
        assert GmailConnector().is_configured() is False


# --- action list: only read-only actions exist -------------------------------------


def test_all_actions_are_read_only():
    with _with_tokens(_tokens()):
        connector = GmailConnector()
    for action in connector.actions:
        assert action.risk_level == RiskLevel.READ_ONLY


def test_expected_five_actions_present():
    connector = GmailConnector()
    names = {a.name for a in connector.actions}
    assert names == {
        "list_recent_messages", "list_unread_messages",
        "search_messages", "get_message", "get_message_full_content",
    }


def test_no_write_action_exists():
    connector = GmailConnector()
    names = {a.name for a in connector.actions}
    for forbidden in (
        "send_message", "delete_message", "trash_message", "archive_message",
        "modify_labels", "mark_as_read", "mark_read", "create_draft",
        "send_draft", "batch_delete", "batch_modify",
    ):
        assert forbidden not in names


def test_service_name_is_gmail():
    assert GmailConnector().service_name == "gmail"


# --- execute() without tokens returns error result, never raises, never calls network --


def test_execute_unconfigured_returns_error_result():
    with _with_tokens(None):
        result = GmailConnector().execute("list_recent_messages")
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_execute_never_calls_urlopen_when_unconfigured():
    with _with_tokens(None):
        with patch("urllib.request.urlopen") as mock_urlopen:
            GmailConnector().execute("list_recent_messages")
    mock_urlopen.assert_not_called()


# --- execute() with an unknown action ------------------------------------------------


def test_execute_unknown_action_returns_error_result():
    with _with_tokens(_tokens()):
        result = GmailConnector().execute("nonexistent_action")
    assert result.ok is False
    assert "Unknown action" in result.output


def test_execute_unknown_write_looking_action_returns_error_not_attempted():
    # Even if something asked for a plausible-sounding write action name,
    # there is no handler for it - it must be reported as unknown, never
    # silently succeed or attempt any network call.
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = GmailConnector().execute("send_message", to="x@example.com", body="hi")
    assert result.ok is False
    assert "Unknown action" in result.output
    mock_urlopen.assert_not_called()


# --- expired tokens: refuse rather than attempt a refresh -----------------------------


def test_expired_tokens_raise_credential_error_not_attempt_refresh():
    expired = _tokens(expires_at=time.time() - 3600)
    with _with_tokens(expired):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = GmailConnector().execute("list_recent_messages")
    assert result.ok is False
    assert "expired" in result.output.lower()
    mock_urlopen.assert_not_called()


def test_expired_tokens_never_call_refresh_access_token():
    expired = _tokens(expires_at=time.time() - 3600)
    with _with_tokens(expired):
        with patch("jarvis.integrations.oauth.refresh_access_token") as mock_refresh:
            GmailConnector().execute("list_recent_messages")
    mock_refresh.assert_not_called()


# --- list_recent_messages --------------------------------------------------------------


def test_list_recent_messages_success():
    list_payload = _message_list_payload(["m1", "m2"])
    detail_1 = _message_detail_payload("m1", subject="First")
    detail_2 = _message_detail_payload("m2", subject="Second")

    with _with_tokens(_tokens()):
        with patch(
            "urllib.request.urlopen",
            side_effect=[_mock_response(list_payload), _mock_response(detail_1), _mock_response(detail_2)],
        ):
            result = GmailConnector().execute("list_recent_messages")

    assert result.ok is True
    assert "m1" in result.output
    assert "First" in result.output
    assert "m2" in result.output
    assert "Second" in result.output
    assert "alice@example.com" in result.output


def test_list_recent_messages_empty():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})):
            result = GmailConnector().execute("list_recent_messages")
    assert result.ok is True
    assert "no messages found" in result.output.lower()


def test_list_recent_messages_uses_correct_endpoint():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})) as mock_urlopen:
            GmailConnector().execute("list_recent_messages")
    request = mock_urlopen.call_args[0][0]
    assert "/messages" in request.full_url
    assert "gmail.googleapis.com" in request.full_url


def test_list_recent_messages_respects_limit_parameter():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})) as mock_urlopen:
            GmailConnector().execute("list_recent_messages", limit=5)
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=5" in request.full_url


def test_list_recent_messages_default_limit_used_when_omitted():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})) as mock_urlopen:
            GmailConnector().execute("list_recent_messages")
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=10" in request.full_url


def test_list_recent_messages_limit_capped_at_max():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})) as mock_urlopen:
            GmailConnector().execute("list_recent_messages", limit=1000)
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=50" in request.full_url


def test_list_recent_messages_limit_floor_at_one():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})) as mock_urlopen:
            GmailConnector().execute("list_recent_messages", limit=0)
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=1" in request.full_url


def test_list_recent_messages_does_not_filter_by_unread():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})) as mock_urlopen:
            GmailConnector().execute("list_recent_messages")
    request = mock_urlopen.call_args[0][0]
    assert "is%3Aunread" not in request.full_url  # 'is:unread' percent-encoded
    assert "q=" not in request.full_url


# --- list_unread_messages --------------------------------------------------------------


def test_list_unread_messages_success():
    list_payload = _message_list_payload(["m1"])
    detail = _message_detail_payload("m1", subject="Unread thing")

    with _with_tokens(_tokens()):
        with patch(
            "urllib.request.urlopen",
            side_effect=[_mock_response(list_payload), _mock_response(detail)],
        ):
            result = GmailConnector().execute("list_unread_messages")

    assert result.ok is True
    assert "Unread thing" in result.output


def test_list_unread_messages_empty():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})):
            result = GmailConnector().execute("list_unread_messages")
    assert result.ok is True
    assert "no messages found" in result.output.lower()


def test_list_unread_messages_uses_is_unread_query():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})) as mock_urlopen:
            GmailConnector().execute("list_unread_messages")
    request = mock_urlopen.call_args[0][0]
    assert "q=is%3Aunread" in request.full_url


def test_list_unread_messages_respects_limit():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})) as mock_urlopen:
            GmailConnector().execute("list_unread_messages", limit=3)
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=3" in request.full_url


# --- search_messages: by sender, by subject/topic --------------------------------------


def test_search_messages_by_sender():
    list_payload = _message_list_payload(["m1"])
    detail = _message_detail_payload("m1", from_="bob@example.com")

    with _with_tokens(_tokens()):
        with patch(
            "urllib.request.urlopen",
            side_effect=[_mock_response(list_payload), _mock_response(detail)],
        ):
            result = GmailConnector().execute("search_messages", query="from:bob@example.com")

    assert result.ok is True
    assert "bob@example.com" in result.output


def test_search_messages_sends_query_to_api():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})) as mock_urlopen:
            GmailConnector().execute("search_messages", query="from:bob@example.com")
    request = mock_urlopen.call_args[0][0]
    assert "from%3Abob%40example.com" in request.full_url


def test_search_messages_by_subject_topic():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})) as mock_urlopen:
            GmailConnector().execute("search_messages", query="subject:invoice")
    request = mock_urlopen.call_args[0][0]
    assert "subject%3Ainvoice" in request.full_url


def test_search_messages_empty_query_returns_error_without_network_call():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = GmailConnector().execute("search_messages", query="")
    assert result.ok is False
    mock_urlopen.assert_not_called()


def test_search_messages_whitespace_query_returns_error():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = GmailConnector().execute("search_messages", query="   ")
    assert result.ok is False
    mock_urlopen.assert_not_called()


def test_search_messages_no_results():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})):
            result = GmailConnector().execute("search_messages", query="from:nobody@nowhere.com")
    assert result.ok is True
    assert "no messages found" in result.output.lower()


def test_search_messages_respects_limit():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})) as mock_urlopen:
            GmailConnector().execute("search_messages", query="test", limit=2)
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=2" in request.full_url


# --- get_message ------------------------------------------------------------------------


def test_get_message_success():
    detail = _message_detail_payload("m1", from_="carol@example.com", subject="Meeting notes", snippet="Here are the notes")
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(detail)):
            result = GmailConnector().execute("get_message", message_id="m1")

    assert result.ok is True
    assert "carol@example.com" in result.output
    assert "Meeting notes" in result.output
    assert "Here are the notes" in result.output


def test_get_message_uses_correct_endpoint():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
            GmailConnector().execute("get_message", message_id="m42")
    request = mock_urlopen.call_args[0][0]
    assert "/messages/m42" in request.full_url


def test_get_message_uses_metadata_format_not_full_body():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
            GmailConnector().execute("get_message", message_id="m1")
    request = mock_urlopen.call_args[0][0]
    assert "format=metadata" in request.full_url


def test_get_message_empty_message_id_returns_error_without_network_call():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = GmailConnector().execute("get_message", message_id="")
    assert result.ok is False
    mock_urlopen.assert_not_called()


def test_get_message_missing_message_id_raises():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({})):
            with pytest.raises(TypeError):
                GmailConnector().execute("get_message")  # message_id is required


def test_get_message_minimal_fields_uses_placeholders():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"id": "m1"})):
            result = GmailConnector().execute("get_message", message_id="m1")
    assert result.ok is True
    assert "(no subject)" in result.output


# --- get_message_full_content ------------------------------------------------------------


def _b64url(text: str) -> str:
    import base64

    return base64.urlsafe_b64encode(text.encode("utf-8")).decode().rstrip("=")


def _full_message_payload(
    message_id: str, *, from_: str = "alice@example.com", subject: str = "Hello",
    date: str = "Mon, 1 Jan 2026 10:00:00 +0000", body_text: str = "Full message body here.",
    mime_type: str = "text/plain", snippet: str = "short preview",
) -> dict:
    return {
        "id": message_id,
        "snippet": snippet,
        "payload": {
            "headers": [
                {"name": "From", "value": from_},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date},
            ],
            "mimeType": mime_type,
            "body": {"data": _b64url(body_text)},
        },
    }


def test_get_message_full_content_success_plain_text():
    payload = _full_message_payload("m1", body_text="Sveiki, čia pilnas laiško tekstas.")
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GmailConnector().execute("get_message_full_content", message_id="m1")

    assert result.ok is True
    assert "alice@example.com" in result.output
    assert "Hello" in result.output
    assert "Sveiki, čia pilnas laiško tekstas." in result.output


def test_get_message_full_content_uses_correct_endpoint():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
            GmailConnector().execute("get_message_full_content", message_id="m42")
    request = mock_urlopen.call_args[0][0]
    assert "/messages/m42" in request.full_url


def test_get_message_full_content_uses_full_format_not_metadata():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
            GmailConnector().execute("get_message_full_content", message_id="m1")
    request = mock_urlopen.call_args[0][0]
    assert "format=full" in request.full_url


def test_get_message_full_content_empty_message_id_returns_error_without_network_call():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = GmailConnector().execute("get_message_full_content", message_id="")
    assert result.ok is False
    mock_urlopen.assert_not_called()


def test_get_message_full_content_missing_message_id_raises():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({})):
            with pytest.raises(TypeError):
                GmailConnector().execute("get_message_full_content")  # message_id is required


def test_get_message_full_content_decodes_multipart_nested_text_plain():
    # A realistic multipart/alternative message nested inside
    # multipart/mixed - the plain-text part is two levels deep.
    payload = {
        "id": "m1",
        "snippet": "preview",
        "payload": {
            "headers": [
                {"name": "From", "value": "bob@example.com"},
                {"name": "Subject", "value": "Nested"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 10:00:00 +0000"},
            ],
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64url("Nested plain text.")}},
                        {"mimeType": "text/html", "body": {"data": _b64url("<p>Nested HTML.</p>")}},
                    ],
                },
            ],
        },
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GmailConnector().execute("get_message_full_content", message_id="m1")
    assert result.ok is True
    assert "Nested plain text." in result.output


def test_get_message_full_content_falls_back_to_html_when_no_plain_text():
    payload = _full_message_payload(
        "m1", body_text="<p>HTML only body.</p>", mime_type="text/html"
    )
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GmailConnector().execute("get_message_full_content", message_id="m1")
    assert result.ok is True
    assert "HTML only body." in result.output


def test_get_message_full_content_falls_back_to_snippet_when_no_body_at_all():
    payload = {
        "id": "m1",
        "snippet": "Only a snippet is available.",
        "payload": {
            "headers": [{"name": "Subject", "value": "No body"}],
            "mimeType": "application/octet-stream",
        },
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GmailConnector().execute("get_message_full_content", message_id="m1")
    assert result.ok is True
    assert "Only a snippet is available." in result.output


def test_get_message_full_content_malformed_base64_does_not_crash():
    # A body.data value with padding that cannot be corrected to a valid
    # base64 length (odd number of characters after the 4-char rounding
    # this connector applies) - decoding must never raise or crash the
    # whole action, whatever garbage bytes/replacement characters result.
    payload = {
        "id": "m1",
        "snippet": "fallback snippet",
        "payload": {
            "headers": [{"name": "Subject", "value": "Bad data"}],
            "mimeType": "text/plain",
            "body": {"data": "%%%"},
        },
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GmailConnector().execute("get_message_full_content", message_id="m1")
    assert result.ok is True  # never raises/fails outright


def test_get_message_full_content_never_leaks_access_token():
    payload = _full_message_payload("m1", body_text="Some body text.")
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GmailConnector().execute("get_message_full_content", message_id="m1")
    assert "superSecretGmailAccessTokenValue12345" not in result.output


def test_get_message_full_content_is_read_only():
    connector = GmailConnector()
    action = connector.get_action("get_message_full_content")
    assert action is not None
    assert action.risk_level == RiskLevel.READ_ONLY


# --- authentication: Bearer token, never appears in plaintext-visible form -----------


def test_authorization_header_uses_bearer_scheme():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})) as mock_urlopen:
            GmailConnector().execute("list_recent_messages")
    request = mock_urlopen.call_args[0][0]
    auth_header = request.get_header("Authorization")
    assert auth_header == "Bearer superSecretGmailAccessTokenValue12345"


def test_access_token_never_appears_in_successful_result_output():
    detail = _message_detail_payload("m1")
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=[_mock_response(_message_list_payload(["m1"])), _mock_response(detail)]):
            result = GmailConnector().execute("list_recent_messages")
    assert "superSecretGmailAccessTokenValue12345" not in result.output


def test_refresh_token_never_appears_in_any_result():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"messages": []})):
            result = GmailConnector().execute("list_recent_messages")
    assert "superSecretGmailRefreshTokenValue12345" not in result.output


# --- API error handling: never raises, never leaks the token ---------------------------


def test_http_error_returns_error_result_not_exception():
    http_error = urllib.error.HTTPError(
        url="https://gmail.googleapis.com/gmail/v1/users/me/messages",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=MagicMock(read=lambda: b'{"error": {"message": "Invalid Credentials"}}'),
    )
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=http_error):
            result = GmailConnector().execute("list_recent_messages")
    assert result.ok is False
    assert "401" in result.output


def test_http_error_never_leaks_access_token():
    http_error = urllib.error.HTTPError(
        url="https://gmail.googleapis.com/gmail/v1/users/me/messages",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=MagicMock(read=lambda: b'{"error": "superSecretGmailAccessTokenValue12345 rejected"}'),
    )
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=http_error):
            result = GmailConnector().execute("list_recent_messages")
    assert "superSecretGmailAccessTokenValue12345" not in result.output


def test_connection_error_returns_error_result():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            result = GmailConnector().execute("list_recent_messages")
    assert result.ok is False


def test_timeout_returns_error_result():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            result = GmailConnector().execute("list_recent_messages")
    assert result.ok is False
    assert "timed out" in result.output.lower()


def test_invalid_json_response_returns_error_result():
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"not valid json{{{"
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=cm):
            result = GmailConnector().execute("list_recent_messages")
    assert result.ok is False


def test_invalid_json_response_never_leaks_token():
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"not valid json superSecretGmailAccessTokenValue12345"
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=cm):
            result = GmailConnector().execute("list_recent_messages")
    assert "superSecretGmailAccessTokenValue12345" not in result.output


# --- disconnect() clears stored OAuth tokens, makes no network call --------------------


def test_disconnect_calls_token_store_clear():
    with patch("jarvis.integrations.connectors.gmail.TokenStore.clear", return_value=True) as mock_clear:
        result = GmailConnector().disconnect()
    mock_clear.assert_called_once()
    assert result is True


def test_disconnect_returns_false_when_nothing_stored():
    with patch("jarvis.integrations.connectors.gmail.TokenStore.clear", return_value=False):
        result = GmailConnector().disconnect()
    assert result is False


def test_disconnect_makes_no_network_call():
    with patch("jarvis.integrations.connectors.gmail.TokenStore.clear", return_value=True):
        with patch("urllib.request.urlopen") as mock_urlopen:
            GmailConnector().disconnect()
    mock_urlopen.assert_not_called()
