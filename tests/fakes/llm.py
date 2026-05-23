"""FakeLLMProvider: scripted responses for unit tests.

Usage:
    fake = FakeLLMProvider()
    fake.queue_response(LLMResponse(text="...", tool_calls=()))
    gateway = LLMGateway(provider=fake)
    ...
"""

from __future__ import annotations

from collections import deque

# from ai_sre.llm.tools import ToolSpec
from ai_sre.llm.gateway import LLMProvider, LLMResponse, Message, ResponseFormat


class FakeLLMProvider(LLMProvider):
    name = "fake"

    def __init__(self) -> None:
        self._responses: deque[LLMResponse] = deque()
        self.calls: list[dict] = []

    def queue_response(self, response: LLMResponse) -> None:
        self._responses.append(response)

    async def chat(
        self,
        *,
        system: str,
        messages: list[Message],
        tools=None,
        response_format: ResponseFormat | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append({
            "system": system,
            "messages": messages,
            "tools": tools,
            "response_format": response_format,
        })
        if not self._responses:
            return LLMResponse(text="")
        return self._responses.popleft()
