"""Formats IntegrationsManager.list_statuses() output into human-readable
text for the CLI's 'jarvis integrations' argument and the REPL
'integrations' keyword. Kept separate from IntegrationsManager itself,
mirroring project_scan_view's separation from project_scan - no status
logic lives here, only presentation.

Every field IntegrationStatusInfo carries (service_name, status, detail)
is already safe to display in full - detail is built from
jarvis.integrations.credentials.format_credential_status(), which is
already masked - so this module does no additional filtering; it only
arranges what's already safe to show.
"""

from __future__ import annotations

from jarvis.integrations.manager import IntegrationStatus, IntegrationStatusInfo

_STATUS_LABELS = {
    IntegrationStatus.NOT_CONFIGURED: "NOT CONFIGURED",
    IntegrationStatus.CONNECTED: "CONNECTED",
    IntegrationStatus.DISCONNECTED: "DISCONNECTED",
    IntegrationStatus.ERROR: "ERROR",
}


def _format_one(info: IntegrationStatusInfo) -> str:
    label = _STATUS_LABELS.get(info.status, info.status.value.upper())
    lines = [f"{info.service_name}: {label}"]
    if info.detail:
        lines.extend(f"  {line}" for line in info.detail.splitlines())
    return "\n".join(lines)


def format_integration_statuses(statuses: list[IntegrationStatusInfo]) -> str:
    """Return a human-readable summary of every integration's status.
    Never raises - an empty list is reported as "no integrations
    registered" rather than an empty/confusing blank output.
    """
    if not statuses:
        return "No integrations registered."

    header = f"Integrations ({len(statuses)}):"
    body = "\n\n".join(_format_one(info) for info in statuses)
    return f"{header}\n\n{body}"
