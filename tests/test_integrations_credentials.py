"""Tests for jarvis.integrations.credentials: env-var-only credential
lookup for external connectors, generalizing jarvis.config's
ANTHROPIC_API_KEY pattern to any number of named services. Never reads
from a file JARVIS manages, never logs/prints an unmasked value - all
display goes through jarvis.core.secrets.mask_secret()."""

from __future__ import annotations

import pytest

from jarvis.integrations import credentials


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    # Never let a test's registration leak into another test or the real
    # CONNECTOR_ENV_VARS module state.
    monkeypatch.setattr(credentials, "CONNECTOR_ENV_VARS", {})


# --- register_connector_env_vars ----------------------------------------------------


def test_register_connector_env_vars_stores_the_list():
    credentials.register_connector_env_vars("fake_service", ["FAKE_KEY", "FAKE_SECRET"])
    assert credentials.CONNECTOR_ENV_VARS["fake_service"] == ["FAKE_KEY", "FAKE_SECRET"]


def test_register_connector_env_vars_overwrites_not_merges():
    credentials.register_connector_env_vars("fake_service", ["FAKE_KEY"])
    credentials.register_connector_env_vars("fake_service", ["OTHER_KEY"])
    assert credentials.CONNECTOR_ENV_VARS["fake_service"] == ["OTHER_KEY"]


# --- get_credential --------------------------------------------------------------------


def test_get_credential_reads_from_environment(monkeypatch):
    monkeypatch.setenv("FAKE_TEST_VAR", "secret-value-123")
    assert credentials.get_credential("fake_service", "FAKE_TEST_VAR") == "secret-value-123"


def test_get_credential_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("FAKE_TEST_VAR_UNSET", raising=False)
    assert credentials.get_credential("fake_service", "FAKE_TEST_VAR_UNSET") is None


# --- credential_status ------------------------------------------------------------------


def test_status_no_registered_vars_is_configured():
    status = credentials.credential_status("unregistered_service")
    assert status.is_configured is True
    assert status.required_vars == []
    assert status.missing == []


def test_status_all_vars_present_is_configured(monkeypatch):
    monkeypatch.setenv("FAKE_KEY_A", "a")
    monkeypatch.setenv("FAKE_KEY_B", "b")
    credentials.register_connector_env_vars("fake_service", ["FAKE_KEY_A", "FAKE_KEY_B"])

    status = credentials.credential_status("fake_service")
    assert status.is_configured is True
    assert status.missing == []


def test_status_missing_var_reports_it(monkeypatch):
    monkeypatch.setenv("FAKE_KEY_A", "a")
    monkeypatch.delenv("FAKE_KEY_MISSING", raising=False)
    credentials.register_connector_env_vars("fake_service", ["FAKE_KEY_A", "FAKE_KEY_MISSING"])

    status = credentials.credential_status("fake_service")
    assert status.is_configured is False
    assert status.missing == ["FAKE_KEY_MISSING"]


def test_status_all_missing(monkeypatch):
    monkeypatch.delenv("FAKE_KEY_X", raising=False)
    monkeypatch.delenv("FAKE_KEY_Y", raising=False)
    credentials.register_connector_env_vars("fake_service", ["FAKE_KEY_X", "FAKE_KEY_Y"])

    status = credentials.credential_status("fake_service")
    assert status.missing == ["FAKE_KEY_X", "FAKE_KEY_Y"]


# --- format_credential_status: never leaks the raw value --------------------------------


def test_format_never_includes_raw_credential_value(monkeypatch):
    monkeypatch.setenv("FAKE_SECRET_VALUE", "supersecretvalue12345")
    credentials.register_connector_env_vars("fake_service", ["FAKE_SECRET_VALUE"])

    status = credentials.credential_status("fake_service")
    output = credentials.format_credential_status(status)
    assert "supersecretvalue12345" not in output


def test_format_no_registered_vars():
    status = credentials.credential_status("unregistered_service")
    output = credentials.format_credential_status(status)
    assert "no credentials registered" in output.lower()


def test_format_configured_service_shows_configured(monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "value")
    credentials.register_connector_env_vars("fake_service", ["FAKE_KEY"])
    status = credentials.credential_status("fake_service")
    output = credentials.format_credential_status(status)
    assert "configured" in output.lower()
    assert "NOT" not in output


def test_format_unconfigured_service_shows_missing(monkeypatch):
    monkeypatch.delenv("FAKE_KEY_MISSING2", raising=False)
    credentials.register_connector_env_vars("fake_service", ["FAKE_KEY_MISSING2"])
    status = credentials.credential_status("fake_service")
    output = credentials.format_credential_status(status)
    assert "NOT fully configured" in output
    assert "FAKE_KEY_MISSING2" in output
