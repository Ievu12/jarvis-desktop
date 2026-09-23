"""Integrations Manager: the single place JARVIS (and, later, the user
through a CLI/REPL surface) looks to see every integration's status,
check whether one is configured, and connect/disconnect it - without
needing to import or know about each connector module individually.

Builds directly on jarvis.integrations.registry.IntegrationRegistry
(which already tracks which Connector instances exist and answers
"which are configured") and jarvis.integrations.base.Connector
(is_configured()/disconnect()). This module adds the missing pieces:
a four-state status model (NOT_CONFIGURED/CONNECTED/DISCONNECTED/ERROR),
and connect()/disconnect() as manager-level operations rather than only
per-connector ones.

No real connector is actually connected by this module on its own
initiative anywhere - connect() only reports what a human-driven "connect
my account" step would still need to do (e.g. visiting an OAuth
authorization URL) for connectors that support OAuth, and otherwise
simply re-checks is_configured(). It never performs an OAuth token
exchange (jarvis.integrations.oauth.exchange_code_for_tokens() is itself
deliberately unimplemented) and never reads or displays a credential's
raw value - RiskLevel/ExternalAction/confirm_external_action's approval
model for actually USING a connected integration is entirely unchanged;
this module only manages the connected/not-connected state, not action
approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from jarvis.integrations.base import Connector
from jarvis.integrations.credentials import credential_status, format_credential_status
from jarvis.integrations.registry import IntegrationRegistry


class IntegrationStatus(Enum):
    """Four-state model for one integration's connection state:

    NOT_CONFIGURED - no credentials of any kind are present (no env vars
        set, no OAuth tokens stored). Nothing to do but configure it.
    CONNECTED - is_configured() is True: credentials (password or OAuth
        tokens) are present and this connector's actions can be used.
        This does not mean the credentials are still valid with the
        remote service - only that JARVIS has something to try them
        with. A rejected credential surfaces as ERROR the next time an
        action actually runs (jarvis.integrations.base.CredentialError),
        not preemptively here.
    DISCONNECTED - credentials were present at some point and have since
        been explicitly cleared via disconnect() (or manually removed).
        Distinguished from NOT_CONFIGURED so "the user deliberately
        disconnected this" reads differently from "this was never set
        up" in a status listing.
    ERROR - checking configuration itself failed unexpectedly (e.g.
        is_configured() raised). Distinct from a connected-but-invalid
        credential, which is CONNECTED here and only surfaces as an
        error when an action is actually attempted.
    """

    NOT_CONFIGURED = "not_configured"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass(frozen=True)
class IntegrationStatusInfo:
    """One connector's current status, safe to print/log in full - never
    contains a credential value, only the service name, status, and a
    short human-readable detail string (itself built from
    jarvis.integrations.credentials.format_credential_status(), which is
    already masked)."""

    service_name: str
    status: IntegrationStatus
    detail: str


class IntegrationsManager:
    """Wraps an IntegrationRegistry with status reporting and
    connect/disconnect operations. One manager instance tracks its own
    in-memory record of which services have been explicitly disconnected
    this process, so DISCONNECTED can be distinguished from
    NOT_CONFIGURED - this is deliberately not persisted anywhere; a new
    process (or a fresh IntegrationsManager) sees a previously
    disconnected-but-still-unconfigured service as NOT_CONFIGURED again,
    since there is no meaningful difference once nothing is stored.
    """

    def __init__(self, registry: IntegrationRegistry) -> None:
        self._registry = registry
        self._explicitly_disconnected: set[str] = set()

    def get_status(self, service_name: str) -> IntegrationStatusInfo:
        """Status for one registered service. Returns an ERROR status
        (never raises) if the service isn't registered at all, or if
        checking its configuration raises unexpectedly."""
        connector = self._registry.get(service_name)
        if connector is None:
            return IntegrationStatusInfo(
                service_name=service_name,
                status=IntegrationStatus.ERROR,
                detail=f"'{service_name}' is not a registered integration.",
            )
        return self._status_for_connector(connector)

    def _status_for_connector(self, connector: Connector) -> IntegrationStatusInfo:
        service_name = connector.service_name
        try:
            configured = connector.is_configured()
        except Exception as e:
            return IntegrationStatusInfo(
                service_name=service_name,
                status=IntegrationStatus.ERROR,
                detail=f"Could not check configuration: {e}",
            )

        env_detail = format_credential_status(credential_status(service_name))

        if configured:
            # Being configured again after an explicit disconnect (e.g.
            # the password env vars were re-set, or the account was
            # reconnected) supersedes the disconnected marker - CONNECTED
            # always wins over a stale DISCONNECTED record.
            self._explicitly_disconnected.discard(service_name)
            # is_configured() can be True via a credential this manager
            # doesn't know the shape of (e.g. GmailConnector/
            # GoogleCalendarConnector's OAuth tokens, or EmailConnector's
            # OAuth tokens, all held entirely outside CONNECTOR_ENV_VARS)
            # even when no env var is registered for this service at all.
            # credential_status(service_name).is_configured is misleading
            # here on its own: an OAuth-only connector has an EMPTY
            # required_vars list, and CredentialStatus.is_configured
            # ("nothing is missing from an empty requirement") is True for
            # that regardless of whether OAuth tokens are actually stored
            # - which previously caused env_detail's "no credentials
            # registered" placeholder text to be shown even for a
            # genuinely connected OAuth-only service. Only trust
            # env_detail when this service actually HAS registered env
            # vars (a real password/API-key credential shape) AND they're
            # fully present; otherwise this connector's CONNECTED status
            # came from something env_detail can't see, so use a generic,
            # still-non-leaking confirmation instead.
            status_for_env_vars = credential_status(service_name)
            detail = env_detail if (
                status_for_env_vars.required_vars and status_for_env_vars.is_configured
            ) else (
                f"{service_name}: configured via a non-environment-variable credential "
                "(e.g. stored OAuth tokens)."
            )
            return IntegrationStatusInfo(
                service_name=service_name, status=IntegrationStatus.CONNECTED, detail=detail
            )

        if service_name in self._explicitly_disconnected:
            return IntegrationStatusInfo(
                service_name=service_name, status=IntegrationStatus.DISCONNECTED, detail=env_detail
            )

        return IntegrationStatusInfo(
            service_name=service_name, status=IntegrationStatus.NOT_CONFIGURED, detail=env_detail
        )

    def list_statuses(self) -> list[IntegrationStatusInfo]:
        """Status for every registered connector, in registration order."""
        return [self._status_for_connector(c) for c in self._registry.all()]

    def is_configured(self, service_name: str) -> bool:
        """Convenience check matching Connector.is_configured() but
        resolvable by service name through the manager, without the
        caller needing the Connector instance itself. False (not raised)
        if the service isn't registered."""
        connector = self._registry.get(service_name)
        if connector is None:
            return False
        return connector.is_configured()

    def connect(self, service_name: str) -> IntegrationStatusInfo:
        """Re-check and report a connector's status, clearing any prior
        'explicitly disconnected' record for it.

        This does NOT perform an OAuth flow or exchange any code for
        tokens - jarvis.integrations.oauth.exchange_code_for_tokens() is
        itself deliberately unimplemented, so there is currently nothing
        for this method to drive end-to-end for an OAuth-based connector.
        What it does: if the connector is already configured (e.g. the
        password env vars are set, or tokens were already stored by some
        other means), it reports CONNECTED. If not, it reports
        NOT_CONFIGURED - the caller (a human, or a future CLI command)
        still has to actually provide credentials or complete an OAuth
        authorization step outside of this method before a later call to
        connect() would report CONNECTED.
        """
        connector = self._registry.get(service_name)
        if connector is None:
            return IntegrationStatusInfo(
                service_name=service_name,
                status=IntegrationStatus.ERROR,
                detail=f"'{service_name}' is not a registered integration.",
            )
        self._explicitly_disconnected.discard(service_name)
        return self._status_for_connector(connector)

    def disconnect(self, service_name: str) -> IntegrationStatusInfo:
        """Clear whatever locally-held credential state the connector
        manages on its own (Connector.disconnect()) and mark it as
        explicitly disconnected. Makes no network call and never revokes
        anything with the external service - see Connector.disconnect()'s
        docstring. If the connector doesn't support disconnect() (the
        default False - e.g. a password-only connector with nothing
        local to clear), the service is still marked as explicitly
        disconnected for status-reporting purposes, but nothing is
        actually removed; the detail message says so.
        """
        connector = self._registry.get(service_name)
        if connector is None:
            return IntegrationStatusInfo(
                service_name=service_name,
                status=IntegrationStatus.ERROR,
                detail=f"'{service_name}' is not a registered integration.",
            )

        try:
            cleared = connector.disconnect()
        except Exception as e:
            return IntegrationStatusInfo(
                service_name=service_name,
                status=IntegrationStatus.ERROR,
                detail=f"Disconnect failed: {e}",
            )

        self._explicitly_disconnected.add(service_name)
        status_info = self._status_for_connector(connector)
        if not cleared:
            status_info = IntegrationStatusInfo(
                service_name=status_info.service_name,
                status=status_info.status,
                detail=(
                    f"{status_info.detail} (no locally-stored credential to clear for "
                    f"'{service_name}' - if a password is set via environment variables, "
                    "unset it yourself to fully disconnect.)"
                ),
            )
        return status_info
