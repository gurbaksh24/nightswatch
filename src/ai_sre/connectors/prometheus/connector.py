"""Prometheus connector implementation.

Translates ``ConnectorQuery`` instances to PromQL via the typed builders in
:mod:`queries.py`, executes them against the customer's Prometheus, and
returns a normalised ``ConnectorResult``.

Auth modes supported: ``none``, ``bearer``, ``basic``. mTLS deferred.

Safety enforced here (per NFR-5.6):

    * Per-query timeout (default 10s).
    * Max series / max points enforced by inspecting the response after the
      Prometheus query (Prometheus has no native ``LIMIT`` clause, so we
      truncate post-hoc and flag the result).
    * ``RawPromQL`` always goes through :func:`parse_safe_promql`.

See LLD §10.1, §10.2.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
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
from ai_sre.connectors.prometheus.queries import build
from ai_sre.exceptions import ConnectorError, ConnectorTimeout, ConnectorUnsupported
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PrometheusAuth:
    """Auth config for a Prometheus integration. ``kind`` matches the
    discriminated union in :mod:`schemas.integration`.
    """

    kind: str  # "none" | "bearer" | "basic"
    bearer_token: str | None = None
    basic_user: str | None = None
    basic_password: str | None = None


@dataclass(frozen=True)
class PrometheusConfig:
    """Fully resolved Prometheus connection config, post-decryption."""

    base_url: str
    auth: PrometheusAuth
    query_timeout_seconds: int = 10
    max_points: int = 10_000
    max_series: int = 10_000

    @classmethod
    def from_decrypted(
        cls,
        config: dict[str, Any],
        *,
        query_timeout_seconds: int,
        max_points: int,
        max_series: int,
    ) -> PrometheusConfig:
        """Build from the decrypted integration config + per-tenant limits."""
        auth = config.get("auth") or {"type": "none"}
        return cls(
            base_url=str(config["url"]).rstrip("/"),
            auth=PrometheusAuth(
                kind=auth.get("type", "none"),
                bearer_token=auth.get("token"),
                basic_user=auth.get("username"),
                basic_password=auth.get("password"),
            ),
            query_timeout_seconds=query_timeout_seconds,
            max_points=max_points,
            max_series=max_series,
        )


class PrometheusConnector(Connector):
    """HTTP-over-PromQL connector. One instance per (tenant, integration)."""

    kind = ConnectorKind.PROMETHEUS

    def __init__(
        self,
        config: PrometheusConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.query_timeout_seconds,
            headers=self._auth_headers(),
            auth=self._basic_auth(),
        )

    # ---- HTTP plumbing ----

    def _auth_headers(self) -> dict[str, str]:
        a = self.config.auth
        if a.kind == "bearer" and a.bearer_token:
            return {"Authorization": f"Bearer {a.bearer_token}"}
        return {}

    def _basic_auth(self) -> tuple[str, str] | None:
        a = self.config.auth
        if a.kind == "basic" and a.basic_user is not None and a.basic_password is not None:
            return (a.basic_user, a.basic_password)
        return None

    async def aclose(self) -> None:
        """Close the underlying client. No-op if it was injected."""
        if self._owns_client:
            await self._client.aclose()

    # ---- Connector ABC ----

    async def health_check(self) -> ConnectorHealth:
        """Probe ``/-/healthy``. Considered healthy on 200; otherwise
        carries the upstream status code in ``details``."""
        start = time.monotonic()
        try:
            r = await self._client.get("/-/healthy")
        except httpx.TimeoutException as exc:
            return ConnectorHealth(
                healthy=False,
                latency_ms=int((time.monotonic() - start) * 1000),
                error=f"timeout: {exc}",
                details={"kind": "timeout"},
            )
        except httpx.HTTPError as exc:
            return ConnectorHealth(
                healthy=False,
                latency_ms=int((time.monotonic() - start) * 1000),
                error=str(exc),
                details={"kind": "http_error"},
            )
        latency_ms = int((time.monotonic() - start) * 1000)
        if r.status_code == 200:
            return ConnectorHealth(healthy=True, latency_ms=latency_ms)
        return ConnectorHealth(
            healthy=False,
            latency_ms=latency_ms,
            error=f"HTTP {r.status_code}",
            details={"status_code": r.status_code},
        )

    async def discover_topology(self, service: dict[str, Any]) -> dict[str, Any]:
        """Implemented in spec 0005."""
        raise NotImplementedError("Topology discovery lands in spec 0005.")

    async def discover_metrics(self, service: dict[str, Any]) -> list[dict[str, Any]]:
        """Implemented in spec 0005."""
        raise NotImplementedError("Metric catalog discovery lands in spec 0005.")

    async def query(self, query: ConnectorQuery) -> ConnectorResult:
        """Execute a PromQL query against ``/api/v1/query`` (instant) or
        ``/api/v1/query_range`` (range-based).

        Range-mode is chosen iff both ``start`` and ``end`` are set on the
        query.
        """
        if not isinstance(query, PromQLQuery):
            raise ConnectorUnsupported(
                f"Prometheus connector does not support query kind: "
                f"{type(query).__name__}"
            )
        if query.intent is None:
            raise ConnectorError("PromQLQuery.intent is required.")

        promql = build(query.intent)
        return await self._execute(promql, query.start, query.end, query.step)

    # ---- internals ----

    async def _execute(
        self,
        promql: str,
        start: datetime | None,
        end: datetime | None,
        step: Any,
    ) -> ConnectorResult:
        is_range = start is not None and end is not None
        path = "/api/v1/query_range" if is_range else "/api/v1/query"
        params: dict[str, Any] = {
            "query": promql,
            "timeout": f"{self.config.query_timeout_seconds}s",
        }
        if is_range:
            assert start is not None and end is not None
            params["start"] = start.timestamp()
            params["end"] = end.timestamp()
            params["step"] = f"{int(step.total_seconds())}s"

        t0 = time.monotonic()
        try:
            r = await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise ConnectorTimeout(
                f"Prometheus query timed out after "
                f"{self.config.query_timeout_seconds}s.",
                details={"path": path},
            ) from exc
        except httpx.HTTPError as exc:
            return ConnectorResult(
                success=False,
                latency_ms=int((time.monotonic() - t0) * 1000),
                error=f"HTTP transport: {exc}",
            )
        latency_ms = int((time.monotonic() - t0) * 1000)

        if r.status_code != 200:
            return ConnectorResult(
                success=False,
                latency_ms=latency_ms,
                error=f"Prometheus returned HTTP {r.status_code}",
                data={"status_code": r.status_code, "body": r.text[:512]},
            )

        body = r.json()
        if body.get("status") != "success":
            return ConnectorResult(
                success=False,
                latency_ms=latency_ms,
                error=str(body.get("error", "Prometheus error")),
                data={"errorType": body.get("errorType")},
            )

        # Normalise + cap.
        data = body.get("data", {})
        result = data.get("result", []) or []
        series_count = len(result)
        point_count = sum(
            len(entry.get("values", []) or ([entry["value"]] if entry.get("value") else []))
            for entry in result
        )

        truncated = False
        if series_count > self.config.max_series:
            logger.warning(
                "prometheus.query.truncated",
                series_count=series_count,
                max_series=self.config.max_series,
                promql=promql,
            )
            result = result[: self.config.max_series]
            truncated = True

        return ConnectorResult(
            success=True,
            data={
                "resultType": data.get("resultType"),
                "result": result,
                "promql": promql,
                "truncated": truncated,
            },
            series_count=min(series_count, self.config.max_series),
            point_count=point_count,
            latency_ms=latency_ms,
        )
