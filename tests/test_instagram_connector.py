"""Tests for jarvis.integrations.connectors.instagram.InstagramConnector:
the fifth real Connector implementation, Meta Graph API + OAuth-only
auth, read-only Stage 1A (get_profile, list_recent_media,
get_media_details, get_media_insights, list_comments,
list_recent_messages). No real network access - urllib.request.urlopen
is mocked throughout. No real OS keychain access either -
TokenStore.load() is mocked directly, never the real keyring backend
(mirrors test_gmail_connector.py/test_google_calendar_connector.py).
Confirms: no write action exists, is_configured() reflects ONLY stored
OAuth tokens (no password/env-var fallback), expired tokens are refused
rather than refreshed, every action degrades gracefully, the access
token never appears in any output/error/exception text, and this
connector never touches Gmail's or Google Calendar's stored tokens."""

from __future__ import annotations

import json
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from jarvis.integrations import instagram_history
from jarvis.integrations.base import CredentialError, RiskLevel
from jarvis.integrations.connectors.instagram import InstagramConnector
from jarvis.integrations.oauth import META_INSTAGRAM_OAUTH_PROVIDER, OAuthTokens

_ACCOUNT_ID = "17841400000000000"


@pytest.fixture(autouse=True)
def _isolated_instagram_history(tmp_path, monkeypatch):
    """record_daily_snapshot/compare_history persist to
    jarvis.integrations.instagram_history's JSON file - redirected to a
    per-test tmp_path file here so no test ever touches the real
    .jarvis/instagram_insights_history.json on disk."""
    history_file = tmp_path / "instagram_insights_history.json"
    monkeypatch.setattr(instagram_history, "INSTAGRAM_INSIGHTS_HISTORY_FILE", history_file)
    return history_file


def _tokens(**overrides) -> OAuthTokens:
    defaults = dict(
        access_token="superSecretInstagramAccessTokenValue12345",
        refresh_token="",
        expires_at=time.time() + 3600,
        scope=f"{META_INSTAGRAM_OAUTH_PROVIDER.scope}|{_ACCOUNT_ID}",
    )
    defaults.update(overrides)
    return OAuthTokens(**defaults)


def _mock_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm


def _with_tokens(tokens: OAuthTokens | None):
    return patch("jarvis.integrations.connectors.instagram.TokenStore.load", return_value=tokens)


# --- is_configured: OAuth-only, no password/env-var fallback ------------------------


def test_is_configured_true_when_tokens_stored():
    with _with_tokens(_tokens()):
        assert InstagramConnector().is_configured() is True


def test_is_configured_false_when_no_tokens_stored():
    with _with_tokens(None):
        assert InstagramConnector().is_configured() is False


