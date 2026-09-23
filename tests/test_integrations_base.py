"""Tests for jarvis.integrations.base: the Connector/ExternalAction
interface every future connector (email, Stripe, Instagram, Facebook)
will implement. No real connector exists yet - these tests exercise the
interface itself via a minimal fake connector, confirming the shape is
usable and that execute() never runs without the caller having already
gone through approval (enforced by convention/tests here, not by
execute() itself, exactly like jarvis.tools.fs's write_file relies on its
own caller discipline)."""

from __future__ import annotations

import pytest

from jarvis.integrations.base import (
    Connector,
    CredentialError,
    ExternalAction,
    ExternalActionResult,
    RiskLevel,
)


class _FakeEmailConnector(Connector):
    """Minimal stand-in connector for testing the base interface only -
    not a real email integration. Simulates configured/unconfigured
    states via a constructor flag rather than real credential lookup."""

    service_name = "fake_email"

    def __init__(self, configured: bool = True):
        self._configured = configured
        self.actions = [
            ExternalAction(
                name="send_email",
                description="Send an email",
                risk_level=RiskLevel.IRREVERSIBLE_WRITE,
                input_schema={"type": "object", "properties": {"to": {"type": "string"}}},
            ),
            ExternalAction(
                name="list_recent_emails",
                description="List recent emails",
                risk_level=RiskLevel.READ_ONLY,
                input_schema={"type": "object", "properties": {}},
            ),
        ]

    def is_configured(self) -> bool:
        return self._configured

    def execute(self, action_name: str, **kwargs) -> ExternalActionResult:
        if not self._configured:
            raise CredentialError("fake_email is not configured")
        action = self.get_action(action_name)
        if action is None:
            return ExternalActionResult(
                ok=False, output=f"Unknown action: {action_name}",
                service=self.service_name, action=action_name,
            )
        return ExternalActionResult(
            ok=True, output=f"executed {action_name} with {kwargs}",
            service=self.service_name, action=action_name,
        )


# --- RiskLevel ---------------------------------------------------------------------


def test_risk_level_has_three_tiers():
    assert {RiskLevel.READ_ONLY, RiskLevel.REVERSIBLE_WRITE, RiskLevel.IRREVERSIBLE_WRITE} == set(RiskLevel)


# --- ExternalAction ------------------------------------------------------------------


def test_external_action_is_immutable():
    action = ExternalAction(
        name="x", description="d", risk_level=RiskLevel.READ_ONLY, input_schema={}
    )
    with pytest.raises(Exception):
        action.name = "y"  # type: ignore[misc]


# --- Connector interface --------------------------------------------------------------


def test_connector_declares_service_name_and_actions():
    connector = _FakeEmailConnector()
    assert connector.service_name == "fake_email"
    assert len(connector.actions) == 2


def test_get_action_finds_by_name():
    connector = _FakeEmailConnector()
    action = connector.get_action("send_email")
    assert action is not None
    assert action.risk_level == RiskLevel.IRREVERSIBLE_WRITE


def test_get_action_returns_none_for_unknown_name():
    connector = _FakeEmailConnector()
    assert connector.get_action("does_not_exist") is None


def test_is_configured_true():
    connector = _FakeEmailConnector(configured=True)
    assert connector.is_configured() is True


def test_is_configured_false():
    connector = _FakeEmailConnector(configured=False)
    assert connector.is_configured() is False


def test_execute_unconfigured_raises_credential_error():
    connector = _FakeEmailConnector(configured=False)
    with pytest.raises(CredentialError):
        connector.execute("send_email", to="someone@example.com")


def test_execute_succeeds_when_configured():
    connector = _FakeEmailConnector(configured=True)
    result = connector.execute("send_email", to="someone@example.com")
    assert result.ok is True
    assert result.service == "fake_email"
    assert result.action == "send_email"


def test_execute_unknown_action_returns_error_result_not_exception():
    connector = _FakeEmailConnector(configured=True)
    result = connector.execute("nonexistent_action")
    assert result.ok is False
    assert "Unknown action" in result.output


def test_cannot_instantiate_connector_directly():
    with pytest.raises(TypeError):
        Connector()  # type: ignore[abstract]


# --- disconnect() default behavior -----------------------------------------------


def test_disconnect_default_returns_false():
    # _FakeEmailConnector doesn't override disconnect() - the base
    # class's default (not supported, nothing to clear) must apply.
    connector = _FakeEmailConnector()
    assert connector.disconnect() is False


def test_disconnect_is_not_abstract_subclass_without_override_is_instantiable():
    # Confirms disconnect() being added to the base class did not turn it
    # into a required override - any existing/future Connector subclass
    # that only implements is_configured()/execute() must still work.
    connector = _FakeEmailConnector(configured=True)
    assert isinstance(connector, Connector)
