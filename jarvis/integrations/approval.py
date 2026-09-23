"""Approval prompts for external-service actions - stricter than
jarvis.core.approval.confirm_side_effect, because an external action can
have real, irreversible consequences outside the project (a sent email,
a public post, a charge) where a local file write or git commit does
not. Logged as its own audit event type (external_action_confirmation),
distinct from side_effect_confirmation, so external approvals are
identifiable in jarvis history on their own.
"""

from __future__ import annotations

from jarvis.core.audit import log_event
from jarvis.integrations.base import RiskLevel


def confirm_external_action(
    service: str,
    action: str,
    risk_level: RiskLevel,
    description: str,
) -> bool:
    """Ask the user to approve one specific external action.

    READ_ONLY actions should never reach this function at all (same
    convention as local read-only tools skipping confirm_side_effect) -
    if they do, this still requires the same plain y/N as
    REVERSIBLE_WRITE, since there is no lower tier below it here.

    REVERSIBLE_WRITE uses a plain y/N prompt, same shape as
    confirm_side_effect().

    IRREVERSIBLE_WRITE requires typing the service name back exactly
    (case-insensitive) rather than 'y' - deliberately higher friction
    than any existing local approval prompt, since these actions cannot
    be undone by JARVIS itself (jarvis.tools' equivalent, a local file
    write, can always be overwritten again; an external action like a
    sent email or a charge cannot).

    Always logs an external_action_confirmation audit event with the
    outcome, mirroring confirm_side_effect()'s side_effect_confirmation
    logging - kept as a distinct event type so external approvals can be
    seen separately in jarvis history from local ones.

    A KeyboardInterrupt during the prompt propagates rather than being
    treated as approval or denial - same rationale and same 'log as
    denied+interrupted, then re-raise' handling as confirm_side_effect().
    """
    header = f"[JARVIS] About to perform an EXTERNAL action on {service}: {action}"
    detail = f"\n{description}" if description else ""

    if risk_level == RiskLevel.IRREVERSIBLE_WRITE:
        prompt = (
            f"\n{header}{detail}\n"
            f"This cannot be undone by JARVIS. Type '{service}' (without quotes) "
            f"to confirm, or anything else to cancel: "
        )
    else:
        prompt = f"\n{header}{detail}\nProceed? [y/N] "

    try:
        answer = input(prompt).strip()
    except KeyboardInterrupt:
        print("\n[JARVIS] Interrupted - treating as denied.")
        log_event(
            "external_action_confirmation",
            service=service,
            action=action,
            risk_level=risk_level.value,
            description=description,
            approved=False,
            interrupted=True,
        )
        raise

    if risk_level == RiskLevel.IRREVERSIBLE_WRITE:
        approved = answer.lower() == service.lower()
    else:
        approved = answer.lower() == "y"

    log_event(
        "external_action_confirmation",
        service=service,
        action=action,
        risk_level=risk_level.value,
        description=description,
        approved=approved,
    )
    return approved