def test_is_configured_ignores_environment_variables(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_APP_SECRET", "some-value")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "some-other-value")
    with _with_tokens(None):
        assert InstagramConnector().is_configured() is False


# --- action list: only read-only actions exist -------------------------------------


def test_all_actions_are_read_only():
    with _with_tokens(_tokens()):
        connector = InstagramConnector()
    for action in connector.actions:
        assert action.risk_level == RiskLevel.READ_ONLY


def test_expected_eleven_actions_present():
    connector = InstagramConnector()
    names = {a.name for a in connector.actions}
    assert names == {
        "get_profile", "list_recent_media", "get_media_details",
        "get_media_insights", "get_account_insights", "analyze_insights",
        "daily_report", "record_daily_snapshot", "compare_history",
        "list_comments", "list_recent_messages",
    }


def test_no_write_action_exists():
    connector = InstagramConnector()
    names = {a.name for a in connector.actions}
    for forbidden in (
        "publish_media", "create_media", "reply_to_comment", "delete_comment",
        "hide_comment", "send_message", "create_story", "create_reel",
        "delete_media", "update_media",
    ):
        assert forbidden not in names


def test_service_name_is_instagram():
    assert InstagramConnector().service_name == "instagram"


# --- execute() without tokens returns error result, never raises, never calls network --


def test_execute_unconfigured_returns_error_result():
    with _with_tokens(None):
        result = InstagramConnector().execute("get_profile")
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_execute_never_calls_urlopen_when_unconfigured():
    with _with_tokens(None):
        with patch("urllib.request.urlopen") as mock_urlopen:
            InstagramConnector().execute("get_profile")
    mock_urlopen.assert_not_called()


# --- execute() with an unknown action ------------------------------------------------


def test_execute_unknown_action_returns_error_result():
    with _with_tokens(_tokens()):
        result = InstagramConnector().execute("nonexistent_action")
    assert result.ok is False
    assert "Unknown action" in result.output


def test_execute_unknown_write_looking_action_returns_error_not_attempted():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = InstagramConnector().execute("publish_media", caption="hi")
    assert result.ok is False
    assert "Unknown action" in result.output
    mock_urlopen.assert_not_called()


# --- expired tokens: refuse rather than attempt a refresh -----------------------------


def test_expired_tokens_raise_credential_error_not_attempt_refresh():
    expired = _tokens(expires_at=time.time() - 3600)
    with _with_tokens(expired):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = InstagramConnector().execute("get_profile")
    assert result.ok is False
    assert "expired" in result.output.lower()
    mock_urlopen.assert_not_called()


def test_expired_tokens_never_call_refresh_access_token():
    expired = _tokens(expires_at=time.time() - 3600)
    with _with_tokens(expired):
        with patch("jarvis.integrations.oauth.refresh_access_token") as mock_refresh:
            InstagramConnector().execute("get_profile")
    mock_refresh.assert_not_called()


# --- account id resolution (scope-suffix encoding) -----------------------------------


def test_missing_account_id_in_scope_raises_credential_error():
    tokens_without_account = _tokens(scope=META_INSTAGRAM_OAUTH_PROVIDER.scope)  # no "|account_id" suffix
    with _with_tokens(tokens_without_account):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = InstagramConnector().execute("get_profile")
    assert result.ok is False
    assert "account id" in result.output.lower()
    mock_urlopen.assert_not_called()


# --- get_profile --------------------------------------------------------------


def test_get_profile_success():
    payload = {
        "username": "myshop", "name": "My Shop", "followers_count": 1234,
        "media_count": 56, "account_type": "BUSINESS",
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = InstagramConnector().execute("get_profile")

    assert result.ok is True
    assert "myshop" in result.output
    assert "1234" in result.output
    assert "BUSINESS" in result.output


def test_get_profile_uses_correct_endpoint_with_account_id():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
            InstagramConnector().execute("get_profile")
    request = mock_urlopen.call_args[0][0]
    assert f"/{_ACCOUNT_ID}" in request.full_url


def test_all_actions_use_graph_instagram_com_not_graph_facebook_com():
    # Regression test: a token issued by the Instagram Login flow (see
    # meta_instagram_oauth_setup.py) is only valid against
    # graph.instagram.com - sending it to graph.facebook.com produces
    # Meta error code 190 ("Invalid OAuth access token - Cannot parse
    # access token") even though the stored token itself is valid. Every
    # action must use the same, correct base URL.
    calls = {}

    def _record_and_respond(request, *a, **k):
        calls[request.full_url] = True
        return _mock_response({"data": []})

    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_record_and_respond):
            InstagramConnector().execute("get_profile")
            InstagramConnector().execute("list_recent_media")
            InstagramConnector().execute("list_recent_messages")

    assert calls, "no requests were recorded"
    for url in calls:
        assert url.startswith("https://graph.instagram.com/"), url
        assert "graph.facebook.com" not in url


# --- list_recent_media --------------------------------------------------------------


def test_list_recent_media_success():
    payload = {
        "data": [
            {"id": "media_1", "caption": "First post", "media_type": "IMAGE", "timestamp": "2026-01-01T10:00:00+0000"},
            {"id": "media_2", "caption": "Second post", "media_type": "VIDEO", "timestamp": "2026-01-02T10:00:00+0000"},
        ]
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = InstagramConnector().execute("list_recent_media")

    assert result.ok is True
    assert "media_1" in result.output
    assert "First post" in result.output
    assert "media_2" in result.output


def test_list_recent_media_empty():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})):
            result = InstagramConnector().execute("list_recent_media")
    assert result.ok is True
    assert "no posts found" in result.output.lower()


def test_list_recent_media_respects_limit_parameter():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
            InstagramConnector().execute("list_recent_media", limit=5)
    request = mock_urlopen.call_args[0][0]
    assert "limit=5" in request.full_url


def test_list_recent_media_limit_capped_at_max():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
            InstagramConnector().execute("list_recent_media", limit=1000)
    request = mock_urlopen.call_args[0][0]
    assert "limit=50" in request.full_url


def test_list_recent_media_limit_floor_at_one():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
            InstagramConnector().execute("list_recent_media", limit=0)
    request = mock_urlopen.call_args[0][0]
    assert "limit=1" in request.full_url


# --- get_media_details --------------------------------------------------------------


def test_get_media_details_success():
    payload = {
        "id": "media_1", "caption": "A nice photo", "media_type": "IMAGE",
        "permalink": "https://instagram.com/p/xyz", "timestamp": "2026-01-01T10:00:00+0000",
        "like_count": 42, "comments_count": 3,
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = InstagramConnector().execute("get_media_details", media_id="media_1")

    assert result.ok is True
    assert "media_1" in result.output
    assert "42" in result.output
    assert "A nice photo" in result.output


def test_get_media_details_empty_media_id_returns_error_without_network_call():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = InstagramConnector().execute("get_media_details", media_id="")
    assert result.ok is False
    mock_urlopen.assert_not_called()


def test_get_media_details_missing_media_id_raises():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({})):
            with pytest.raises(TypeError):
                InstagramConnector().execute("get_media_details")


# --- get_media_insights --------------------------------------------------------------


