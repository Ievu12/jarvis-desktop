"""Shared interface every external-system connector implements
(Instagram, Facebook, email, Stripe, ...). No real connector exists yet -
this module only defines the shape a future one must have.

Mirrors jarvis.tools.shell's AllowedCommand pattern deliberately: a
connector exposes a fixed, finite list of named ExternalAction entries
(never "make an arbitrary API call"), each with its own risk level that
determines how strict its approval prompt is. A connector's execute()
performs the real network call, but only ever after the caller has
already gone through jarvis.integrations.approval.confirm_external_action
- execute() itself has no approval logic and must never be called
without it, the same discipline jarvis.tools.fs's write_file/etc. already
follow for confirm_side_effect().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class RiskLevel(Enum):
    """How consequential an ExternalAction is, and therefore how strict
    its approval prompt must be (see jarvis.integrations.approval):

    READ_ONLY - no external state changes (e.g. reading recent posts,
        checking a balance). No approval required, mirroring read-only
        local tools (read_file, get_workflow_state).
    REVERSIBLE_WRITE - changes external state, but the change can
        realistically be undone (e.g. deleting a draft, unpublishing a
        post shortly after). Ordinary y/N approval, like write_file.
    IRREVERSIBLE_WRITE - changes external state in a way that cannot be
        undone, or where undoing it has real-world consequences (e.g.
        sending an email, charging a card, publishing a public post).
        Requires the stricter confirm_external_action() flow: typing the
        service name, not just 'y' - deliberately higher friction than
        any existing local approval prompt.
    """

    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    IRREVERSIBLE_WRITE = "irreversible_write"


@dataclass(frozen=True)
class ExternalAction:
    """One named, fixed action a connector can perform - the external
    equivalent of jarvis.tools.shell.AllowedCommand. A connector's action
    list is closed: the model can only ever request one of these by name,
    never an arbitrary API call shape."""

    name: str
    description: str
    risk_level: RiskLevel
    input_schema: dict[str, Any]


@dataclass
class ExternalActionResult:
    """Outcome of a Connector.execute() call. Deliberately mirrors
    jarvis.tools.base.ToolResult's (ok, output) shape so callers/tests can
    reason about both the same way, but kept as its own type since
    external calls carry additional context (which service/action ran)
    that a local ToolResult has no field for."""

    ok: bool
    output: str
    service: str
    action: str


class CredentialError(Exception):
    """Raised by Connector.execute() when required credentials are
    missing or invalid. Never raised for a routine 'not configured yet'
    check - see Connector.is_configured() for that - only when execute()
    is called despite is_configured() being false, or when the service
    itself rejects the credentials at call time (e.g. an expired OAuth
    token)."""


class Connector(ABC):
    """One connection to one external service. A connector never performs
    an action on its own initiative and never bypasses approval - it is
    invoked only after jarvis.integrations.approval.confirm_external_action
    has already returned True for the specific action being requested.

    No real subclass exists yet in this codebase; this class exists so
    that when one is added (email first, per the integrations roadmap),
    it has an interface to implement rather than an ad hoc shape."""

    service_name: str
    actions: list[ExternalAction]

    @abstractmethod
    def is_configured(self) -> bool:
        """Whether this connector has the credentials it needs to
        operate (see jarvis.integrations.credentials). Never raises -
        used to decide whether to offer this connector's actions at all,
        before any approval prompt or network call."""

    def get_action(self, name: str) -> ExternalAction | None:
        return next((a for a in self.actions if a.name == name), None)

    @abstractmethod
    def execute(self, action_name: str, **kwargs: Any) -> ExternalActionResult:
        """Perform the named action for real. Callers MUST have already
        obtained approval via confirm_external_action() for this exact
        action before calling this - execute() itself does not prompt.
        Raises CredentialError if required credentials are missing or
        rejected by the service; other failures are reported via
        ExternalActionResult(ok=False, ...), not exceptions, mirroring
        Tool.run()'s error-reporting convention."""

    def disconnect(self) -> bool:
        """Remove whatever locally-held credential state this connector
        manages on its own (e.g. stored OAuth tokens) - a "forget this
        account" operation, used by jarvis.integrations.manager
        .IntegrationsManager.disconnect(). Never revokes the credential
        with the external service itself (no network call) - only clears
        what JARVIS holds locally, so the user's own account access is
        unaffected either way.

        Default implementation: returns False (not supported) and does
        nothing - correct for a connector whose only credential is a
        plain environment variable (nothing local to disconnect, since
        env vars are the user's shell state, not something JARVIS could
        or should clear). A connector with its own locally-managed
        secret (e.g. EmailConnector's OAuth tokens via
        jarvis.integrations.oauth.TokenStore) overrides this to actually
        clear it. Optional and non-abstract deliberately, so adding this
        capability to Connector does not break any existing or future
        connector that doesn't need it.
        """
        return False
