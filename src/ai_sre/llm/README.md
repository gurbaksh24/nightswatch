# `ai_sre.llm`

The LLM gateway and the tool registry. Every LLM call in the platform goes
through here.

## Files

| Path | Purpose |
|---|---|
| `gateway.py` | `LLMGateway` — the only entry point. Wraps providers, accounting, retry, structured output, tool loop. |
| `tools.py` | `ToolSpec` definitions + the registry. Adding a tool happens here. |
| `prompts/` | All prompts as Python constants. Versioned. |

## Why a gateway

Three reasons:

1. **Accounting:** every call records tokens, cost, latency on the budget.
2. **Provider abstraction:** swapping Anthropic ↔ Bedrock ↔ Azure ↔ OpenAI is
   a one-file change.
3. **Caching & retry:** these are easy to break individually if scattered
   across the codebase.

## Two call shapes

| Method | Use case |
|---|---|
| `gateway.chat(...)` | Single-shot LLM call. Optional structured output via tool-use or response_format. Used by ReportStage, TriageStage (when needed). |
| `gateway.tool_loop(...)` | Agentic loop. Used by HypothesisStage and ValidationStage. The gateway dispatches tool calls to handlers and loops up to `max_iterations` or until the model says it's done. |

## Tools

Tools are how the LLM interacts with the outside world. A tool is:

```python
ToolSpec(
    name="query_prometheus",
    description="Query a Prometheus instance with a typed intent.",
    input_schema={...JSON Schema...},
    handler=async (input, ctx) -> output,
)
```

To add a tool:
1. Define `ToolSpec` in `tools.py`.
2. Register via `register()`.
3. Add it to `for_stage(stage_name)` selection if appropriate.
4. Update the relevant stage's docstring.

## Prompts

Every prompt is a module-level constant with a `PROMPT_VERSION` string.
Changing a prompt **must** bump the version. The version is recorded on
every `tool_call` and `report` row for reproducibility.

## Dependency direction

```
core/ ──► llm/gateway, llm/tools, llm/prompts (interfaces and registry)
llm/  ──► models/, schemas/, utils/, connectors/ (only abstract bases)
llm/  ──► ❌ api/, ❌ core/
```