def test_get_media_insights_success():
    payload = {
        "data": [
            {"name": "likes", "values": [{"value": 12}]},
            {"name": "comments", "values": [{"value": 3}]},
            {"name": "shares", "values": [{"value": 1}]},
            {"name": "saved", "values": [{"value": 5}]},
            {"name": "total_interactions", "values": [{"value": 21}]},
            {"name": "reach", "values": [{"value": 400}]},
        ]
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = InstagramConnector().execute("get_media_insights", media_id="media_1")

    assert result.ok is True
    assert "likes: 12" in result.output
    assert "comments: 3" in result.output
    assert "shares: 1" in result.output
    assert "saved: 5" in result.output
    assert "total_interactions: 21" in result.output
    assert "reach: 400" in result.output


def test_get_media_insights_requests_current_metric_set_not_deprecated_names():
    # Regression test: "impressions" and "engagement" were confirmed (via
    # a live Meta API error response) to be rejected for this media
    # product type - "engagement" was renamed to "total_interactions" and
    # "impressions" is not in the currently accepted metric list at all.
    # This must never regress back to the old, now-rejected metric names.
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
            InstagramConnector().execute("get_media_insights", media_id="media_1")
    request = mock_urlopen.call_args[0][0]
    assert "likes" in request.full_url
    assert "total_interactions" in request.full_url
    assert "engagement" not in request.full_url
    # "impressions" must not appear as the metric param value - note this
    # does not assert general absence, since "impressions" is a substring
    # of nothing else here, so a plain "not in" is safe and precise.
    assert "impressions" not in request.full_url


def test_get_media_insights_no_data_available():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})):
            result = InstagramConnector().execute("get_media_insights", media_id="media_1")
    assert result.ok is True
    assert "no insights data available" in result.output.lower()


def test_get_media_insights_empty_media_id_returns_error_without_network_call():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = InstagramConnector().execute("get_media_insights", media_id="")
    assert result.ok is False
    mock_urlopen.assert_not_called()


# --- get_account_insights --------------------------------------------------------------


def test_get_account_insights_success():
    payload = {
        "data": [
            {
                "name": "reach",
                "values": [
                    {"value": 133, "end_time": "2026-09-21T07:00:00+0000"},
                    {"value": 28, "end_time": "2026-09-22T07:00:00+0000"},
                ],
            },
        ]
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = InstagramConnector().execute("get_account_insights")

    assert result.ok is True
    assert "reach: 133 (as of 2026-09-21T07:00:00+0000)" in result.output
    assert "reach: 28 (as of 2026-09-22T07:00:00+0000)" in result.output


def test_get_account_insights_no_data_available():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})):
            result = InstagramConnector().execute("get_account_insights")
    assert result.ok is True
    assert "no account insights data available" in result.output.lower()


def test_get_account_insights_uses_account_id_endpoint_not_media_id():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
            InstagramConnector().execute("get_account_insights")
    request = mock_urlopen.call_args[0][0]
    assert f"/{_ACCOUNT_ID}/insights" in request.full_url


def test_get_account_insights_includes_profile_views_metric():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
            InstagramConnector().execute("get_account_insights")
    request = mock_urlopen.call_args[0][0]
    assert "profile_views" in request.full_url


def test_get_account_insights_default_period_is_day():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
            InstagramConnector().execute("get_account_insights")
    request = mock_urlopen.call_args[0][0]
    assert "period=day" in request.full_url


def test_get_account_insights_respects_custom_period():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
            InstagramConnector().execute("get_account_insights", period="week")
    request = mock_urlopen.call_args[0][0]
    assert "period=week" in request.full_url


def test_get_account_insights_never_leaks_access_token():
    payload = {"data": [{"name": "reach", "values": [{"value": 10, "end_time": "2026-01-01T00:00:00+0000"}]}]}
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = InstagramConnector().execute("get_account_insights")
    assert "superSecretInstagramAccessTokenValue12345" not in result.output


def test_get_account_insights_is_read_only():
    connector = InstagramConnector()
    action = connector.get_action("get_account_insights")
    assert action is not None
    assert action.risk_level == RiskLevel.READ_ONLY


def test_get_account_insights_never_offers_a_write_action():
    connector = InstagramConnector()
    assert connector.get_action("update_account_insights") is None


# --- analyze_insights --------------------------------------------------------------


def _routed_urlopen(routes: dict[str, dict]):
    """A urlopen side_effect that returns a different payload depending on
    which endpoint path is in the request URL - needed because
    analyze_insights makes several distinct GET requests (account
    insights, media list, then one insights call per media item) in a
    single execute() call, unlike every other action here which makes
    exactly one."""

    def _side_effect(request, timeout=None):
        url = request.full_url
        for fragment, payload in routes.items():
            if fragment in url:
                return _mock_response(payload)
        raise AssertionError(f"Unrouted urlopen call: {url}")

    return _side_effect


