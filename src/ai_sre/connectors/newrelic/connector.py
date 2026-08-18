"""NewRelicConnector — NRQL over NerdGraph (spec 0019).

Implements the same four ``Connector`` operations as the Prometheus
connector, against New Relic's GraphQL API:

* ``health_check``      — resolve the account by id.
* ``query``             — accept :class:`NRQLQuery` only; typed intents are
                          rendered by :mod:`.queries`.
* ``discover_metrics``  — ``uniques(metricName)`` scoped to the selector.
                          Entries carry the name only (no type/help
                          enrichment in MVP — documented spec trade-off).
* ``discover_topology`` — NerdGraph entity relationships (``CALLS`` edges)
                          around the subject service's entity. Parse failures
                          degrade to empty edges with a warning, never a
                          failed refresh.
* ``probe_selector``    — ``uniqueCount(metricName)`` existence probe backing
                          service-selector validation (base-class hook).

Error contract mirrors Prometheus: HTTP timeout → :class:`ConnectorTimeout`;
non-200 / GraphQL ``errors`` → failed :class:`ConnectorResult` (not raised).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from ai_sre.connectors.base import (
    Connector,
    ConnectorHealth,
    ConnectorKind,
    ConnectorQuery,
    ConnectorResult,
    NRQLQuery,
)
from ai_sre.connectors.newrelic import queries
from ai_sre.exceptions import ConnectorError, ConnectorTimeout, ConnectorUnsupported
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)

_ENDPOINTS = {
    "US": "https://api.newrelic.com/graphql",
    "EU": "https://api.eu.newrelic.com/graphql",
}

_NRQL_GQL = """
query($accountId: Int!, $nrql: Nrql!) {
  actor { account(id: $accountId) { nrql(query: $nrql) { results } } }
}
"""

_HEALTH_GQL = """
query($accountId: Int!) {
  actor { account(id: $accountId) { id name } }
}
"""

_ENTITY_SEARCH_GQL = """
query($q: String!) {
  actor { entitySearch(query: $q) { results { entities { guid name } } } }
}
"""

_RELATED_GQL = """
query($guid: EntityGuid!) {
  actor {
    entity(guid: $guid) {
      relatedEntities {
        results {
          type
          source { entity { guid name } }
          target { entity { guid name } }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class NewRelicConfig:
    """Decrypted connection config for one tenant's New Relic account."""

    account_id: int
    api_key: str
    region: str = "US"
    query_timeout_seconds: int = 10
    max_series: int = 10_000

    @classmethod
    def from_decrypted(
        cls,
        config: dict[str, Any],
        *,
        query_timeout_seconds: int,
        max_series: int,
    ) -> NewRelicConfig:
        return cls(
            account_id=int(config["account_id"]),
            api_key=str(config["api_key"]),
            region=str(config.get("region", "US")).upper(),
            query_timeout_seconds=query_timeout_seconds,
            max_series=max_series,
        )

    @property
    def endpoint(self) -> str:
        return _ENDPOINTS.get(self.region, _ENDPOINTS["US"])


class NewRelicConnector(Connector):
    """Connector backed by New Relic's NerdGraph API."""

    kind = ConnectorKind.NEWRELIC

    def __init__(self, config: NewRelicConfig) -> None:
        self.config = config
        self._client = httpx.AsyncClient(
            headers={"API-Key": config.api_key},
            timeout=config.query_timeout_seconds,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- Connector API ----

    async def health_check(self) -> ConnectorHealth:
        started = time.monotonic()
        try:
            data = await self._graphql(_HEALTH_GQL, {"accountId": self.config.account_id})
        except ConnectorTimeout as exc:
            return ConnectorHealth(healthy=False, error=str(exc), details={"kind": "timeout"})
        except ConnectorError as exc:
            return ConnectorHealth(healthy=False, error=str(exc), details={"kind": "http_error"})
        latency_ms = int((time.monotonic() - started) * 1000)

        account = ((data.get("actor") or {}).get("account")) or None
        if account is None:
            return ConnectorHealth(
                healthy=False,
                latency_ms=latency_ms,
                error="Account not visible to this API key.",
                details={"kind": "account_not_found"},
            )
        return ConnectorHealth(healthy=True, latency_ms=latency_ms)

    async def query(self, query: ConnectorQuery) -> ConnectorResult:
        if not isinstance(query, NRQLQuery):
            raise ConnectorUnsupported(f"NewRelicConnector cannot run {type(query).__name__}.")
        if query.intent is None:
            raise ConnectorError("NRQLQuery.intent is required.")
        nrql = queries.build(query.intent, start=query.start, end=query.end)
        return await self._run_nrql(nrql)

    async def discover_metrics(self, service: dict[str, Any]) -> list[dict[str, Any]]:
        """Metric names matching the selector, in the persisted catalog shape."""
        selector = service.get("label_selector") or {}
        result = await self._run_nrql(queries.metric_discovery_query(selector))
        if not result.success:
            raise ConnectorError(result.error or "New Relic metric discovery failed.")
        names = _first_list_value(result.data.get("results") or [])
        return [
            {
                "metric_name": str(name),
                "metric_type": None,
                "labels": {},
                "unit": None,
                "help_text": None,
            }
            for name in names
        ]

    async def discover_topology(self, service: dict[str, Any]) -> dict[str, Any]:
        """Upstream/downstream edges from NerdGraph ``CALLS`` relationships.

        The subject entity is found by name (the registered service name,
        falling back to the first selector value). Any parse surprise
        degrades to empty edges — topology is an enrichment, not a
        prerequisite.
        """
        name = service.get("name") or next(iter((service.get("label_selector") or {}).values()), "")
        if not name:
            return {"upstream": [], "downstream": []}
        try:
            return await self._topology_for_name(str(name))
        except (ConnectorError, KeyError, TypeError) as exc:
            logger.warning("newrelic.topology_discovery_failed", service=name, error=str(exc))
            return {"upstream": [], "downstream": []}

    async def probe_selector(self, selector: dict[str, str]) -> ConnectorResult:
        """Existence probe: series_count = distinct matching metric names."""
        result = await self._run_nrql(queries.selector_probe_query(selector))
        if not result.success:
            return result
        rows = result.data.get("results") or []
        count = int(_first_number_value(rows))
        return ConnectorResult(
            success=True,
            data=result.data,
            series_count=count,
            latency_ms=result.latency_ms,
        )

    # ---- internals ----

    async def _run_nrql(self, nrql: str) -> ConnectorResult:
        started = time.monotonic()
        try:
            data = await self._graphql(
                _NRQL_GQL, {"accountId": self.config.account_id, "nrql": nrql}
            )
        except ConnectorTimeout:
            raise
        except ConnectorError as exc:
            return ConnectorResult(success=False, error=str(exc))
        latency_ms = int((time.monotonic() - started) * 1000)

        nrql_block = (((data.get("actor") or {}).get("account")) or {}).get("nrql")
        if nrql_block is None or nrql_block.get("results") is None:
            return ConnectorResult(
                success=False,
                error="NerdGraph returned no NRQL results block.",
                latency_ms=latency_ms,
            )
        results: list[dict[str, Any]] = nrql_block["results"]
        truncated = len(results) > self.config.max_series
        if truncated:
            logger.warning(
                "newrelic.query.truncated",
                returned=len(results),
                max_series=self.config.max_series,
            )
            results = results[: self.config.max_series]
        return ConnectorResult(
            success=True,
            data={"nrql": nrql, "results": results, "truncated": truncated},
            series_count=len(results),
            point_count=sum(len(r) for r in results),
            latency_ms=latency_ms,
        )

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """POST one GraphQL operation; return the ``data`` payload.

        Raises ConnectorTimeout on timeouts, ConnectorError on transport
        failures, non-200s, or GraphQL-level ``errors``.
        """
        try:
            resp = await self._client.post(
                self.config.endpoint,
                json={"query": query, "variables": variables},
            )
        except httpx.TimeoutException as exc:
            raise ConnectorTimeout(f"NerdGraph timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ConnectorError(f"NerdGraph request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ConnectorError(f"NerdGraph HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if body.get("errors"):
            first = body["errors"][0]
            raise ConnectorError(f"NerdGraph error: {first.get('message', 'unknown')}")
        return body.get("data") or {}

    async def _topology_for_name(self, name: str) -> dict[str, Any]:
        safe = queries.escape_string(name)
        search = await self._graphql(
            _ENTITY_SEARCH_GQL, {"q": f"name = '{safe}' AND domain IN ('APM', 'EXT')"}
        )
        entities = (
            ((search.get("actor") or {}).get("entitySearch") or {}).get("results") or {}
        ).get("entities") or []
        if not entities:
            return {"upstream": [], "downstream": []}
        guid = entities[0]["guid"]

        related = await self._graphql(_RELATED_GQL, {"guid": guid})
        rows = (
            (((related.get("actor") or {}).get("entity")) or {}).get("relatedEntities") or {}
        ).get("results") or []

        upstream: list[dict[str, Any]] = []
        downstream: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if "CALLS" not in str(row.get("type", "")):
                continue
            source = ((row.get("source") or {}).get("entity")) or {}
            target = ((row.get("target") or {}).get("entity")) or {}
            if source.get("guid") == guid and target.get("name"):
                edge = ("downstream", str(target["name"]))
                bucket = downstream
            elif target.get("guid") == guid and source.get("name"):
                edge = ("upstream", str(source["name"]))
                bucket = upstream
            else:
                continue
            if edge[1] == name or edge in seen:
                continue
            seen.add(edge)
            bucket.append({"name": edge[1], "via_label": "nerdgraph"})
        return {"upstream": upstream, "downstream": downstream}


def _first_list_value(rows: list[dict[str, Any]]) -> list[Any]:
    """First list-typed value in the first result row (uniques() shape)."""
    for row in rows:
        for value in row.values():
            if isinstance(value, list):
                return value
    return []


def _first_number_value(rows: list[dict[str, Any]]) -> float:
    """First numeric value in the first result row (uniqueCount() shape)."""
    for row in rows:
        for value in row.values():
            if isinstance(value, int | float) and not isinstance(value, bool):
                return float(value)
    return 0.0
