"""Tests for jarvis.integrations.registry.IntegrationRegistry: the
integrations-layer counterpart to jarvis.tools.base.ToolRegistry. No real
connector exists yet - exercised via a minimal fake connector."""

from __future__ import annotations

from jarvis.integrations.base import Connector, ExternalActionResult
from jarvis.integrations.registry import IntegrationRegistry


class _FakeConnector(Connector):
    def __init__(self, service_name: str, configured: bool = True):
        self.service_name = service_name
        self.actions = []
        self._configured = configured

    def is_configured(self) -> bool:
        return self._configured

    def execute(self, action_name: str, **kwargs) -> ExternalActionResult:
        return ExternalActionResult(ok=True, output="", service=self.service_name, action=action_name)


def test_register_and_get():
    registry = IntegrationRegistry()
    connector = _FakeConnector("fake_service")
    registry.register(connector)
    assert registry.get("fake_service") is connector


def test_get_unregistered_returns_none():
    registry = IntegrationRegistry()
    assert registry.get("unregistered") is None


def test_duplicate_registration_raises():
    registry = IntegrationRegistry()
    registry.register(_FakeConnector("fake_service"))
    try:
        registry.register(_FakeConnector("fake_service"))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_all_returns_every_registered_connector():
    registry = IntegrationRegistry()
    a = _FakeConnector("service_a")
    b = _FakeConnector("service_b")
    registry.register(a)
    registry.register(b)
    assert set(registry.all()) == {a, b}


def test_configured_excludes_unconfigured_connectors():
    registry = IntegrationRegistry()
    configured = _FakeConnector("configured_service", configured=True)
    unconfigured = _FakeConnector("unconfigured_service", configured=False)
    registry.register(configured)
    registry.register(unconfigured)

    assert registry.configured() == [configured]


def test_empty_registry_returns_empty_lists():
    registry = IntegrationRegistry()
    assert registry.all() == []
    assert registry.configured() == []