def test_analyze_insights_account_level_percent_change():
    routes = {
        "/insights": {
            "data": [
                {
                    "name": "reach",
                    "values": [
                        {"value": 100, "end_time": "2026-09-21T07:00:00+0000"},
                        {"value": 150, "end_time": "2026-09-22T07:00:00+0000"},
                    ],
                },
            ]
        },
        "/media": {"data": [{"id": "m1", "timestamp": "2026-09-22T00:00:00+0000"}]},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("analyze_insights")

    assert result.ok is True
    assert "reach" in result.output
    assert "100 -> 150" in result.output
    assert "+50.0%" in result.output
    assert "↑ augo" in result.output


def test_analyze_insights_account_level_flat_when_unchanged():
    routes = {
        "/insights": {
            "data": [
                {
                    "name": "reach",
                    "values": [
                        {"value": 50, "end_time": "2026-09-21T07:00:00+0000"},
                        {"value": 50, "end_time": "2026-09-22T07:00:00+0000"},
                    ],
                },
            ]
        },
        "/media": {"data": [{"id": "m1"}]},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("analyze_insights")
    assert "→ nepakito" in result.output


def test_analyze_insights_account_level_insufficient_data():
    routes = {
        "/insights": {
            "data": [
                {
                    "name": "reach",
                    "values": [{"value": 50, "end_time": "2026-09-22T07:00:00+0000"}],
                },
            ]
        },
        "/media": {"data": [{"id": "m1"}]},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("analyze_insights")
    assert "nepakanka duomenų" in result.output.lower()


def test_analyze_insights_media_level_compares_latest_to_prior_average():
    routes = {
        "/insights": {"data": [{"name": "reach", "values": [{"value": 10, "end_time": "x"}]}]},
        "/media": {
            "data": [
                {"id": "latest_media"},
                {"id": "old_media_1"},
                {"id": "old_media_2"},
            ]
        },
    }

    def _side_effect(request, timeout=None):
        url = request.full_url
        if "/latest_media/insights" in url:
            return _mock_response({"data": [{"name": "likes", "values": [{"value": 20}]}]})
        if "/old_media_1/insights" in url:
            return _mock_response({"data": [{"name": "likes", "values": [{"value": 10}]}]})
        if "/old_media_2/insights" in url:
            return _mock_response({"data": [{"name": "likes", "values": [{"value": 30}]}]})
        if "/media" in url and "/insights" not in url:
            return _mock_response(routes["/media"])
        if url.endswith("/insights") or "/insights?" in url:
            return _mock_response(routes["/insights"])
        raise AssertionError(f"Unrouted urlopen call: {url}")

    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_side_effect):
            result = InstagramConnector().execute("analyze_insights")

    assert result.ok is True
    # latest=20, prior average of [10, 30] = 20 -> 0% change, flat
    assert "likes: 20 -> 20 (+0.0%) → nepakito" in result.output


def test_analyze_insights_media_level_insufficient_posts():
    routes = {
        "/insights": {"data": []},
        "/media": {"data": [{"id": "only_one_post"}]},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("analyze_insights")
    assert "nepakanka įrašų" in result.output.lower()


def test_analyze_insights_zero_baseline_reported_as_new_result_not_percent():
    routes = {
        "/insights": {
            "data": [{"name": "reach", "values": [
                {"value": 0, "end_time": "2026-09-21T07:00:00+0000"},
                {"value": 40, "end_time": "2026-09-22T07:00:00+0000"},
            ]}]
        },
        "/media": {"data": [{"id": "m1"}]},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("analyze_insights")
    assert "naujas rezultatas" in result.output.lower()
    assert "%" not in result.output.split("naujas rezultatas")[0].split("\n")[-1]


def test_analyze_insights_respects_custom_compare_media_count():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen({
            "/insights": {"data": []},
            "/media": {"data": [{"id": "m1"}]},
        })) as mock_urlopen:
            InstagramConnector().execute("analyze_insights", compare_media_count=3)
    media_call = next(
        c for c in mock_urlopen.call_args_list if "/media?" in c[0][0].full_url
    )
    assert "limit=4" in media_call[0][0].full_url  # compare_media_count + 1


def test_analyze_insights_never_leaks_access_token():
    routes = {
        "/insights": {"data": [{"name": "reach", "values": [{"value": 10, "end_time": "x"}]}]},
        "/media": {"data": [{"id": "m1"}]},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("analyze_insights")
    assert "superSecretInstagramAccessTokenValue12345" not in result.output


def test_analyze_insights_is_read_only():
    connector = InstagramConnector()
    action = connector.get_action("analyze_insights")
    assert action is not None
    assert action.risk_level == RiskLevel.READ_ONLY


def test_analyze_insights_output_is_in_lithuanian():
    routes = {
        "/insights": {"data": [{"name": "reach", "values": [{"value": 10, "end_time": "x"}]}]},
        "/media": {"data": [{"id": "m1"}]},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("analyze_insights")
    assert "Paskyros rodikliai" in result.output


def test_analyze_insights_makes_no_write_call():
    # A read-only smoke check that only GET requests (via urllib.request
    # .Request(..., method="GET"), the same _get() helper every other
    # action uses) are ever issued - analyze_insights composes existing
    # read-only calls and never constructs its own request.
    routes = {
        "/insights": {"data": [{"name": "reach", "values": [{"value": 10, "end_time": "x"}]}]},
        "/media": {"data": [{"id": "m1"}]},
    }
    captured_methods = []

    def _side_effect(request, timeout=None):
        captured_methods.append(request.get_method())
        return _routed_urlopen(routes)(request, timeout)

    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_side_effect):
            InstagramConnector().execute("analyze_insights")
    assert all(m == "GET" for m in captured_methods)


# --- daily_report --------------------------------------------------------------


def _today_ts(hour: int = 10) -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=hour, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+0000")


def _yesterday_ts(hour: int = 10) -> str:
    now = datetime.now(timezone.utc) - timedelta(days=1)
    return now.replace(hour=hour, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+0000")


def test_is_today_true_for_a_timestamp_from_today():
    assert InstagramConnector._is_today(_today_ts()) is True


def test_is_today_false_for_a_timestamp_from_yesterday():
    assert InstagramConnector._is_today(_yesterday_ts()) is False


def test_is_today_false_for_missing_or_malformed_timestamp():
    assert InstagramConnector._is_today(None) is False
    assert InstagramConnector._is_today("") is False
    assert InstagramConnector._is_today("not-a-timestamp") is False


def test_daily_report_no_posts_published_today_states_it_plainly():
    routes = {
        "/insights": {
            "data": [
                {
                    "name": "reach",
                    "values": [
                        {"value": 100, "end_time": "2020-01-01T07:00:00+0000"},
                        {"value": 120, "end_time": "2020-01-02T07:00:00+0000"},
                    ],
                },
            ]
        },
        "/media": {"data": [{"id": "old_post", "timestamp": _yesterday_ts()}]},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("daily_report")

    assert result.ok is True
    assert "nepaskelbta nė vieno įrašo" in result.output
    assert "likes: neprieinama" in result.output
    assert "media_id=old_post" not in result.output


def test_daily_report_sums_totals_across_todays_posts_only():
    routes = {
        "/insights": {"data": []},
        "/media": {
            "data": [
                {"id": "today_post_1", "timestamp": _today_ts(9)},
                {"id": "today_post_2", "timestamp": _today_ts(15)},
                {"id": "yesterday_post", "timestamp": _yesterday_ts()},
            ]
        },
    }

    def _side_effect(request, timeout=None):
        url = request.full_url
        if "/today_post_1/insights" in url:
            return _mock_response({"data": [
                {"name": "likes", "values": [{"value": 5}]},
                {"name": "total_interactions", "values": [{"value": 6}]},
            ]})
        if "/today_post_2/insights" in url:
            return _mock_response({"data": [
                {"name": "likes", "values": [{"value": 3}]},
                {"name": "total_interactions", "values": [{"value": 9}]},
            ]})
        if "/yesterday_post/insights" in url:
            raise AssertionError("yesterday's post must never be queried for daily_report totals")
        if "/media" in url and "/insights" not in url:
            return _mock_response(routes["/media"])
        if "/insights" in url:
            return _mock_response(routes["/insights"])
        raise AssertionError(f"Unrouted urlopen call: {url}")

    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_side_effect):
            result = InstagramConnector().execute("daily_report")

    assert result.ok is True
    assert "likes: 8" in result.output  # 5 + 3
    assert "2 įrašas" in result.output or "2 įrašai" in result.output or "(iš viso, 2" in result.output


def test_daily_report_identifies_best_performer_by_total_interactions():
    routes = {
        "/insights": {"data": []},
        "/media": {
            "data": [
                {"id": "low_post", "timestamp": _today_ts(9)},
                {"id": "high_post", "timestamp": _today_ts(15)},
            ]
        },
    }

    def _side_effect(request, timeout=None):
        url = request.full_url
        if "/low_post/insights" in url:
            return _mock_response({"data": [{"name": "total_interactions", "values": [{"value": 2}]}]})
        if "/high_post/insights" in url:
            return _mock_response({"data": [{"name": "total_interactions", "values": [{"value": 50}]}]})
        if "/media" in url and "/insights" not in url:
            return _mock_response(routes["/media"])
        if "/insights" in url:
            return _mock_response(routes["/insights"])
        raise AssertionError(f"Unrouted urlopen call: {url}")

    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_side_effect):
            result = InstagramConnector().execute("daily_report")

    assert "media_id=high_post" in result.output
    assert "media_id=low_post" not in result.output


def test_daily_report_reach_change_vs_prior_day():
    routes = {
        "/insights": {
            "data": [
                {
                    "name": "reach",
                    "values": [
                        {"value": 200, "end_time": "2020-01-01T07:00:00+0000"},
                        {"value": 300, "end_time": "2020-01-02T07:00:00+0000"},
                    ],
                },
            ]
        },
        "/media": {"data": []},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("daily_report")
    assert "200 -> 300" in result.output
    assert "+50.0%" in result.output
    assert "↑ augo" in result.output


def test_daily_report_reach_insufficient_data():
    routes = {
        "/insights": {"data": [{"name": "reach", "values": [{"value": 10, "end_time": "x"}]}]},
        "/media": {"data": []},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("daily_report")
    assert "nepakanka duomenų" in result.output.lower()


def test_daily_report_reach_no_data_at_all():
    routes = {
        "/insights": {"data": []},
        "/media": {"data": []},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("daily_report")
    assert "reach: duomenų nėra." in result.output


def test_daily_report_never_leaks_access_token():
    routes = {
        "/insights": {"data": [{"name": "reach", "values": [{"value": 10, "end_time": "x"}]}]},
        "/media": {"data": [{"id": "m1", "timestamp": _today_ts()}]},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("daily_report")
    assert "superSecretInstagramAccessTokenValue12345" not in result.output


def test_daily_report_is_read_only():
    connector = InstagramConnector()
    action = connector.get_action("daily_report")
    assert action is not None
    assert action.risk_level == RiskLevel.READ_ONLY


def test_daily_report_never_offers_a_write_action():
    connector = InstagramConnector()
    assert connector.get_action("update_daily_report") is None


def test_daily_report_output_is_in_lithuanian():
    routes = {
        "/insights": {"data": []},
        "/media": {"data": []},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("daily_report")
    assert "Reach pokytis" in result.output
    assert "Šiandien paskelbtų įrašų" in result.output


# --- record_daily_snapshot --------------------------------------------------------------


def test_record_daily_snapshot_saves_reach_and_persists_it():
    routes = {
        "/insights": {"data": [{"name": "reach", "values": [{"value": 42, "end_time": "x"}]}]},
        "/media": {"data": []},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("record_daily_snapshot")

    assert result.ok is True
    assert "reach: 42" in result.output
    today_str = datetime.now(timezone.utc).date().isoformat()
    stored = instagram_history.get_snapshot(today_str)
    assert stored == {"reach": 42.0}


def test_record_daily_snapshot_sums_todays_media_metrics():
    routes = {"data": [{"id": "today_post", "timestamp": _today_ts()}]}

    def _side_effect(request, timeout=None):
        url = request.full_url
        if "/today_post/insights" in url:
            return _mock_response({"data": [
                {"name": "likes", "values": [{"value": 7}]},
                {"name": "total_interactions", "values": [{"value": 9}]},
            ]})
        if "/media" in url and "/insights" not in url:
            return _mock_response(routes)
        if "/insights" in url:
            return _mock_response({"data": []})
        raise AssertionError(f"Unrouted urlopen call: {url}")

    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_side_effect):
            result = InstagramConnector().execute("record_daily_snapshot")

    assert result.ok is True
    today_str = datetime.now(timezone.utc).date().isoformat()
    stored = instagram_history.get_snapshot(today_str)
    assert stored == {"likes": 7.0, "total_interactions": 9.0}


def test_record_daily_snapshot_includes_profile_views_when_available():
    routes = {
        "/insights": {
            "data": [
                {"name": "reach", "values": [{"value": 10, "end_time": "x"}]},
                {"name": "profile_views", "values": [{"value": 3, "end_time": "x"}]},
            ]
        },
        "/media": {"data": []},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            InstagramConnector().execute("record_daily_snapshot")
    today_str = datetime.now(timezone.utc).date().isoformat()
    stored = instagram_history.get_snapshot(today_str)
    assert stored == {"reach": 10.0, "profile_views": 3.0}


def test_record_daily_snapshot_no_data_at_all_saves_nothing():
    routes = {"/insights": {"data": []}, "/media": {"data": []}}
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("record_daily_snapshot")

    assert result.ok is True
    assert "nieko neįrašyta" in result.output.lower()
    today_str = datetime.now(timezone.utc).date().isoformat()
    assert instagram_history.get_snapshot(today_str) is None


def test_record_daily_snapshot_rerun_same_day_overwrites_not_duplicates():
    routes_first = {
        "/insights": {"data": [{"name": "reach", "values": [{"value": 10, "end_time": "x"}]}]},
        "/media": {"data": []},
    }
    routes_second = {
        "/insights": {"data": [{"name": "reach", "values": [{"value": 20, "end_time": "x"}]}]},
        "/media": {"data": []},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes_first)):
            InstagramConnector().execute("record_daily_snapshot")
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes_second)):
            InstagramConnector().execute("record_daily_snapshot")

    today_str = datetime.now(timezone.utc).date().isoformat()
    assert instagram_history.get_snapshot(today_str) == {"reach": 20.0}
    assert len(instagram_history.load_history().entries) == 1


def test_record_daily_snapshot_never_leaks_access_token():
    routes = {
        "/insights": {"data": [{"name": "reach", "values": [{"value": 10, "end_time": "x"}]}]},
        "/media": {"data": []},
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=_routed_urlopen(routes)):
            result = InstagramConnector().execute("record_daily_snapshot")
    assert "superSecretInstagramAccessTokenValue12345" not in result.output


def test_record_daily_snapshot_is_read_only():
    connector = InstagramConnector()
    action = connector.get_action("record_daily_snapshot")
    assert action is not None
    assert action.risk_level == RiskLevel.READ_ONLY


# --- compare_history --------------------------------------------------------------


def test_compare_history_today_vs_yesterday():
    today_str = datetime.now(timezone.utc).date().isoformat()
    yesterday_str = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    instagram_history.upsert_snapshot(yesterday_str, {"reach": 100})
    instagram_history.upsert_snapshot(today_str, {"reach": 150})

    with _with_tokens(_tokens()):
        result = InstagramConnector().execute("compare_history")

    assert result.ok is True
    assert "Šiandien vs vakar" in result.output
    assert "100 -> 150" in result.output
    assert "+50.0%" in result.output


def test_compare_history_today_vs_seven_days_ago():
    today_str = datetime.now(timezone.utc).date().isoformat()
    seven_days_ago_str = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
    instagram_history.upsert_snapshot(seven_days_ago_str, {"likes": 10})
    instagram_history.upsert_snapshot(today_str, {"likes": 20})

    with _with_tokens(_tokens()):
        result = InstagramConnector().execute("compare_history")

    assert "Šiandien vs prieš 7 dienas" in result.output
    assert "10 -> 20" in result.output


def test_compare_history_last_7_days_vs_prior_7_days():
    today = datetime.now(timezone.utc).date()
    for i in range(0, 7):
        instagram_history.upsert_snapshot((today - timedelta(days=i)).isoformat(), {"reach": 10})
    for i in range(7, 14):
        instagram_history.upsert_snapshot((today - timedelta(days=i)).isoformat(), {"reach": 5})

    with _with_tokens(_tokens()):
        result = InstagramConnector().execute("compare_history")

    assert "Paskutinės 7 dienos vs ankstesnės 7 dienos" in result.output
    # last 7 days sum = 70, prior 7 days sum = 35 -> +100%
    assert "70 -> 35" not in result.output  # sanity: order must be prior -> last
    assert "35 -> 70" in result.output
    assert "+100.0%" in result.output


def test_compare_history_missing_date_reports_unavailable_never_guesses():
    today_str = datetime.now(timezone.utc).date().isoformat()
    instagram_history.upsert_snapshot(today_str, {"reach": 100})
    # No entry for yesterday at all.

    with _with_tokens(_tokens()):
        result = InstagramConnector().execute("compare_history")

    assert "Nėra išsaugotų duomenų datai" in result.output


def test_compare_history_empty_history_reports_unavailable_everywhere():
    with _with_tokens(_tokens()):
        result = InstagramConnector().execute("compare_history")

    assert result.ok is True
    assert "Nėra išsaugotų duomenų datai" in result.output
    assert "Nepakanka išsaugotų duomenų" in result.output


def test_compare_history_makes_no_live_api_call():
    today_str = datetime.now(timezone.utc).date().isoformat()
    yesterday_str = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    instagram_history.upsert_snapshot(yesterday_str, {"reach": 100})
    instagram_history.upsert_snapshot(today_str, {"reach": 150})

    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen") as mock_urlopen:
            InstagramConnector().execute("compare_history")
    mock_urlopen.assert_not_called()


def test_compare_history_metric_missing_on_one_day_is_skipped_not_guessed():
    today_str = datetime.now(timezone.utc).date().isoformat()
    yesterday_str = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    instagram_history.upsert_snapshot(yesterday_str, {"reach": 100})
    instagram_history.upsert_snapshot(today_str, {"reach": 150, "likes": 5})

    with _with_tokens(_tokens()):
        result = InstagramConnector().execute("compare_history")

    assert "likes: duomenų nėra vienai iš dienų" in result.output


def test_compare_history_is_read_only():
    connector = InstagramConnector()
    action = connector.get_action("compare_history")
    assert action is not None
    assert action.risk_level == RiskLevel.READ_ONLY


def test_compare_history_never_offers_a_write_action():
    connector = InstagramConnector()
    assert connector.get_action("update_history") is None
    assert connector.get_action("delete_history") is None


# --- list_comments --------------------------------------------------------------


def test_list_comments_success():
    payload = {
        "data": [
            {"id": "c1", "username": "alice", "text": "Nice!", "timestamp": "2026-01-01T11:00:00+0000"},
            {"id": "c2", "username": "bob", "text": "Love it", "timestamp": "2026-01-01T12:00:00+0000"},
        ]
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = InstagramConnector().execute("list_comments", media_id="media_1")

    assert result.ok is True
    assert "alice" in result.output
    assert "Nice!" in result.output
    assert "bob" in result.output


def test_list_comments_empty():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})):
            result = InstagramConnector().execute("list_comments", media_id="media_1")
    assert result.ok is True
    assert "no comments found" in result.output.lower()


def test_list_comments_empty_media_id_returns_error_without_network_call():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = InstagramConnector().execute("list_comments", media_id="")
    assert result.ok is False
    mock_urlopen.assert_not_called()


def test_list_comments_respects_limit_parameter():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
            InstagramConnector().execute("list_comments", media_id="media_1", limit=3)
    request = mock_urlopen.call_args[0][0]
    assert "limit=3" in request.full_url


def test_list_comments_never_offers_a_reply_or_delete_action():
    # Sanity: the connector's action list has no comment-write action at
    # all - already covered by test_no_write_action_exists, but this
    # explicitly re-confirms for the comments feature specifically.
    connector = InstagramConnector()
    assert connector.get_action("reply_to_comment") is None
    assert connector.get_action("delete_comment") is None


# --- list_recent_messages --------------------------------------------------------------


def test_list_recent_messages_success():
    payload = {
        "data": [
            {
                "id": "conv_1",
                "participants": {"data": [{"username": "carol", "id": "999"}]},
                "messages": {"data": [{"message": "Hey there!", "created_time": "2026-01-01T09:00:00+0000"}]},
            }
        ]
    }
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = InstagramConnector().execute("list_recent_messages")

    assert result.ok is True
    assert "conv_1" in result.output
    assert "carol" in result.output
    assert "Hey there!" in result.output


def test_list_recent_messages_empty():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})):
            result = InstagramConnector().execute("list_recent_messages")
    assert result.ok is True
    assert "no conversations found" in result.output.lower()


def test_list_recent_messages_respects_limit_parameter():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
            InstagramConnector().execute("list_recent_messages", limit=2)
    request = mock_urlopen.call_args[0][0]
    assert "limit=2" in request.full_url


def test_list_recent_messages_limit_capped_at_max():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
            InstagramConnector().execute("list_recent_messages", limit=1000)
    request = mock_urlopen.call_args[0][0]
    assert "limit=25" in request.full_url


def test_list_recent_messages_never_offers_a_send_action():
    connector = InstagramConnector()
    assert connector.get_action("send_message") is None


# --- authentication: access token passed as query param, never appears in plaintext-visible form --


def test_access_token_sent_as_query_parameter():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
            InstagramConnector().execute("get_profile")
    request = mock_urlopen.call_args[0][0]
    assert "access_token=superSecretInstagramAccessTokenValue12345" in request.full_url


def test_access_token_never_appears_in_successful_result_output():
    payload = {"username": "x", "name": "y"}
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = InstagramConnector().execute("get_profile")
    assert "superSecretInstagramAccessTokenValue12345" not in result.output


# --- API error handling: never raises, never leaks the token, rate limit handled distinctly ------


def test_http_error_returns_error_result_not_exception():
    http_error = urllib.error.HTTPError(
        url="https://graph.instagram.com/me",
        code=400,
        msg="Bad Request",
        hdrs=None,  # type: ignore[arg-type]
        fp=MagicMock(read=lambda: b'{"error": {"message": "Invalid parameter"}}'),
    )
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=http_error):
            result = InstagramConnector().execute("get_profile")
    assert result.ok is False
    assert "400" in result.output


def test_http_error_never_leaks_access_token():
    http_error = urllib.error.HTTPError(
        url="https://graph.instagram.com/me",
        code=400,
        msg="Bad Request",
        hdrs=None,  # type: ignore[arg-type]
        fp=MagicMock(read=lambda: b'{"error": "superSecretInstagramAccessTokenValue12345 rejected"}'),
    )
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=http_error):
            result = InstagramConnector().execute("get_profile")
    assert "superSecretInstagramAccessTokenValue12345" not in result.output


