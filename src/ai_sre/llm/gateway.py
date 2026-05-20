"""LLM Gateway.

Single entry point for all LLM calls. Two methods:

    * `chat(...)`         — one-shot, optionally structured output.
    * `tool_loop(...)`    — agentic loop; gateway dispatches tool calls.

Provider abstraction:

    LLMProvider (ABC)
       └── AnthropicProvider     (concrete; MVP)
       └── (future) OpenAIProvider, BedrockProvider, ...

The provider is selected via `settings.llm_provider`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_sre.core.investigation.budget import Budget
    from ai_sre.core.investigation.context import InvestigationContext
    from ai_sre.llm.tools import ToolDispatcher, ToolSpec


@dataclass
class Message:
    role: str           # "system" | "user" | "assistant" | "tool"
    content: str
    tool_use: dict[str, Any] | None = None
    tool_use_id: str | None = None


@dataclass
class LLMResponse:
    text: str
    structured: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0
    stop_reason: str = ""


@dataclass(frozen=True)
class ResponseFormat:
    """Constrain the LLM to a JSON schema."""

    json_schema: dict[str, Any]


class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        response_format: ResponseFormat | None,
        max_tokens: int,
    ) -> LLMResponse: ...

    @abstractmethod
    def estimate_cost(self, tokens: int) -> float: ...


class LLMGateway:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def chat(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_format: ResponseFormat | None = None,
        budget: Budget,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        budget.assert_can_call_llm(expected_tokens=max_tokens)
        resp = await self.provider.chat(
            system=system,
            messages=messages,
            tools=tools,
            response_format=response_format,
            max_tokens=max_tokens,
        )
        budget.record_llm_call(tokens=resp.tokens_used, cost_usd=resp.cost_usd)
        return resp

    async def tool_loop(
        self,
        *,
        system: str,
        user: str,
        tools: list[ToolSpec],
        tool_dispatcher: ToolDispatcher,
        ctx: InvestigationContext,
        budget: Budget,
        max_iterations: int = 10,
    ) -> LLMResponse:
        """Agentic loop. The LLM may request tool calls; we dispatch them
        and feed results back until the model emits a final response or we
        exhaust iterations / budget.
        """
        # TODO(spec-NNNN: llm-gateway):
        #   messages = [Message(role="user", content=user)]
        #   for _ in range(max_iterations):
        #       budget.assert_can_call_llm()
        #       resp = await self.chat(system=system, messages=messages, tools=tools, budget=budget)
        #       if not resp.tool_calls:
        #           return resp
        #       for tc in resp.tool_calls:
        #           budget.assert_can_call_tool()
        #           result = await tool_dispatcher.dispatch(tc, ctx)
        #           budget.record_tool_call()
        #           messages.append(... assistant tool_use ...)
        #           messages.append(... tool result ...)
        #   return resp  # iteration cap hit
        raise NotImplementedError
