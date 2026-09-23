"""Tests for jarvis.integrations.connectors.google_calendar
.GoogleCalendarConnector: the third real Connector implementation,
REST API + OAuth-only auth, read-only v1 (list_upcoming_events,
get_event). No real network access - urllib.request.urlopen is mocked
throughout. No real OS keychain access either - TokenStore.load() is
mocked directly, never the real keyring backend (unlike test_oauth.py,
which deliberately exercises the real keyring against disposable service
names). Confirms: no write action exists, is_configured() reflects ONLY
stored OAuth tokens (no password/env-var fallback, unlike EmailConnector),
expired tokens are refused rather than refreshed, every action degrades
gracefully, and the access token never appears in any output, error
message, or exception text."""

from __future__ import annotations

import json
import time
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from jarvis.integrations.base import CredentialError, RiskLevel
from jarvis.integrations.connectors.google_calendar import GoogleCalendarConnector
from jarvis.integrations.oauth import OAuthTokens


def _tokens(**overrides) -> OAuthTokens:
    defaults = dict(
        access_token="superSecretAccessTokenValue12345",
        refresh_token="superSecretRefreshTokenValue12345",
        expires_at=time.time() + 3600,
        scope="https://www.googleapis.com/auth/calendar.readonly",
    )
    defaults.update(overrides)
    return OAuthTokens(**defaults)


def _mock_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm


def _with_tokens(tokens: OAuthTokens | None):
    return patch(
        "jarvis.integrations.connectors.google_calendar.TokenStore.load", return_value=tokens
    )


# --- is_configured: OAuth-only, no password/env-var fallback ------------------------


def test_is_configured_true_when_tokens_stored():
    with _with_tokens(_tokens()):
        assert GoogleCalendarConnector().is_configured() is True


def test_is_configured_false_when_no_tokens_stored():
    with _with_tokens(None):
        assert GoogleCalendarConnector().is_configured() is False


def test_is_configured_ignores_environment_variables(monkeypatch):
    # Unlike EmailConnector, there must be NO env-var credential path at
    # all for this connector - setting arbitrary env vars must have zero
    # effect on is_configured().
    monkeypatch.setenv("GOOGLE_CALENDAR_API_KEY", "some-value")
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_SECRET", "some-other-value")
    with _with_tokens(None):
        assert GoogleCalendarConnector().is_configured() is False


# --- action list: only read-only actions exist -------------------------------------


def test_all_actions_are_read_only():
    with _with_tokens(_tokens()):
        connector = GoogleCalendarConnector()
    for action in connector.actions:
        assert action.risk_level == RiskLevel.READ_ONLY


def test_expected_three_actions_present():
    connector = GoogleCalendarConnector()
    names = {a.name for a in connector.actions}
    assert names == {"list_todays_events", "list_upcoming_events", "get_event"}


def test_no_write_action_exists():
    connector = GoogleCalendarConnector()
    names = {a.name for a in connector.actions}
    for forbidden in (
        "create_event", "update_event", "delete_event", "respond_to_invite",
        "move_event", "insert_event", "patch_event",
    ):
        assert forbidden not in names


def test_service_name_is_google_calendar():
    assert GoogleCalendarConnector().service_name == "google_calendar"


# --- execute() without tokens returns error result, never raises, never calls network --


def test_execute_unconfigured_returns_error_result():
    with _with_tokens(None):
        result = GoogleCalendarConnector().execute("list_upcoming_events")
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_execute_never_calls_urlopen_when_unconfigured():
    with _with_tokens(None):
        with patch("urllib.request.urlopen") as mock_urlopen:
            GoogleCalendarConnector().execute("list_upcoming_events")
    mock_urlopen.assert_not_called()


# --- execute() with an unknown action ------------------------------------------------


def test_execute_unknown_action_returns_error_result():
    with _with_tokens(_tokens()):
        result = GoogleCalendarConnector().execute("nonexistent_action")
    assert result.ok is False
    assert "Unknown action" in result.output


# --- expired tokens: refuse rather than attempt a refresh -----------------------------


def test_expired_tokens_raise_credential_error_not_attempt_refresh():
    expired = _tokens(expires_at=time.time() - 3600)
    with _with_tokens(expired):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = GoogleCalendarConnector().execute("list_upcoming_events")
    assert result.ok is False
    assert "expired" in result.output.lower()
    mock_urlopen.assert_not_called()


def test_expired_tokens_never_call_refresh_access_token():
    expired = _tokens(expires_at=time.time() - 3600)
    with _with_tokens(expired):
        with patch("jarvis.integrations.oauth.refresh_access_token") as mock_refresh:
            GoogleCalendarConnector().execute("list_upcoming_events")
    mock_refresh.assert_not_called()


