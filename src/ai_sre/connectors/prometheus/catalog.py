"""Metric catalog discovery for Prometheus.

Periodically (per FR-3.2, every 6 hours by default) we walk the customer's
Prometheus and build a catalog of metrics relevant to their subject service:

    * Metric name + type (counter/gauge/histogram).
    * Observed label keys and a sample of values.
    * Help text (parsed from `# HELP` via /api/v1/metadata).
    * Unit (parsed from suffix where possible).

The LLM uses this catalog via the `list_metric_names` tool. Critically: the
LLM doesn't have to guess that the connection pool metric is called
`hikaricp_active_connections` — it can search the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricEntry:
    name: str
    type: str               # counter | gauge | histogram | summary | unknown
    labels: dict[str, list[str]]   # label_key → sample of values
    unit: str | None = None
    help_text: str | None = None


class CatalogBuilder:
    """Builds metric catalogs against a Prometheus instance.

    The full implementation is its own spec; signatures below.
    """

    async def build_for_service(self, service: dict) -> list[MetricEntry]:
        """Return metric entries scoped to the service's label selector."""
        # TODO(spec-NNNN: prom-catalog):
        #   1. GET /api/v1/label/__name__/values?match[]=...  (scoped)
        #   2. GET /api/v1/metadata to enrich type / help_text / unit.
        #   3. For each metric, sample labels via series API.
        raise NotImplementedError
