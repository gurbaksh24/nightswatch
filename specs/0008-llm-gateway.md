# Spec 0008: LLM Gateway + Anthropic provider + tool registry

> The single entry point for all LLM calls. After this lands, stages can chat() and tool_loop() against Anthropic with budget and observability built in.

**Spec ID:** 0008
**Status:** ready-for-agent
**Depends on:** 0007

---

## Motivation

FR-5.3 (log every tool call), FR-6 (tool surface), NFR-6.1 (per-investigation cost cap), NFR-8.3 (adding tools must not require prompt changes).

## Scope

- [ ] `LLMProvider` ABC and `AnthropicProvider` implementation (Anthropic Python SDK).
- [ ] `LLMGateway` with `chat()` and `tool_loop()`.
- [ ] Budget enforcement on every call.
- [ ] Retry with exponential backoff (`tenacity`) on transient provider errors.
- [ ] Prompt-prefix caching when available (Anthropic supports it).
- [ ] `ToolSpec` dataclass, `ToolRegistry` with `register()` and `for_stage()`.
- [ ] `ToolDispatcher` that invokes a handler with `(input, ctx)` and persists a `tool_call` row.
- [ ] `PromptVersion` constants in `llm/prompts/`.
- [ ] Tests using `FakeLLMProvider` (already in `tests/fakes/`).

## Out of scope

- Real tools — they ship in 0009 onwards.
- Structured-output coercion via Pydantic (we accept JSON in v1; spec 0012 tightens this for the Report stage specifically).
- OpenAI / Bedrock provider implementations — `LLMProvider` ABC supports them but only AnthropicProvider is implemented.

## Context

- `docs/03-lld.md` §11, §12
- `src/ai_sre/llm/gateway.py` (existing stub)
- `src/ai_sre/llm/tools.py` (existing stub)
- `src/ai_sre/llm/prompts/*` (existing stubs)

## Design

### Files to touch

- `src/ai_sre/llm/gateway.py` — implement Gateway + Provider ABC.
- `src/ai_sre/llm/providers/anthropic.py` — new file with `AnthropicProvider`.
- `src/ai_sre/llm/tools.py` — implement Registry + Dispatcher.
- `src/ai_sre/llm/prompts/system.py` — implement (currently stub).
- `src/ai_sre/core/investigation/orchestrator.py` — inject the gateway.
- `tests/unit/llm/test_gateway.py` — new.
- `tests/unit/llm/test_tools.py` — new.

### New / changed contracts

```python
# llm/gateway.py
class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self, *, system: str, messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_format: ResponseFormat | None = None,
    ) -> LLMResponse: ...

class LLMGateway:
    def __init__(self, provider: LLMProvider, prompt_version: str) -> None: ...

    async def chat(self, *, system, messages, tools=None, response_format=None, budget) -> LLMResponse:
        """One-shot. Updates budget; raises BudgetExhausted if over."""

    async def tool_loop(
        self, *, system, user, tools: list[ToolSpec],
        dispatcher: ToolDispatcher, budget: Budget, max_iterations: int = 10,
    ) -> LLMResponse:
        """Drive the model in a loop: it asks for tools, we run them, repeat until stop."""

# llm/tools.py
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: ToolHandler

class ToolRegistry:
    def register(self, spec: ToolSpec) -> None: ...
    def for_stage(self, stage_name: str) -> list[ToolSpec]: ...

class ToolDispatcher:
    def __init__(self, registry: ToolRegistry, repo: ToolCallRepository) -> None: ...
    async def dispatch(self, name: str, input: dict, ctx: InvestigationContext) -> dict: ...
```

### Edge cases

- LLM returns malformed JSON when `response_format` is set → re-prompt once with corrective message; if still bad, raise `LLMResponseInvalid` and the orchestrator records a stage error.
- Provider returns 429 / 503 → tenacity retries with backoff up to N times; if exhausted, raise `LLMError`.
- Tool handler raises → record `tool_call.outcome="error"` with error JSON, return error result to the model so it can recover.
- Tool input fails JSON Schema validation → record `outcome="error"` without calling handler.
- Budget exhausted mid-loop → break loop, return whatever we have.

## Tests

- Unit: gateway updates budget; retries on 429; structured-output retry.
- Unit: ToolDispatcher persists `tool_call` row; validates input; handles handler exceptions.
- Unit: tool_loop terminates on `stop_reason=end_turn`, on max_iterations, on BudgetExhausted.

## Rollout

- Migration: **required after all** — `tool_call` was modelled in ORM but never
  migrated, and the ToolDispatcher persists to it. Migration `0008_tool_call`
  creates it (FKs to `investigation` + `investigation_stage`).
- Env vars: `AI_SRE_LLM_API_KEY` must be set in any env that calls a real
  provider; with no key the worker builds no gateway (the 0007/0008 stub stages
  don't call it) and tests use `FakeLLMProvider`.
- Observability: the gateway records tokens/cost on the budget and the
  dispatcher records `tool_call` rows. Prometheus counters + OTel spans are
  deferred to spec 0017 (no `/metrics` scaffolding yet), consistent with
  specs 0005–0007.

## Definition of done

- [ ] Scope complete.
- [ ] Against a real Anthropic key, a `chat()` returns a response and the budget reflects token usage.
- [ ] `tool_loop()` with a stub tool runs an end-to-end loop with two iterations.

## Follow-ups

- OpenAI / Bedrock providers.
- Prompt-prefix cache hit-rate metric.
- Per-tenant model override.
