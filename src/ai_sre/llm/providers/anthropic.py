"""AnthropicProvider — maps the gateway's neutral types to the Anthropic SDK.

Responsibilities:
    * Translate our ``Message`` / ``ToolSpec`` into Anthropic's request shape
      (tool_use / tool_result content blocks).
    * Enable prompt-prefix caching on the (large, stable) system prompt.
    * Parse text + tool_use blocks + token usage out of the response.
    * Map transient API failures (429 / 5xx / connection) to
      ``LLMTransientError`` so the gateway's retry kicks in.

Not unit-tested (tests use ``FakeLLMProvider``); exercised against a real key
per the spec DoD. Pricing is best-effort and configurable later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import anthropic

from ai_sre.exceptions import LLMError, LLMTransientError
from ai_sre.llm.gateway import LLMProvider, LLMResponse, Message, ResponseFormat
from ai_sre.utils.logging import get_logger

if TYPE_CHECKING:
    from ai_sre.llm.tools import ToolSpec

logger = get_logger(__name__)

# USD per 1M tokens (input, output). Best-effort; matched by model-name prefix
# with a conservative fallback. Real pricing is a deploy-time concern.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.80, 4.0),
}
_FALLBACK_PRICE: tuple[float, float] = (15.0, 75.0)


def _price_for(model: str) -> tuple[float, float]:
    for prefix, price in _PRICES.items():
        if model.startswith(prefix):
            return price
    return _FALLBACK_PRICE


class AnthropicProvider(LLMProvider):
    """LLMProvider backed by the Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._price_in, self._price_out = _price_for(model)

    async def chat(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        response_format: ResponseFormat | None,
        max_tokens: int,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            # List form + cache_control enables prompt-prefix caching.
            "system": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": self._to_anthropic_messages(messages),
        }
        if tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in tools
            ]

        try:
            resp = await self._client.messages.create(**kwargs)
        except (
            anthropic.RateLimitError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
            anthropic.InternalServerError,
        ) as exc:
            raise LLMTransientError(f"Anthropic transient error: {exc}") from exc
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic API error: {exc}") from exc

        return self._parse_response(resp)

    def estimate_cost(self, tokens: int) -> float:
        """Coarse cost estimate when the input/output split isn't known."""
        return tokens / 1_000_000 * self._price_out

    # ---- translation ----

    def _to_anthropic_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_use_id,
                                "content": msg.content,
                            }
                        ],
                    }
                )
            elif msg.role == "assistant" and msg.tool_use:
                content: list[dict[str, Any]] = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for call in msg.tool_use.get("tool_calls", []):
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call.get("id"),
                            "name": call.get("name"),
                            "input": call.get("input", {}),
                        }
                    )
                out.append({"role": "assistant", "content": content})
            else:
                out.append({"role": msg.role, "content": msg.content})
        return out

    def _parse_response(self, resp: Any) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    {"id": block.id, "name": block.name, "input": block.input}
                )

        usage = resp.usage
        in_tokens = getattr(usage, "input_tokens", 0) or 0
        out_tokens = getattr(usage, "output_tokens", 0) or 0
        cost = (
            in_tokens / 1_000_000 * self._price_in
            + out_tokens / 1_000_000 * self._price_out
        )
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            tokens_used=in_tokens + out_tokens,
            cost_usd=cost,
            stop_reason=resp.stop_reason or "",
        )
