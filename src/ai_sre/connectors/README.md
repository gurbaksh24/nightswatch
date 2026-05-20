# `ai_sre.connectors`

Adapters for external telemetry sources. The investigation orchestrator
never imports a concrete connector — it asks the registry.

## Files

| Path | Purpose |
|---|---|
| `base.py` | `Connector` ABC, `ConnectorQuery` tagged union, `ConnectorResult`, `ConnectorHealth`, `ConnectorKind` enum. |
| `registry.py` | Per-tenant connector lookup. Wires concrete connectors at startup. |
| `prometheus/` | Prometheus connector — the only one in MVP. |

## Adding a new connector

Per **NFR-8.1** (extensibility):

1. New package: `connectors/<name>/`.
2. Implement `Connector` ABC. Subclass only — do NOT add new methods to the
   base.
3. Decide which `ConnectorQuery` variants you support; raise
   `ConnectorUnsupported` for the rest.
4. Register the kind in `ConnectorKind` and wire it in `registry.py`.
5. Add contract tests (recorded cassettes against the real service).
6. Document it here.

The orchestrator and the LLM tools will pick it up automatically.

## Why this abstraction

If we hardcoded Prometheus into the orchestrator, then adding Datadog would
mean rewriting the brain. The `Connector` interface keeps a stable shape
("ask for time-series data") that all telemetry providers can implement.

## What the LLM sees vs what the connector sees

The LLM doesn't generate PromQL. It generates **typed query intents** (e.g.
`RateOverWindow(metric="http_errors_total", labels={...}, window=5m)`).
The Prometheus connector translates intent → PromQL via vetted builders.
This is the seam that prevents PromQL injection (NFR-5.6).

## Dependency direction

```
core/ ──► connectors/base, connectors/registry  (interfaces only)

connectors/prometheus/ ──► connectors/base
connectors/prometheus/ ──► models/, schemas/, utils/
connectors/prometheus/ ──► ❌ core/, ❌ api/
```