# --- list_todays_events --------------------------------------------------------------


def test_list_todays_events_success():
    payload = {
        "items": [
            {"id": "evt_1", "summary": "Standup", "start": {"dateTime": "2026-01-01T09:00:00Z"}},
            {"id": "evt_2", "summary": "Lunch", "start": {"dateTime": "2026-01-01T12:00:00Z"}},
        ]
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GoogleCalendarConnector().execute("list_todays_events")

    assert result.ok is True
    assert "evt_1" in result.output
    assert "Standup" in result.output
    assert "evt_2" in result.output
    assert "Lunch" in result.output


def test_list_todays_events_empty():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})):
            result = GoogleCalendarConnector().execute("list_todays_events")
    assert result.ok is True
    assert "no events found for today" in result.output.lower()


def test_list_todays_events_uses_correct_endpoint():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_todays_events")
    request = mock_urlopen.call_args[0][0]
    assert "/calendars/primary/events" in request.full_url


def test_list_todays_events_sends_time_min_and_time_max():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_todays_events")
    request = mock_urlopen.call_args[0][0]
    assert "timeMin=" in request.full_url
    assert "timeMax=" in request.full_url


def test_list_todays_events_time_range_is_exactly_one_day():
    import urllib.parse
    from datetime import datetime

    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_todays_events")
    request = mock_urlopen.call_args[0][0]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
    time_min = datetime.fromisoformat(query["timeMin"][0])
    time_max = datetime.fromisoformat(query["timeMax"][0])
    assert (time_max - time_min).total_seconds() == 24 * 3600


def test_list_todays_events_time_min_is_midnight_local():
    import urllib.parse
    from datetime import datetime

    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_todays_events")
    request = mock_urlopen.call_args[0][0]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
    time_min = datetime.fromisoformat(query["timeMin"][0])
    assert (time_min.hour, time_min.minute, time_min.second) == (0, 0, 0)


def test_list_todays_events_orders_by_start_time():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_todays_events")
    request = mock_urlopen.call_args[0][0]
    assert "orderBy=startTime" in request.full_url


def test_list_todays_events_respects_limit_parameter():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_todays_events", limit=3)
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=3" in request.full_url


def test_list_todays_events_default_limit_used_when_omitted():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_todays_events")
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=10" in request.full_url


def test_list_todays_events_limit_capped_at_max():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_todays_events", limit=1000)
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=50" in request.full_url


def test_list_todays_events_limit_floor_at_one():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_todays_events", limit=0)
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=1" in request.full_url


def test_list_todays_events_is_read_only():
    connector = GoogleCalendarConnector()
    action = connector.get_action("list_todays_events")
    assert action is not None
    assert action.risk_level == RiskLevel.READ_ONLY


def test_list_todays_events_never_leaks_access_token():
    payload = {"items": [{"id": "evt_1", "summary": "Meeting", "start": {"dateTime": "now"}}]}
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GoogleCalendarConnector().execute("list_todays_events")
    assert "superSecretCalendarAccessToken12345" not in result.output


# --- list_upcoming_events --------------------------------------------------------------


def test_list_upcoming_events_success():
    payload = {
        "items": [
            {"id": "evt_1", "summary": "Team sync", "start": {"dateTime": "2026-01-01T10:00:00Z"}},
            {"id": "evt_2", "summary": "Dentist", "start": {"date": "2026-01-02"}},
        ]
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GoogleCalendarConnector().execute("list_upcoming_events")

    assert result.ok is True
    assert "evt_1" in result.output
    assert "Team sync" in result.output
    assert "evt_2" in result.output
    assert "Dentist" in result.output


def test_list_upcoming_events_empty():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})):
            result = GoogleCalendarConnector().execute("list_upcoming_events")
    assert result.ok is True
    assert "no upcoming events" in result.output.lower()


def test_list_upcoming_events_uses_correct_endpoint():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_upcoming_events")
    request = mock_urlopen.call_args[0][0]
    assert "/calendars/primary/events" in request.full_url


def test_list_upcoming_events_respects_limit_parameter():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_upcoming_events", limit=5)
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=5" in request.full_url


def test_list_upcoming_events_default_limit_used_when_omitted():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_upcoming_events")
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=10" in request.full_url


def test_list_upcoming_events_limit_capped_at_max():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_upcoming_events", limit=1000)
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=50" in request.full_url


def test_list_upcoming_events_limit_floor_at_one():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_upcoming_events", limit=0)
    request = mock_urlopen.call_args[0][0]
    assert "maxResults=1" in request.full_url


def test_list_upcoming_events_orders_by_start_time():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_upcoming_events")
    request = mock_urlopen.call_args[0][0]
    assert "orderBy=startTime" in request.full_url


# --- get_event ------------------------------------------------------------------------


