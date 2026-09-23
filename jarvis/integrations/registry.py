"""Registry of configured connectors - the integrations-layer counterpart
to jarvis.tools.base.ToolRegistry. Holds whichever Connector instances
the running JARVIS process has set up (none, in this v1 - no real
connector exists yet), and answers "which services are available" /
"is this one configured" without callers needing to import each
connector module directly.
"""

from __future__ import annotations

from jarvis.integrations.base import Connector


class IntegrationRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}

    def register(self, connector: Connector) -> None:
        if connector.service_name in self._connectors:
            raise ValueError(f"Connector '{connector.service_name}' already registered")
        self._connectors[connector.service_name] = connector

    def get(self, service_name: str) -> Connector | None:
        return self._connectors.get(service_name)

    def all(self) -> list[Connector]:
        return list(self._connectors.values())

    def configured(self) -> list[Connector]:
        """Connectors that are registered AND have their required
        credentials present (Connector.is_configured()) - the set that
        could actually be used right now, as opposed to ones merely
        wired up in code but missing an API key/token.
        """
        return [c for c in self._connectors.values() if c.is_configured()]
