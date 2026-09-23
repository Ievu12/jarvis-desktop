"""Integration-style tests confirming the data returned by the existing,
unmodified Stripe tools (jarvis.tools.stripe_tools) is sufficient for the
LLM-driven agent to answer the six Lithuanian natural-language questions
this feature targets:

  1. "Ar šiandien buvo pirkimų?"                       (were there any purchases today)
  2. "Kiek šiandien gavau per Stripe?"                  (how much did I receive via Stripe today)
  3. "Parodyk šiandienos Stripe mokėjimus."             (show today's Stripe payments)
  4. "Kiek mokėjimų gavau šiandien?"                    (how many payments did I receive today)
  5. "Koks paskutinis Stripe mokėjimas?"                (what was the last Stripe payment)
  6. "Kokia buvo paskutinio mokėjimo suma ir būsena?"   (what was the last payment's amount/status)

No new tool, connector method, or business logic is added or exercised
here beyond what jarvis.tools.stripe_tools/jarvis.integrations.connectors
.stripe already provide - this module only confirms the *data shape*
(created/amount/currency/status always present and machine-parseable)
that lets the LLM answer these questions by reasoning over already-
returned tool output, exactly as jarvis.core.llm.BASE_SYSTEM_PROMPT's
Stripe section instructs it to. No real network access - urllib.request
.urlopen is mocked throughout, and STRIPE_API_KEY is never a real key
nor ever asserted/printed."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.stripe_tools import (
    GetChargeStatusTool,
    GetStripeBalanceTool,
    ListRecentChargesTool,
    ListRecentPaymentIntentsTool,
)


@pytest.fixture
def configured_env(monkeypatch):
    # Fake, test-shaped key only - never a real secret, never asserted or
    # printed anywhere in these tests.
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake_key_never_used_for_real_requests")


def _mock_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm


def _today_ts() -> int:
    return int(time.time())


def _yesterday_ts() -> int:
    return int(time.time()) - 90_000  # well over 24h ago


# --- 1/3/4: "were there purchases today" / "show today's payments" / "how many today" -----------


def test_list_recent_charges_data_supports_filtering_by_today(configured_env):
    payload = {
        "data": [
            {"id": "ch_today_1", "amount": 1000, "currency": "usd", "status": "succeeded", "created": _today_ts()},
            {"id": "ch_today_2", "amount": 2000, "currency": "usd", "status": "succeeded", "created": _today_ts()},
            {"id": "ch_yesterday", "amount": 500, "currency": "usd", "status": "succeeded", "created": _yesterday_ts()},
        ]
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = ListRecentChargesTool().run(limit=10)

    assert result.ok is True
    # Every returned line carries a created timestamp the model can compare
    # against "today" itself - the tool does not pre-filter by date.
    assert "ch_today_1" in result.output
    assert "ch_today_2" in result.output
    assert "ch_yesterday" in result.output
    for line in result.output.splitlines():
        assert "created=" in line
        assert "amount=" in line
        assert "status=" in line


def test_list_recent_charges_no_purchases_case_is_unambiguous(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})):
        result = ListRecentChargesTool().run()
    assert result.ok is True
    assert "no charges found" in result.output.lower()


# --- 2: "how much did I receive today" - amounts are present and summable -----------------------


def test_list_recent_charges_amounts_are_summable_major_units(configured_env):
    payload = {
        "data": [
            {"id": "ch_1", "amount": 1500, "currency": "usd", "status": "succeeded", "created": _today_ts()},
            {"id": "ch_2", "amount": 2500, "currency": "usd", "status": "succeeded", "created": _today_ts()},
        ]
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = ListRecentChargesTool().run()

    assert "15.00 USD" in result.output
    assert "25.00 USD" in result.output


def test_list_recent_payment_intents_data_supports_filtering_by_today(configured_env):
    payload = {
        "data": [
            {"id": "pi_1", "amount": 5000, "currency": "usd", "status": "succeeded", "created": _today_ts()},
            {"id": "pi_2", "amount": 3000, "currency": "usd", "status": "succeeded", "created": _yesterday_ts()},
        ]
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = ListRecentPaymentIntentsTool().run()

    assert result.ok is True
    for line in result.output.splitlines():
        assert "created=" in line
        assert "amount=" in line


# --- 5: "what was the last Stripe payment" - most-recent-first ordering assumed from Stripe -------


def test_list_recent_charges_returns_most_recent_first_as_provided_by_stripe(configured_env):
    # Stripe's API itself returns charges most-recent-first; this tool
    # passes that data through unmodified/unsorted (no re-sorting logic to
    # introduce or duplicate) - confirms the first line is whichever charge
    # Stripe's response lists first.
    payload = {
        "data": [
            {"id": "ch_latest", "amount": 999, "currency": "usd", "status": "succeeded", "created": _today_ts()},
            {"id": "ch_older", "amount": 111, "currency": "usd", "status": "succeeded", "created": _yesterday_ts()},
        ]
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = ListRecentChargesTool().run()

    lines = result.output.splitlines()
    assert "ch_latest" in lines[0]
    assert "ch_older" in lines[1]


def test_list_recent_charges_limit_one_isolates_the_last_payment(configured_env):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})) as mock_urlopen:
        ListRecentChargesTool().run(limit=1)
    request = mock_urlopen.call_args[0][0]
    assert "limit=1" in request.full_url


# --- 6: "what was the last payment's amount and status" - get_charge_status by id ----------------


def test_get_charge_status_returns_amount_and_status_for_a_specific_charge(configured_env):
    payload = {"id": "ch_last", "amount": 4200, "currency": "eur", "status": "succeeded", "created": _today_ts()}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = GetChargeStatusTool().run(charge_id="ch_last")

    assert result.ok is True
    assert "42.00 EUR" in result.output
    assert "succeeded" in result.output


def test_get_charge_status_reports_failed_status_plainly(configured_env):
    payload = {"id": "ch_failed", "amount": 100, "currency": "usd", "status": "failed", "created": _today_ts()}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = GetChargeStatusTool().run(charge_id="ch_failed")

    assert result.ok is True
    assert "failed" in result.output


# --- balance question stays separate from payment-listing questions -----------------------------


def test_get_stripe_balance_is_a_distinct_tool_from_charge_listing(configured_env):
    payload = {"available": [{"amount": 10000, "currency": "usd"}], "pending": []}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = GetStripeBalanceTool().run()

    assert result.ok is True
    assert "100.00 USD" in result.output
    # Balance output never includes per-charge fields - a distinct question
    # shape ("what's my balance") from "what were recent payments".
    assert "created=" not in result.output


# --- credential safety: the fake key never leaks through any of these paths ----------------------


def test_none_of_the_lithuanian_query_paths_ever_leak_the_configured_key(configured_env, monkeypatch):
    fake_key = "sk_test_fake_key_never_used_for_real_requests"
    payload = {"data": [{"id": "ch_1", "amount": 100, "currency": "usd", "status": "succeeded", "created": _today_ts()}]}

    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        results = [
            ListRecentChargesTool().run(),
            ListRecentPaymentIntentsTool().run(),
        ]
    with patch("urllib.request.urlopen", return_value=_mock_response({"id": "ch_1", "amount": 100, "currency": "usd", "status": "succeeded", "created": _today_ts()})):
        results.append(GetChargeStatusTool().run(charge_id="ch_1"))
    with patch("urllib.request.urlopen", return_value=_mock_response({"available": [], "pending": []})):
        results.append(GetStripeBalanceTool().run())

    for result in results:
        assert fake_key not in result.output


# --- no approval prompt for any of these read-only queries -----------------------------------------


def test_none_of_the_six_lithuanian_questions_trigger_an_approval_prompt(configured_env):
    payload = {"data": [{"id": "ch_1", "amount": 100, "currency": "usd", "status": "succeeded", "created": _today_ts()}]}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        with patch("builtins.input") as mock_input:
            ListRecentChargesTool().run()
            ListRecentPaymentIntentsTool().run()
            GetStripeBalanceTool().run()
    mock_input.assert_not_called()
