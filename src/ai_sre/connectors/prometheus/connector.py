"""Prometheus connector implementation.

Translates `ConnectorQuery` instances to PromQL via the typed builders in
`queries.py`, executes them against the customer's Prometheus, and returns
a normalised `ConnectorResult`.

Auth modes supported: none, bearer, basic. mTLS deferred.

Safety enforced here:
    * Per-query timeout (default 10s).
    * Max series and max points enforced via Prometheus query params + a
      post-validation check on the response.
    * `RawPromQL` goes through the parser in `queries.parse_safe_promql`.

See LLD §10.1, §10.2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ai_sre.connectors.base import (
    Connector,
    ConnectorHealth,
    ConnectorKind,
    ConnectorQuery,
    ConnectorResult,
    PromQLQuery,
)
from ai_sre.exceptions import ConnectorTimeout, ConnectorUnsupported


@dataclass(frozen=True)
class PrometheusAuth:
    kind: str          # "none" | "bearer" | "basic"
    bearer_token: str | None = None
    basic_user: str | None = None
    basic_password: str | None = None


@dataclass(frozen=True)
class PrometheusConfig:
    base_url: str
    auth: PrometheusAuth
    query_timeout_seconds: int = 10
    max_points: int = 10_000
    max_series: int = 10_000


class PrometheusConnector(Connector):
    kind = ConnectorKind.PROMETHEUS

    def __init__(self, config: PrometheusConfig, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.query_timeout_seconds,
            headers=self._auth_headers(),
        )

    def _auth_headers(self) -> dict[str, str]:
        a = self.config.auth
        if a.kind == "bearer" and a.bearer_token:
            return {"Authorization": f"Bearer {a.bearer_token}"}
        return {}

    async def health_check(self) -> ConnectorHealth:
        try:
            r = await self._client.get("/-/healthy")
            return ConnectorHealth(healthy=r.status_code == 200, latency_ms=int(r.elapsed.total_seconds() * 1000))
        except httpx.HTTPError as e:
            return ConnectorHealth(healthy=False, error=str(e))

    async def discover_topology(self, service: dict) -> dict:
        # TODO(spec-NNNN: prom-topology):
        #   - Pull http_requests_total / rpc_client_duration_seconds / etc.
        #   - Group by `service` and `upstream`/`downstream` labels.
        #   - Return {"upstream": [...], "downstream": [...]}.
        raise NotImplementedError

    async def discover_metrics(self, service: dict) -> list[dict]:
        # TODO(spec-NNNN: prom-catalog):
        #   - GET /api/v1/label/__name__/values filtered by service selector.
        #   - For each metric name, sample label keys + values.
        #   - Return MetricCatalogEntry-shaped dicts.
        raise NotImplementedError

    async def query(self, query: ConnectorQuery) -> ConnectorResult:
        if not isinstance(query, PromQLQuery):
            raise ConnectorUnsupported(
                f"Prometheus connector does not support query kind: {type(query).__name__}"
            )
        # TODO(spec-NNNN: prom-query):
        #   1. Translate query.intent → PromQL via queries.build(...).
        #   2. Call /api/v1/query or /api/v1/query_range based on time bounds.
        #   3. Validate series and point caps.
        #   4. Return ConnectorResult.
        try:
            _ = self._build_request(query)
        except httpx.TimeoutException as e:
            raise ConnectorTimeout(str(e)) from e
        raise NotImplementedError

    def _build_request(self, query: PromQLQuery) -> dict[str, Any]:
        # Returns request kwargs for httpx.
        raise NotImplementedError
