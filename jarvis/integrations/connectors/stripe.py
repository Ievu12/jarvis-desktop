"""Stripe connector (REST API, read-only v1): the second real
implementation of jarvis.integrations.base.Connector, alongside
jarvis.integrations.connectors.email.EmailConnector. Authenticates with a
single API key (Stripe's own "secret key" or, preferably, a
read-only-scoped restricted key) via HTTP Basic auth, exactly as Stripe's
own API documentation specifies - the key is sent as the basic-auth
username with an empty password, over HTTPS.

Every action here is RiskLevel.READ_ONLY: checking the account balance,
listing recent charges, checking one charge's status, and listing recent
payment intents ("orders" - Stripe has no single unified "order" object;
PaymentIntent is the standard object representing one payment attempt's
lifecycle, amount, and status). No write action (create a charge, issue a
refund, create a payout, update a customer, capture/cancel a payment
intent, ...) exists anywhere in this module. Adding one later means
adding a new ExternalAction with RiskLevel.REVERSIBLE_WRITE or
IRREVERSIBLE_WRITE and going through jarvis.integrations.approval
.confirm_external_action() - it cannot happen by accident, since there is
currently no code path here that could perform one, and this module never
imports or calls anything from Stripe's API beyond the four GET endpoints
below.

The API key is read only from the environment (STRIPE_API_KEY, via
jarvis.integrations.credentials - the same model jarvis.config's
ANTHROPIC_API_KEY and EmailConnector's EMAIL_PASSWORD already use) and is
never logged, printed, or included in any ExternalActionResult/error
message - see _redact_stripe_errors() below, which scrubs the configured
key out of any exception text before it can reach a result or a raised
CredentialError, the same defense-in-depth jarvis.core.secrets
.redact_secret() applies to the Anthropic key.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from base64 import b64encode
from typing import Any

from jarvis.integrations.base import (
    Connector,
    CredentialError,
    ExternalAction,
    ExternalActionResult,
    RiskLevel,
)
from jarvis.integrations.credentials import get_credential, register_connector_env_vars

SERVICE_NAME = "stripe"

_ENV_API_KEY = "STRIPE_API_KEY"

register_connector_env_vars(SERVICE_NAME, [_ENV_API_KEY])

_API_BASE_URL = "https://api.stripe.com/v1"
_REQUEST_TIMEOUT_SECONDS = 15
_DEFAULT_CHARGE_LIMIT = 10
_MAX_CHARGE_LIMIT = 50
_DEFAULT_PAYMENT_INTENT_LIMIT = 10
_MAX_PAYMENT_INTENT_LIMIT = 50


def _redact_key(text: str, api_key: str | None) -> str:
    """Scrub a known Stripe API key value out of arbitrary text (e.g. an
    exception message from urllib) before it can reach a result or log
    line. Mirrors jarvis.core.secrets.redact_secret()'s approach, applied
    to this connector's own credential rather than ANTHROPIC_API_KEY."""
    if not api_key:
        return text
    return text.replace(api_key, "****")


