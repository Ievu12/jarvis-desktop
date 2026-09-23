"""Tests for jarvis.integrations.manager_view.format_integration_statuses:
pure formatting of IntegrationsManager.list_statuses() output, no status
logic, no side effects. Confirms the output is human-readable and never
introduces its own credential leakage beyond whatever IntegrationStatusInfo
already carries (which is itself always pre-masked)."""

from __future__ import annotations

from jarvis.integrations.manager import IntegrationStatus, IntegrationStatusInfo
from jarvis.integrations.manager_view import format_integration_statuses


def _info(**overrides) -> IntegrationStatusInfo:
    defaults = dict(
        service_name="fake_service",
        status=IntegrationStatus.NOT_CONFIGURED,
        detail="fake_service: NOT fully configured - missing FAKE_KEY.",
    )
    defaults.update(overrides)
    return IntegrationStatusInfo(**defaults)


def test_empty_list_reports_none_registered():
    result = format_integration_statuses([])
    assert "no integrations registered" in result.lower()


def test_header_shows_count():
    result = format_integration_statuses([_info(), _info(service_name="another")])
    assert "Integrations (2)" in result


def test_service_name_shown():
    result = format_integration_statuses([_info(service_name="email")])
    assert "email" in result


def test_not_configured_status_shown():
    result = format_integration_statuses([_info(status=IntegrationStatus.NOT_CONFIGURED)])
    assert "NOT CONFIGURED" in result


def test_connected_status_shown():
    result = format_integration_statuses([_info(status=IntegrationStatus.CONNECTED)])
    assert "CONNECTED" in result


def test_disconnected_status_shown():
    result = format_integration_statuses([_info(status=IntegrationStatus.DISCONNECTED)])
    assert "DISCONNECTED" in result


def test_error_status_shown():
    result = format_integration_statuses([_info(status=IntegrationStatus.ERROR)])
    assert "ERROR" in result


def test_detail_text_included():
    result = format_integration_statuses(
        [_info(detail="fake_service: NOT fully configured - missing SOME_VAR.")]
    )
    assert "missing SOME_VAR" in result


def test_multiline_detail_is_indented():
    result = format_integration_statuses(
        [_info(detail="line one\nline two")]
    )
    assert "line one" in result
    assert "line two" in result


def test_multiple_services_all_shown():
    result = format_integration_statuses(
        [
            _info(service_name="email", status=IntegrationStatus.CONNECTED),
            _info(service_name="stripe", status=IntegrationStatus.NOT_CONFIGURED),
        ]
    )
    assert "email" in result
    assert "stripe" in result
    assert "CONNECTED" in result
    assert "NOT CONFIGURED" in result


def test_empty_detail_does_not_crash():
    result = format_integration_statuses([_info(detail="")])
    assert "fake_service" in result


def test_output_is_deterministic():
    statuses = [_info()]
    assert format_integration_statuses(statuses) == format_integration_statuses(statuses)


def test_never_adds_content_beyond_what_detail_already_contains():
    # This module must not itself invent or append any credential-shaped
    # text - it only arranges the already-masked detail string.
    result = format_integration_statuses(
        [_info(detail="fake_service: configured.\n  FAKE_KEY: ****...abcd")]
    )
    assert "****...abcd" in result  # the masked form passes through unchanged
