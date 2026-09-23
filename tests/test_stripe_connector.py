"""Tests for jarvis.integrations.connectors.stripe.StripeConnector: the
second real Connector implementation, REST API + API key auth, read-only
v1 (get_balance, list_recent_charges). No real network access -
urllib.request.urlopen is mocked throughout. Confirms: no write action
exists, is_configured() reflects the env var accurately, the API key is
never read from anywhere but the environment, every action degrades
gracefully (CredentialError when unconfigured, ExternalActionResult
(ok=False, ...) for HTTP/network failures), and the API key never
appears in any output, error message, or exception text."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from jarvis.integrations.base import CredentialError, RiskLevel
from jarvis.integrations.connectors.stripe import StripeConnector


@pytest.fixture
def configured_env(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_superSecretStripeKeyValue12345")


@pytest.fixture
def unconfigured_env(monkeypatch):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)


def _mock_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm


# --- is_configured ----------------------------------------------------------------


def test_is_configured_true_when_key_set(configured_env):
    assert StripeConnector().is_configured() is True


def test_is_configured_false_when_unset(unconfigured_env):
    assert StripeConnector().is_configured() is False


def test_is_configured_false_when_empty_string(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "")
    assert StripeConnector().is_configured() is False


# --- action list: only read-only actions exist -------------------------------------


def test_all_actions_are_read_only(configured_env):
    connector = StripeConnector()
    for action in connector.actions:
        assert action.risk_level == RiskLevel.READ_ONLY


def test_expected_four_actions_present(configured_env):
    connector = StripeConnector()
    names = {a.name for a in connector.actions}
    assert names == {
        "get_balance", "list_recent_charges",
        "get_charge_status", "list_recent_payment_intents",
    }


def test_no_write_action_exists(configured_env):
    connector = StripeConnector()
    names = {a.name for a in connector.actions}
    for forbidden in (
        "create_charge", "refund_charge", "create_payout", "create_refund",
        "update_customer", "capture_charge", "void_charge",
    ):
        assert forbidden not in names


def test_service_name_is_stripe(configured_env):
    assert StripeConnector().service_name == "stripe"


# --- execute() without credentials raises no exception, returns error result ------


def test_execute_unconfigured_returns_error_result(unconfigured_env):
    connector = StripeConnector()
    result = connector.execute("get_balance")
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_execute_never_calls_urlopen_when_unconfigured(unconfigured_env):
    connector = StripeConnector()
    with patch("urllib.request.urlopen") as mock_urlopen:
        connector.execute("get_balance")
    mock_urlopen.assert_not_called()


# --- execute() with an unknown action ------------------------------------------------


def test_execute_unknown_action_returns_error_result(configured_env):
    connector = StripeConnector()
    result = connector.execute("nonexistent_action")
    assert result.ok is False
    assert "Unknown action" in result.output


# --- get_balance --------------------------------------------------------------------


def test_get_balance_success(configured_env):
    payload = {
        "available": [{"amount": 10000, "currency": "usd"}],
        "pending": [{"amount": 500, "currency": "usd"}],
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = StripeConnector().execute("get_balance")

    assert result.ok is True
    assert "100.00 USD" in result.output
    assert "5.00 USD" in result.output
    assert "available" in result.output
    assert "pending" in result.output


def test_get_balance_empty_response(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({})):
        result = StripeConnector().execute("get_balance")
    assert result.ok is True
    assert "no balance data" in result.output.lower()


def test_get_balance_uses_correct_endpoint(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
        StripeConnector().execute("get_balance")
    request = mock_urlopen.call_args[0][0]
    assert request.full_url == "https://api.stripe.com/v1/balance"


def test_get_balance_uses_get_method(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
        StripeConnector().execute("get_balance")
    request = mock_urlopen.call_args[0][0]
    assert request.get_method() == "GET"


# --- list_recent_charges --------------------------------------------------------------


def test_list_recent_charges_success(configured_env):
    payload = {
        "data": [
            {"id": "ch_1", "amount": 2500, "currency": "usd", "status": "succeeded", "created": 1700000000},
            {"id": "ch_2", "amount": 500, "currency": "eur", "status": "pending", "created": 1700000100},
        ]
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = StripeConnector().execute("list_recent_charges")

    assert result.ok is True
    assert "ch_1" in result.output
    assert "25.00 USD" in result.output
    assert "ch_2" in result.output
    assert "5.00 EUR" in result.output
    assert "succeeded" in result.output
    assert "pending" in result.output


def test_list_recent_charges_empty(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})):
        result = StripeConnector().execute("list_recent_charges")
    assert result.ok is True
    assert "no charges found" in result.output.lower()


def test_list_recent_charges_respects_limit_parameter(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
        StripeConnector().execute("list_recent_charges", limit=5)
    request = mock_urlopen.call_args[0][0]
    assert "limit=5" in request.full_url


def test_list_recent_charges_default_limit_used_when_omitted(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
        StripeConnector().execute("list_recent_charges")
    request = mock_urlopen.call_args[0][0]
    assert "limit=10" in request.full_url


def test_list_recent_charges_limit_capped_at_max(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
        StripeConnector().execute("list_recent_charges", limit=1000)
    request = mock_urlopen.call_args[0][0]
    assert "limit=50" in request.full_url


def test_list_recent_charges_limit_floor_at_one(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
        StripeConnector().execute("list_recent_charges", limit=0)
    request = mock_urlopen.call_args[0][0]
    assert "limit=1" in request.full_url


# --- get_charge_status --------------------------------------------------------------


def test_get_charge_status_success(configured_env):
    payload = {"id": "ch_1", "amount": 2500, "currency": "usd", "status": "succeeded", "created": 1700000000}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = StripeConnector().execute("get_charge_status", charge_id="ch_1")

    assert result.ok is True
    assert "ch_1" in result.output
    assert "25.00 USD" in result.output
    assert "succeeded" in result.output


def test_get_charge_status_uses_correct_endpoint(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
        StripeConnector().execute("get_charge_status", charge_id="ch_abc123")
    request = mock_urlopen.call_args[0][0]
    assert request.full_url == "https://api.stripe.com/v1/charges/ch_abc123"


def test_get_charge_status_uses_get_method(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
        StripeConnector().execute("get_charge_status", charge_id="ch_1")
    request = mock_urlopen.call_args[0][0]
    assert request.get_method() == "GET"


def test_get_charge_status_empty_charge_id_returns_error_without_network_call(configured_env):
    with patch("urllib.request.urlopen") as mock_urlopen:
        result = StripeConnector().execute("get_charge_status", charge_id="")
    assert result.ok is False
    mock_urlopen.assert_not_called()


def test_get_charge_status_whitespace_charge_id_returns_error(configured_env):
    with patch("urllib.request.urlopen") as mock_urlopen:
        result = StripeConnector().execute("get_charge_status", charge_id="   ")
    assert result.ok is False
    mock_urlopen.assert_not_called()


def test_get_charge_status_strips_whitespace_from_charge_id(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
        StripeConnector().execute("get_charge_status", charge_id="  ch_1  ")
    request = mock_urlopen.call_args[0][0]
    assert request.full_url == "https://api.stripe.com/v1/charges/ch_1"


# --- list_recent_payment_intents ------------------------------------------------------


def test_list_recent_payment_intents_success(configured_env):
    payload = {
        "data": [
            {"id": "pi_1", "amount": 5000, "currency": "usd", "status": "succeeded", "created": 1700000000},
            {"id": "pi_2", "amount": 1500, "currency": "eur", "status": "requires_payment_method", "created": 1700000100},
        ]
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = StripeConnector().execute("list_recent_payment_intents")

    assert result.ok is True
    assert "pi_1" in result.output
    assert "50.00 USD" in result.output
    assert "pi_2" in result.output
    assert "15.00 EUR" in result.output
    assert "succeeded" in result.output
    assert "requires_payment_method" in result.output


def test_list_recent_payment_intents_empty(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})):
        result = StripeConnector().execute("list_recent_payment_intents")
    assert result.ok is True
    assert "no payment intents found" in result.output.lower()


def test_list_recent_payment_intents_uses_correct_endpoint(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
        StripeConnector().execute("list_recent_payment_intents")
    request = mock_urlopen.call_args[0][0]
    assert request.full_url.startswith("https://api.stripe.com/v1/payment_intents")


def test_list_recent_payment_intents_respects_limit_parameter(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
        StripeConnector().execute("list_recent_payment_intents", limit=5)
    request = mock_urlopen.call_args[0][0]
    assert "limit=5" in request.full_url


def test_list_recent_payment_intents_default_limit_used_when_omitted(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
        StripeConnector().execute("list_recent_payment_intents")
    request = mock_urlopen.call_args[0][0]
    assert "limit=10" in request.full_url


def test_list_recent_payment_intents_limit_capped_at_max(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
        StripeConnector().execute("list_recent_payment_intents", limit=1000)
    request = mock_urlopen.call_args[0][0]
    assert "limit=50" in request.full_url


def test_list_recent_payment_intents_limit_floor_at_one(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
        StripeConnector().execute("list_recent_payment_intents", limit=0)
    request = mock_urlopen.call_args[0][0]
    assert "limit=1" in request.full_url


# --- new actions never leak the API key, never require approval, stay read-only ------


def test_get_charge_status_never_leaks_api_key(configured_env):
    http_error = urllib.error.HTTPError(
        url="https://api.stripe.com/v1/charges/ch_1",
        code=404,
        msg="Not Found",
        hdrs=None,  # type: ignore[arg-type]
        fp=MagicMock(read=lambda: b'{"error": {"message": "sk_test_superSecretStripeKeyValue12345 rejected"}}'),
    )
    with patch("urllib.request.urlopen", side_effect=http_error):
        result = StripeConnector().execute("get_charge_status", charge_id="ch_1")
    assert "sk_test_superSecretStripeKeyValue12345" not in result.output


def test_list_recent_payment_intents_never_leaks_api_key(configured_env):
    payload = {"data": [{"id": "pi_1", "amount": 100, "currency": "usd", "status": "succeeded", "created": 1}]}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = StripeConnector().execute("list_recent_payment_intents")
    assert "sk_test_superSecretStripeKeyValue12345" not in result.output


def test_new_actions_are_also_read_only(configured_env):
    connector = StripeConnector()
    new_action_names = {"get_charge_status", "list_recent_payment_intents"}
    for action in connector.actions:
        if action.name in new_action_names:
            assert action.risk_level == RiskLevel.READ_ONLY


# --- authentication: HTTP Basic auth with the API key as username -----------------------


def test_authorization_header_uses_basic_auth_with_api_key(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
        StripeConnector().execute("get_balance")
    request = mock_urlopen.call_args[0][0]
    auth_header = request.get_header("Authorization")
    assert auth_header is not None
    assert auth_header.startswith("Basic ")


def test_authorization_header_never_contains_raw_key_in_plaintext(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({})) as mock_urlopen:
        StripeConnector().execute("get_balance")
    request = mock_urlopen.call_args[0][0]
    auth_header = request.get_header("Authorization")
    assert "sk_test_superSecretStripeKeyValue12345" not in auth_header  # base64-encoded, not raw


# --- API error handling: never raises, never leaks the key -----------------------------


def test_http_error_returns_error_result_not_exception(configured_env):
    http_error = urllib.error.HTTPError(
        url="https://api.stripe.com/v1/balance",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=MagicMock(read=lambda: b'{"error": {"message": "Invalid API Key provided"}}'),
    )
    with patch("urllib.request.urlopen", side_effect=http_error):
        result = StripeConnector().execute("get_balance")
    assert result.ok is False
    assert "401" in result.output


def test_http_error_never_leaks_api_key(configured_env):
    http_error = urllib.error.HTTPError(
        url="https://api.stripe.com/v1/balance",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=MagicMock(read=lambda: b'{"error": {"message": "sk_test_superSecretStripeKeyValue12345 is invalid"}}'),
    )
    with patch("urllib.request.urlopen", side_effect=http_error):
        result = StripeConnector().execute("get_balance")
    assert "sk_test_superSecretStripeKeyValue12345" not in result.output


def test_connection_error_returns_error_result(configured_env):
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        result = StripeConnector().execute("get_balance")
    assert result.ok is False
    assert "connection" in result.output.lower() or "error" in result.output.lower()


def test_timeout_returns_error_result(configured_env):
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        result = StripeConnector().execute("get_balance")
    assert result.ok is False
    assert "timed out" in result.output.lower()


def test_invalid_json_response_returns_error_result(configured_env):
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"not valid json{{{"
    with patch("urllib.request.urlopen", return_value=cm):
        result = StripeConnector().execute("get_balance")
    assert result.ok is False


def test_invalid_json_response_never_leaks_key(configured_env):
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"not valid json sk_test_superSecretStripeKeyValue12345"
    with patch("urllib.request.urlopen", return_value=cm):
        result = StripeConnector().execute("get_balance")
    assert "sk_test_superSecretStripeKeyValue12345" not in result.output


# --- credentials never leak in any successful result output -------------------------------


def test_api_key_never_appears_in_successful_result_output(configured_env):
    payload = {"available": [{"amount": 100, "currency": "usd"}]}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = StripeConnector().execute("get_balance")
    assert "sk_test_superSecretStripeKeyValue12345" not in result.output


def test_credential_error_message_never_leaks_key_when_unconfigured(unconfigured_env):
    connector = StripeConnector()
    result = connector.execute("get_balance")
    # No key was ever set, so nothing to redact, but confirm the error
    # message itself never invents or echoes a key-shaped value.
    assert "STRIPE_API_KEY" in result.output
    assert "sk_" not in result.output
