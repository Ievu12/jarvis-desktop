"""Tests for jarvis.tools.stripe_tools: the four agent-facing Tool
wrappers around StripeConnector. Confirms each tool delegates correctly,
none requires approval (all READ_ONLY), and all degrade gracefully with a
clear message when Stripe isn't configured - never attempting a network
call or crashing. StripeConnector itself is mocked; no real HTTP access."""

from __future__ import annotations

from unittest.mock import patch

from jarvis.integrations.base import CredentialError, ExternalActionResult
from jarvis.tools.stripe_tools import (
    GetChargeStatusTool,
    GetStripeBalanceTool,
    ListRecentChargesTool,
    ListRecentPaymentIntentsTool,
)


# --- not configured: clear message, no network attempt -----------------------------


def test_get_balance_not_configured_returns_clear_message():
    with patch("jarvis.tools.stripe_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = GetStripeBalanceTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()
    mock_connector.execute.assert_not_called()


def test_list_recent_charges_not_configured():
    with patch("jarvis.tools.stripe_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ListRecentChargesTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_get_charge_status_not_configured():
    with patch("jarvis.tools.stripe_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = GetChargeStatusTool().run(charge_id="ch_1")
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_list_recent_payment_intents_not_configured():
    with patch("jarvis.tools.stripe_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ListRecentPaymentIntentsTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()


# --- successful delegation to the connector -----------------------------------------


def test_get_balance_delegates_to_connector():
    with patch("jarvis.tools.stripe_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="available: 100.00 USD", service="stripe", action="get_balance"
        )
        result = GetStripeBalanceTool().run()
    assert result.ok is True
    assert result.output == "available: 100.00 USD"
    mock_connector.execute.assert_called_once_with("get_balance")


def test_list_recent_charges_delegates_with_limit():
    with patch("jarvis.tools.stripe_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="stripe", action="list_recent_charges"
        )
        ListRecentChargesTool().run(limit=5)
    mock_connector.execute.assert_called_once_with("list_recent_charges", limit=5)


def test_list_recent_charges_defaults_to_ten():
    with patch("jarvis.tools.stripe_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="stripe", action="list_recent_charges"
        )
        ListRecentChargesTool().run()
    mock_connector.execute.assert_called_once_with("list_recent_charges", limit=10)


def test_get_charge_status_delegates_with_charge_id():
    with patch("jarvis.tools.stripe_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="status=succeeded", service="stripe", action="get_charge_status"
        )
        result = GetChargeStatusTool().run(charge_id="ch_123")
    assert result.ok is True
    assert result.output == "status=succeeded"
    mock_connector.execute.assert_called_once_with("get_charge_status", charge_id="ch_123")


def test_list_recent_payment_intents_delegates_with_limit():
    with patch("jarvis.tools.stripe_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="stripe", action="list_recent_payment_intents"
        )
        ListRecentPaymentIntentsTool().run(limit=3)
    mock_connector.execute.assert_called_once_with("list_recent_payment_intents", limit=3)


def test_list_recent_payment_intents_defaults_to_ten():
    with patch("jarvis.tools.stripe_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="stripe", action="list_recent_payment_intents"
        )
        ListRecentPaymentIntentsTool().run()
    mock_connector.execute.assert_called_once_with("list_recent_payment_intents", limit=10)


# --- CredentialError raised by execute() is caught, not propagated -----------------


def test_credential_error_from_execute_is_caught_gracefully():
    with patch("jarvis.tools.stripe_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.side_effect = CredentialError("invalid key")
        result = GetStripeBalanceTool().run()
    assert result.ok is False
    assert "invalid key" in result.output


# --- no approval required for any of the four (all READ_ONLY) -----------------------


def test_none_of_the_four_tools_calls_confirm_side_effect():
    with patch("jarvis.tools.stripe_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="ok", service="stripe", action="x"
        )
        with patch("builtins.input") as mock_input:
            GetStripeBalanceTool().run()
            ListRecentChargesTool().run()
            GetChargeStatusTool().run(charge_id="ch_1")
            ListRecentPaymentIntentsTool().run()
    mock_input.assert_not_called()


# --- tool metadata -----------------------------------------------------------------------


def test_all_four_tools_have_distinct_names():
    names = {
        GetStripeBalanceTool().name,
        ListRecentChargesTool().name,
        GetChargeStatusTool().name,
        ListRecentPaymentIntentsTool().name,
    }
    assert names == {
        "get_stripe_balance", "list_recent_charges",
        "get_charge_status", "list_recent_payment_intents",
    }


def test_get_charge_status_requires_charge_id_in_schema():
    schema = GetChargeStatusTool().input_schema
    assert schema["required"] == ["charge_id"]


def test_other_three_tools_have_no_required_fields():
    for tool in (GetStripeBalanceTool(), ListRecentChargesTool(), ListRecentPaymentIntentsTool()):
        assert "required" not in tool.input_schema or tool.input_schema.get("required") == []
