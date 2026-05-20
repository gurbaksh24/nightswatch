"""FakePrometheusConnector: returns scripted results for unit tests."""

from __future__ import annotations

from collections import deque

from ai_sre.connectors.base import (
    Connector,
    ConnectorHealth,
    ConnectorKind,
    ConnectorQuery,
    ConnectorResult,
)


class FakePrometheusConnector(Connector):
    kind = ConnectorKind.PROMETHEUS

    def __init__(self) -> None:
        self._results: deque[ConnectorResult] = deque()
        self.queries: list[ConnectorQuery] = []

    def queue_result(self, result: ConnectorResult) -> None:
        self._results.append(result)

    async def health_check(self) -> ConnectorHealth:
        return ConnectorHealth(healthy=True, latency_ms=1)

    async def discover_topology(self, service: dict) -> dict:
        return {"upstream": [], "downstream": []}

    async def discover_metrics(self, service: dict) -> list[dict]:
        return []

    async def query(self, query: ConnectorQuery) -> ConnectorResult:
        self.queries.append(query)
        if not self._results:
            return ConnectorResult(success=True)
        return self._results.popleft()
