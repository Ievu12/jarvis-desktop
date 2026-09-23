"""Agent-facing tool wrappers around
jarvis.integrations.connectors.stripe.StripeConnector: the bridge between
the LLM-driven agent and the Stripe connector, exactly the shape
jarvis.tools.email_tools uses to bridge the agent to EmailConnector. No
business logic lives here - each tool's run() only delegates to the
connector.

All four tools wrap READ_ONLY actions (account balance, recent charges,
one charge's status, recent payment intents), so none of them requires
confirm_side_effect() or confirm_external_action() - there is nothing to
approve when nothing external is being changed, mirroring how the email
tools and read_file/get_workflow_state skip approval today. There is
currently no write action (create a charge, issue a refund, create a
payout, ...) to wrap, so no approval-gated Stripe tool exists yet - see
StripeConnector's own module docstring for what adding one would require.

If Stripe isn't configured (no STRIPE_API_KEY in the environment), every
tool here returns a clear, non-crashing ToolResult(ok=False, ...) instead
of attempting a network call - the same "fail closed with a clear
message" pattern used throughout jarvis.tools.
"""

from __future__ import annotations

from jarvis.integrations.base import CredentialError
from jarvis.integrations.connectors.stripe import StripeConnector
from jarvis.tools.base import Tool, ToolResult

_connector = StripeConnector()


def _run_stripe_action(action_name: str, **kwargs) -> ToolResult:
    if not _connector.is_configured():
        return ToolResult(
            ok=False,
            output=(
                "Stripe is not configured. Set STRIPE_API_KEY in the "
                "environment (a read-only-scoped restricted key is "
                "recommended) to enable Stripe tools."
            ),
        )
    try:
        result = _connector.execute(action_name, **kwargs)
    except CredentialError as e:
        return ToolResult(ok=False, output=str(e))
    return ToolResult(ok=result.ok, output=result.output)


class GetStripeBalanceTool(Tool):
    name = "get_stripe_balance"
    description = (
        "Get the current Stripe account balance (available and pending "
        "amounts, by currency). Read-only, no approval required."
    )
    input_schema = {"type": "object", "properties": {}}

    def run(self) -> ToolResult:
        return _run_stripe_action("get_balance")


class ListRecentChargesTool(Tool):
    name = "list_recent_charges"
    description = (
        "List recent Stripe charges (id, amount, currency, status, "
        "created timestamp) - most recent first. Use this to answer "
        "'what were the recent payments' or 'what is the amount/status of "
        "recent payments'. Read-only, no approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max charges to list (default 10, max 50).",
            }
        },
    }

    def run(self, *, limit: int = 10) -> ToolResult:
        return _run_stripe_action("list_recent_charges", limit=limit)


class GetChargeStatusTool(Tool):
    name = "get_charge_status"
    description = (
        "Get one specific Stripe charge's status and details by its "
        "charge id (as returned by list_recent_charges). Use this to "
        "answer 'was this specific payment successful'. Read-only, no "
        "approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "charge_id": {
                "type": "string",
                "description": "The Stripe charge id to look up (e.g. 'ch_...').",
            }
        },
        "required": ["charge_id"],
    }

    def run(self, *, charge_id: str) -> ToolResult:
        return _run_stripe_action("get_charge_status", charge_id=charge_id)


class ListRecentPaymentIntentsTool(Tool):
    name = "list_recent_payment_intents"
    description = (
        "List recent Stripe payment intents ('orders' - id, amount, "
        "currency, status, created timestamp) - most recent first. Use "
        "this to answer 'what were the recent orders'. Read-only, no "
        "approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max payment intents to list (default 10, max 50).",
            }
        },
    }

    def run(self, *, limit: int = 10) -> ToolResult:
        return _run_stripe_action("list_recent_payment_intents", limit=limit)
