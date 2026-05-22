# Spec 0009: `query_prometheus` tool + HypothesisStage end-to-end

> First time the LLM actually queries customer data. The Hypothesis stage drives the tool loop; the connector executes vetted PromQL.

**Spec ID:** 0009
**Status:** ready-for-agent
**Depends on:** 0003, 0005, 0007, 0008

---

## Motivation

FR-6.1, FR-6.2, FR-6.3, FR-6.7; NFR-5.6 (PromQL injection impossible).

## Scope

- [ ] `QueryIntent` typed union in `connectors/prometheus/queries.py` (`RateOverWindow`, `Aggregation`, `Percentile`, `ChangeOverTime`, `RawPromQl`).
- [ ] `PromQlBuilder.build(intent) -> str` with vetted PromQL fragments + a parser that rejects unsafe raw promql.
- [ ] Tools (one ToolSpec each, registered in spec 0008's registry):
  - `query_prometheus(intent: dict, start, end, step)` — returns trimmed samples.
  - `list_metric_names(filter: str)` — queries `metric_catalog_entry`.
  - `get_service_dependencies()` — queries `service_dependency`.
  - `get_alert_details()` — returns the triggering alert from context.
- [ ] Replace `HypothesisStage` stub with a real implementation: LLM tool-loop with up to N hypotheses (default 5), Anthropic structured output for the hypothesis list.
- [ ] `hypothesis.py` prompt with `PROMPT_VERSION`.
- [ ] Tests with FakeLLMProvider returning scripted tool_use + final structured output.

## Out of scope

- ValidationStage (spec 0011).
- search_runbooks / search_past_incidents (spec 0015).
- Streaming partial hypotheses.

## Context

- `docs/03-lld.md` §10.2, §11, §12
- `docs/01-requirements.md` §3.6
- Specs 0003 (connector), 0005 (catalog), 0008 (gateway).

## Design

### Files to touch

- `src/ai_sre/connectors/prometheus/queries.py` — implement.
- `src/ai_sre/llm/tools/__init__.py` — new package for concrete tools.
- `src/ai_sre/llm/tools/query_prometheus.py` — new.
- `src/ai_sre/llm/tools/list_metric_names.py` — new.
- `src/ai_sre/llm/tools/get_service_dependencies.py` — new.
- `src/ai_sre/llm/tools/get_alert_details.py` — new.
- `src/ai_sre/llm/prompts/hypothesis.py` — implement.
- `src/ai_sre/core/investigation/stages/hypothesis.py` — replace stub.
- Tests.

### New / changed contracts

```python
# connectors/prometheus/queries.py
type QueryIntent = RateOverWindow | Aggregation | Percentile | ChangeOverTime | RawPromQl

class PromQlBuilder:
    def build(self, intent: QueryIntent) -> str: ...
    def validate_raw(self, raw: str) -> None:
        """Reject: topk over unbounded series, recording-rule writes, unbounded ranges, etc."""

# llm/tools/query_prometheus.py
QUERY_PROMETHEUS = ToolSpec(
    name="query_prometheus",
    description=(
        "Run a PromQL-equivalent query against the connected Prometheus. "
        "Pass a structured intent or `raw_promql` (must pass safety validation)."
    ),
    input_schema={...},   # JSON Schema; mirror QueryIntent variants
    handler=query_prometheus_handler,
)
```

### Edge cases

- LLM passes invalid intent shape → JSON Schema validation rejects before handler.
- LLM passes `raw_promql` that contains a forbidden construct → handler returns `{"error":"unsafe_query","detail":"..."}` to the model, doesn't execute.
- Query times out → tool result is an error; LLM can decide to try a narrower window.
- Returned data > truncation threshold → trim and set `"truncated": true`; LLM sees the flag.
- Hypothesis output isn't valid against the expected schema → re-prompt once, then fall back to text → record stage as `partial`.

## Tests

- Unit: PromQlBuilder for each intent variant; raw query validator rejects forbidden constructs.
- Unit: each tool handler against FakePrometheusConnector.
- Integration: end-to-end Hypothesis run against a scripted FakeLLMProvider that emits 2 tool_use blocks then a structured hypothesis list; assert hypotheses persisted in context + `tool_call` rows.

## Rollout

- Migration: none.
- Observability: `aisre_tool_calls_total{tool}` already covered by spec 0008.

## Definition of done

- [ ] All scope complete.
- [ ] On a synthetic alert against a real Prometheus, the Hypothesis stage produces 1+ hypotheses each with at least one tool-call citation.

## Follow-ups

- Tool: `get_recent_changes(service, window)` — needs an integration with the customer's deploy tracker (deferred).
- Cache repeated identical queries within one investigation.