def test_rate_limit_429_reports_a_distinct_clear_message():
    http_error = urllib.error.HTTPError(
        url="https://graph.instagram.com/me",
        code=429,
        msg="Too Many Requests",
        hdrs=None,  # type: ignore[arg-type]
        fp=MagicMock(read=lambda: b'{"error": {"message": "rate limited"}}'),
    )
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=http_error):
            result = InstagramConnector().execute("get_profile")
    assert result.ok is False
    assert "rate limit" in result.output.lower()
    assert "temporary" in result.output.lower()


def test_connection_error_returns_error_result():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            result = InstagramConnector().execute("get_profile")
    assert result.ok is False


def test_timeout_returns_error_result():
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            result = InstagramConnector().execute("get_profile")
    assert result.ok is False
    assert "timed out" in result.output.lower()


def test_invalid_json_response_returns_error_result():
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"not valid json{{{"
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=cm):
            result = InstagramConnector().execute("get_profile")
    assert result.ok is False


def test_invalid_json_response_never_leaks_token():
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"not valid json superSecretInstagramAccessTokenValue12345"
    with _with_tokens(_tokens()):
        with patch("urllib.request.urlopen", return_value=cm):
            result = InstagramConnector().execute("get_profile")
    assert "superSecretInstagramAccessTokenValue12345" not in result.output


