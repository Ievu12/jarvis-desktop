"""Credential lookup for external-service connectors: the same env-var-
only model jarvis.config already uses for ANTHROPIC_API_KEY, generalized
to any number of named services. No credential is ever read from a file
JARVIS itself manages, written to a file, or logged/printed unmasked -
jarvis.core.secrets.mask_secret() is reused for any display purpose.

No real connector is registered here yet - CONNECTOR_ENV_VARS is the
declared shape future connectors (email, Stripe, Instagram, Facebook,
...) will register their required variable names under, so credential
status/masking works uniformly once they exist, rather than each
connector inventing its own lookup and masking logic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from jarvis.core.secrets import mask_secret

# Maps a service name to the environment variable names it needs. Empty
# for now - populated as real connectors are added (e.g.
# "email": ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"]). A connector
# declares its own requirements; this registry only needs to know the
# names so credential_status() can report on any registered service
# uniformly, without each connector re-implementing masking/lookup.
CONNECTOR_ENV_VARS: dict[str, list[str]] = {}


def register_connector_env_vars(service: str, var_names: list[str]) -> None:
    """Declare which environment variables a service's credentials live
    in. Called once per connector at startup/registration time (see
    jarvis.integrations.registry), not per-request. Overwrites any prior
    registration for the same service name rather than merging, so a
    connector's declared requirements are always exactly what it last
    registered - no stale entries from an earlier version.
    """
    CONNECTOR_ENV_VARS[service] = list(var_names)


def get_credential(service: str, var_name: str) -> str | None:
    """Look up one credential value by service + variable name. Returns
    None if unset - never raises, never falls back to a file or default.
    Mirrors jarvis.config.ANTHROPIC_API_KEY's os.environ.get() pattern
    exactly, generalized to any service/variable pair.
    """
    return os.environ.get(var_name)


@dataclass
class CredentialStatus:
    """Whether a service's required credentials are present, without ever
    exposing their values. `missing` lists variable names that are unset;
    an empty list means everything required is configured."""

    service: str
    required_vars: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def is_configured(self) -> bool:
        return not self.missing


def credential_status(service: str) -> CredentialStatus:
    """Check which of a registered service's required env vars are set,
    without reading or exposing their values beyond presence/absence.
    A service with no registered variables (nothing declared via
    register_connector_env_vars(), or an unknown service name) is
    reported as configured with an empty requirement list - there is
    nothing to be missing.
    """
    required = CONNECTOR_ENV_VARS.get(service, [])
    missing = [name for name in required if not get_credential(service, name)]
    return CredentialStatus(service=service, required_vars=required, missing=missing)


def format_credential_status(status: CredentialStatus) -> str:
    """Human-readable summary of a CredentialStatus - masked values only,
    via jarvis.core.secrets.mask_secret(), never the raw credential."""
    if not status.required_vars:
        return f"{status.service}: no credentials registered."
    if status.is_configured:
        lines = [f"{status.service}: configured."]
    else:
        lines = [f"{status.service}: NOT fully configured - missing {', '.join(status.missing)}."]
    for var_name in status.required_vars:
        value = get_credential(status.service, var_name)
        lines.append(f"  {var_name}: {mask_secret(value)}")
    return "\n".join(lines)