def test_get_event_success():
    payload = {
        "summary": "Team sync",
        "description": "Weekly sync meeting",
        "start": {"dateTime": "2026-01-01T10:00:00Z"},
        "end": {"dateTime": "2026-01-01T11:00:00Z"},
        "location": "Room 101",
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GoogleCalendarConnector().execute("get_event", event_id="evt_1")

    assert result.ok is True
    assert "Team sync" in result.output
    assert "Weekly sync meeting" in result.output
    assert "Room 101" in result.output


def test_get_event_minimal_fields():
    payload = {"start": {"date": "2026-01-02"}, "end": {"date": "2026-01-02"}}
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GoogleCalendarConnector().execute("get_event", event_id="evt_2")
    assert result.ok is True
    assert "(no title)" in result.output


def test_get_event_uses_correct_endpoint():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
            GoogleCalendarConnector().execute("get_event", event_id="evt_42")
    request = mock_urlopen.call_args[0][0]
    assert "/calendars/primary/events/evt_42" in request.full_url


def test_get_event_missing_event_id_raises():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({})):
            with pytest.raises(TypeError):
                GoogleCalendarConnector().execute("get_event")  # event_id is required


# --- authentication: Bearer token, never appears in plaintext-visible form -----------


def test_authorization_header_uses_bearer_scheme():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"items": []})) as mock_urlopen:
            GoogleCalendarConnector().execute("list_upcoming_events")
    request = mock_urlopen.call_args[0][0]
    auth_header = request.get_header("Authorization")
    assert auth_header == "Bearer superSecretAccessTokenValue12345"


def test_access_token_never_appears_in_successful_result_output():
    payload = {"items": [{"id": "evt_1", "summary": "Meeting", "start": {"dateTime": "now"}}]}
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GoogleCalendarConnector().execute("list_upcoming_events")
    assert "superSecretAccessTokenValue12345" not in result.output


# --- API error handling: never raises, never leaks the token ---------------------------


def test_http_error_returns_error_result_not_exception():
    http_error = urllib.error.HTTPError(
        url="https://www.googleapis.com/calendar/v3/calendars/primary/events",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=MagicMock(read=lambda: b'{"error": {"message": "Invalid Credentials"}}'),
    )
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=http_error):
            result = GoogleCalendarConnector().execute("list_upcoming_events")
    assert result.ok is False
    assert "401" in result.output


def test_http_error_never_leaks_access_token():
    http_error = urllib.error.HTTPError(
        url="https://www.googleapis.com/calendar/v3/calendars/primary/events",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=MagicMock(read=lambda: b'{"error": "superSecretAccessTokenValue12345 rejected"}'),
    )
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=http_error):
            result = GoogleCalendarConnector().execute("list_upcoming_events")
    assert "superSecretAccessTokenValue12345" not in result.output


def test_connection_error_returns_error_result():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            result = GoogleCalendarConnector().execute("list_upcoming_events")
    assert result.ok is False


def test_timeout_returns_error_result():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            result = GoogleCalendarConnector().execute("list_upcoming_events")
    assert result.ok is False
    assert "timed out" in result.output.lower()


def test_invalid_json_response_returns_error_result():
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"not valid json{{{"
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=cm):
            result = GoogleCalendarConnector().execute("list_upcoming_events")
    assert result.ok is False


def test_invalid_json_response_never_leaks_token():
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"not valid json superSecretAccessTokenValue12345"
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=cm):
            result = GoogleCalendarConnector().execute("list_upcoming_events")
    assert "superSecretAccessTokenValue12345" not in result.output


# --- refresh token never appears anywhere ------------------------------------------------


def test_refresh_token_never_appears_in_any_result():
    payload = {"items": []}
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = GoogleCalendarConnector().execute("list_upcoming_events")
    assert "superSecretRefreshTokenValue12345" not in result.output


# --- disconnect() clears stored OAuth tokens, makes no network call --------------------


def test_disconnect_calls_token_store_clear():
    with patch(
        "jarvis.integrations.connectors.google_calendar.TokenStore.clear", return_value=True
    ) as mock_clear:
        result = GoogleCalendarConnector().disconnect()
    mock_clear.assert_called_once()
    assert result is True


def test_disconnect_returns_false_when_nothing_stored():
    with patch(
        "jarvis.integrations.connectors.google_calendar.TokenStore.clear", return_value=False
    ):
        result = GoogleCalendarConnector().disconnect()
    assert result is False


def test_disconnect_makes_no_network_call():
    with patch("jarvis.integrations.connectors.google_calendar.TokenStore.clear", return_value=True):
        with patch("urllib.request.urlopen") as mock_urlopen:
            GoogleCalendarConnector().disconnect()
    mock_urlopen.assert_not_called()