# --- disconnect() clears stored OAuth tokens, makes no network call --------------------


def test_disconnect_calls_token_store_clear():
    with patch("jarvis.integrations.connectors.instagram.TokenStore.clear", return_value=True) as mock_clear:
        result = InstagramConnector().disconnect()
    mock_clear.assert_called_once()
    assert result is True


def test_disconnect_returns_false_when_nothing_stored():
    with patch("jarvis.integrations.connectors.instagram.TokenStore.clear", return_value=False):
        result = InstagramConnector().disconnect()
    assert result is False


def test_disconnect_makes_no_network_call():
    with patch("jarvis.integrations.connectors.instagram.TokenStore.clear", return_value=True):
        with patch("urllib.request.urlopen") as mock_urlopen:
            InstagramConnector().disconnect()
    mock_urlopen.assert_not_called()


# --- isolation from Gmail/Google Calendar: separate TokenStore, never touched --------------


def test_instagram_uses_its_own_token_store_service_name():
    connector = InstagramConnector()
    assert connector._token_store._keyring_service == "jarvis-oauth-instagram"


def test_instagram_connector_never_touches_gmail_token_store():
    with patch("jarvis.integrations.connectors.gmail.TokenStore.load") as mock_gmail_load:
        with _with_tokens(_tokens()):
            with patch("urllib.request.urlopen", return_value=_mock_response({})):
                InstagramConnector().execute("get_profile")
    mock_gmail_load.assert_not_called()


def test_instagram_connector_never_touches_google_calendar_token_store():
    with patch("jarvis.integrations.connectors.google_calendar.TokenStore.load") as mock_calendar_load:
        with _with_tokens(_tokens()):
            with patch("urllib.request.urlopen", return_value=_mock_response({})):
                InstagramConnector().execute("get_profile")
    mock_calendar_load.assert_not_called()