class StripeConnector(Connector):
    service_name = SERVICE_NAME

    def __init__(self) -> None:
        self.actions = [
            ExternalAction(
                name="get_balance",
                description=(
                    "Get the current Stripe account balance (available and "
                    "pending amounts, by currency). Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={"type": "object", "properties": {}},
            ),
            ExternalAction(
                name="list_recent_charges",
                description=(
                    "List recent charges (id, amount, currency, status, "
                    "created timestamp) - most recent first. Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": f"Max charges to list (default {_DEFAULT_CHARGE_LIMIT}, max {_MAX_CHARGE_LIMIT}).",
                        }
                    },
                },
            ),
            ExternalAction(
                name="get_charge_status",
                description=(
                    "Get one specific charge's status and details (id, amount, "
                    "currency, status, created timestamp) by its charge id - "
                    "e.g. to check whether a specific payment succeeded. "
                    "Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "charge_id": {
                            "type": "string",
                            "description": "The Stripe charge id to look up (e.g. 'ch_...').",
                        }
                    },
                    "required": ["charge_id"],
                },
            ),
            ExternalAction(
                name="list_recent_payment_intents",
                description=(
                    "List recent payment intents ('orders' - Stripe's object "
                    "representing one payment attempt's lifecycle: id, amount, "
                    "currency, status, created timestamp) - most recent first. "
                    "Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": (
                                f"Max payment intents to list (default "
                                f"{_DEFAULT_PAYMENT_INTENT_LIMIT}, max {_MAX_PAYMENT_INTENT_LIMIT})."
                            ),
                        }
                    },
                },
            ),
        ]

    def is_configured(self) -> bool:
        return bool(get_credential(SERVICE_NAME, _ENV_API_KEY))

    def _api_key(self) -> str:
        api_key = get_credential(SERVICE_NAME, _ENV_API_KEY)
        if not api_key:
            raise CredentialError(
                f"'{SERVICE_NAME}' is not configured - set {_ENV_API_KEY} in the environment."
            )
        return api_key

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Perform a single, read-only GET request against the Stripe API.
        Never called for anything but the two actions below - there is no
        generic "make any Stripe request" entry point, deliberately
        mirroring jarvis.tools.shell's fixed-allowlist approach rather than
        exposing an arbitrary-path HTTP client.
        """
        api_key = self._api_key()

        url = f"{_API_BASE_URL}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"

        # Stripe's documented authentication scheme: HTTP Basic auth with
        # the API key as the username and an empty password. Built by
        # hand (rather than urllib's own basic-auth helper) so the key
        # never touches a second layer of library code unnecessarily.
        auth_header = b64encode(f"{api_key}:".encode()).decode()
        request = urllib.request.Request(
            url, headers={"Authorization": f"Basic {auth_header}"}, method="GET"
        )

        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise CredentialError(
                _redact_key(f"Stripe API request failed ({e.code}): {error_body}", api_key)
            ) from None
        except urllib.error.URLError as e:
            raise CredentialError(_redact_key(f"Stripe API connection error: {e.reason}", api_key)) from None
        except TimeoutError:
            raise CredentialError(
                f"Stripe API request timed out after {_REQUEST_TIMEOUT_SECONDS}s."
            ) from None

        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise CredentialError(_redact_key(f"Stripe API returned invalid JSON: {e}", api_key)) from None

    def execute(self, action_name: str, **kwargs: Any) -> ExternalActionResult:
        action = self.get_action(action_name)
        if action is None:
            return ExternalActionResult(
                ok=False,
                output=f"Unknown action: {action_name}",
                service=self.service_name,
                action=action_name,
            )

        handler = getattr(self, f"_do_{action_name}")
        try:
            return handler(**kwargs)
        except CredentialError as e:
            return ExternalActionResult(
                ok=False, output=str(e), service=self.service_name, action=action_name
            )

    def _do_get_balance(self) -> ExternalActionResult:
        data = self._get("/balance")

        lines = []
        for bucket_name in ("available", "pending"):
            for entry in data.get(bucket_name, []):
                amount = entry.get("amount", 0)
                currency = str(entry.get("currency", "")).upper()
                # Stripe amounts are in the smallest currency unit (e.g.
                # cents) - divided by 100 for a human-readable major-unit
                # display, matching Stripe's own dashboard convention.
                lines.append(f"{bucket_name}: {amount / 100:.2f} {currency}")

        output = "\n".join(lines) if lines else "No balance data returned."
        return ExternalActionResult(
            ok=True, output=output, service=self.service_name, action="get_balance"
        )

    def _do_list_recent_charges(self, limit: int = _DEFAULT_CHARGE_LIMIT) -> ExternalActionResult:
        limit = max(1, min(limit, _MAX_CHARGE_LIMIT))
        data = self._get("/charges", params={"limit": limit})

        charges = data.get("data", [])
        if not charges:
            return ExternalActionResult(
                ok=True, output="No charges found.", service=self.service_name, action="list_recent_charges"
            )

        lines = []
        for charge in charges:
            charge_id = charge.get("id", "?")
            amount = charge.get("amount", 0)
            currency = str(charge.get("currency", "")).upper()
            status = charge.get("status", "?")
            created = charge.get("created", "?")
            lines.append(
                f"id={charge_id} | amount={amount / 100:.2f} {currency} | "
                f"status={status} | created={created}"
            )

        return ExternalActionResult(
            ok=True, output="\n".join(lines), service=self.service_name, action="list_recent_charges"
        )

    def _do_get_charge_status(self, charge_id: str) -> ExternalActionResult:
        if not charge_id or not charge_id.strip():
            return ExternalActionResult(
                ok=False,
                output="A charge_id is required.",
                service=self.service_name,
                action="get_charge_status",
            )

        data = self._get(f"/charges/{charge_id.strip()}")

        amount = data.get("amount", 0)
        currency = str(data.get("currency", "")).upper()
        status = data.get("status", "?")
        created = data.get("created", "?")
        output = (
            f"id={data.get('id', charge_id)} | amount={amount / 100:.2f} {currency} | "
            f"status={status} | created={created}"
        )
        return ExternalActionResult(
            ok=True, output=output, service=self.service_name, action="get_charge_status"
        )

    def _do_list_recent_payment_intents(
        self, limit: int = _DEFAULT_PAYMENT_INTENT_LIMIT
    ) -> ExternalActionResult:
        limit = max(1, min(limit, _MAX_PAYMENT_INTENT_LIMIT))
        data = self._get("/payment_intents", params={"limit": limit})

        payment_intents = data.get("data", [])
        if not payment_intents:
            return ExternalActionResult(
                ok=True,
                output="No payment intents found.",
                service=self.service_name,
                action="list_recent_payment_intents",
            )

        lines = []
        for pi in payment_intents:
            pi_id = pi.get("id", "?")
            amount = pi.get("amount", 0)
            currency = str(pi.get("currency", "")).upper()
            status = pi.get("status", "?")
            created = pi.get("created", "?")
            lines.append(
                f"id={pi_id} | amount={amount / 100:.2f} {currency} | "
                f"status={status} | created={created}"
            )

        return ExternalActionResult(
            ok=True,
            output="\n".join(lines),
            service=self.service_name,
            action="list_recent_payment_intents",
        )
