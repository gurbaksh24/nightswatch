"""LLM Gateway — the single entry point for all LLM calls.

Two call shapes:

    * ``chat(...)``       — one-shot; optional structured (JSON) output.
    * ``tool_loop(...)``  — agentic loop; the gateway dispatches the model's
                            tool calls and feeds results back until the model
                            stops, iterations run out, or the budget is spent.

Cross-cutting concerns handled here so they're not scattered:

    * **Budget**: every call is gated + recorded against the investigation's
      :class:`Budget` (NFR-6.1).
    * **Retry**: transient provider failures (``LLMTransientError``) are
      retried with exponential backoff via ``tenacity``.
    * **Structured output**: when a ``ResponseFormat`` is set, the response
      text is parsed as JSON; one corrective re-prompt is attempted before
      giving up with ``LLMResponseInvalid``.

Provider abstraction: ``LLMProvider`` ABC, with ``AnthropicProvider`` the only
concrete impl in MVP (``llm/providers/anthropic.py``).

See LLD sections 11-12 and spec 0008.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ai_sre.exceptions import BudgetExhausted, LLMResponseInvalid, LLMTransientError
from ai_sre.llm.prompts import PROMPT_VERSION as _DEFAULT_PROMPT_VERSION

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
    """Constrain the LLM to a JSON schema (parsed/validated by the gateway)."""

    json_schema: dict[str, Any]


class LLMProvider(ABC):
    """One provider = one external LLM API. Stateless w.r.t. budget."""

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


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Return the parsed JSON object, or ``None`` if it isn't a JSON object."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


class LLMGateway:
    """Wraps a provider with budget accounting, retry, and structured output."""

    def __init__(
        self,
        provider: LLMProvider,
        prompt_version: str = _DEFAULT_PROMPT_VERSION,
        *,
        max_attempts: int = 3,
    ) -> None:
        self.provider = provider
        self.prompt_version = prompt_version
        self._max_attempts = max_attempts

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
        """One-shot call. Enforces + records budget; coerces JSON when a
        ``response_format`` is requested."""
        budget.assert_can_call_llm(expected_tokens=max_tokens)
        resp = await self._chat_with_retry(
            system=system,
            messages=messages,
            tools=tools,
            response_format=response_format,
            max_tokens=max_tokens,
        )
        budget.record_llm_call(tokens=resp.tokens_used, cost_usd=resp.cost_usd)

        if response_format is not None:
            resp = await self._coerce_structured(
                resp,
                system=system,
                messages=messages,
                tools=tools,
                response_format=response_format,
                budget=budget,
                max_tokens=max_tokens,
            )
        return resp

    async def tool_loop(
        self,
        *,
        system: str,
        user: str,
        tools: list[ToolSpec],
        dispatcher: ToolDispatcher,
        ctx: InvestigationContext,
        budget: Budget,
        max_iterations: int = 10,
    ) -> LLMResponse:
        """Drive the model: it asks for tools, we dispatch them, repeat.

        Terminates when the model stops asking for tools (``end_turn``), when
        ``max_iterations`` is hit, or when the budget is exhausted — returning
        whatever the last response was in every case.
        """
        messages: list[Message] = [Message(role="user", content=user)]
        resp = LLMResponse(text="")
        try:
            for _ in range(max_iterations):
                resp = await self.chat(
                    system=system, messages=messages, tools=tools, budget=budget
                )
                if not resp.tool_calls:
                    return resp
                # Record the assistant's tool-call turn, then each result.
                messages.append(
                    Message(
                        role="assistant",
                        content=resp.text,
                        tool_use={"tool_calls": resp.tool_calls},
                    )
                )
                for call in resp.tool_calls:
                    budget.assert_can_call_tool()
                    result = await dispatcher.dispatch(
                        call.get("name", ""), call.get("input", {}), ctx
                    )
                    budget.record_tool_call()
                    messages.append(
                        Message(
                            role="tool",
                            content=json.dumps(result, default=str),
                            tool_use_id=call.get("id"),
                        )
                    )
        except BudgetExhausted:
            # Out of budget mid-loop — partial results are still useful.
            pass
        return resp

    # ---- internals ----

    async def _chat_with_retry(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        response_format: ResponseFormat | None,
        max_tokens: int,
    ) -> LLMResponse:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(LLMTransientError),
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=0.5, max=8),
            reraise=True,
        ):
            with attempt:
                return await self.provider.chat(
                    system=system,
                    messages=messages,
                    tools=tools,
                    response_format=response_format,
                    max_tokens=max_tokens,
                )
        raise AssertionError("unreachable: AsyncRetrying with reraise=True")

    async def _coerce_structured(
        self,
        resp: LLMResponse,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        response_format: ResponseFormat,
        budget: Budget,
        max_tokens: int,
    ) -> LLMResponse:
        parsed = _try_parse_json(resp.text)
        if parsed is not None:
            resp.structured = parsed
            return resp

        # One corrective re-prompt before giving up.
        corrective = [
            *messages,
            Message(role="assistant", content=resp.text),
            Message(
                role="user",
                content=(
                    "Your previous reply was not a single valid JSON object. "
                    "Respond with ONLY the JSON object, no prose or code fences."
                ),
            ),
        ]
        budget.assert_can_call_llm(expected_tokens=max_tokens)
        retry_resp = await self._chat_with_retry(
            system=system,
            messages=corrective,
            tools=tools,
            response_format=response_format,
            max_tokens=max_tokens,
        )
        budget.record_llm_call(
            tokens=retry_resp.tokens_used, cost_usd=retry_resp.cost_usd
        )
        parsed_retry = _try_parse_json(retry_resp.text)
        if parsed_retry is None:
            raise LLMResponseInvalid(
                "Model did not return valid JSON after a corrective re-prompt."
            )
        retry_resp.structured = parsed_retry
        return retry_resp
