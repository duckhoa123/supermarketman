"""Thin wrapper over the Claude API so the agent loop can be tested with a scripted fake."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from app.agent.pricing import Usage
from app.config import get_settings


@dataclass
class LLMResult:
    content: list[dict]  # {"type": "text", "text"} | {"type": "tool_use", "id", "name", "input"}
    stop_reason: str
    usage: Usage

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[dict]:
        return [b for b in self.content if b.get("type") == "tool_use"]


class LLM(Protocol):
    def stream(
        self, *, model: str, system: list[dict], tools: list[dict], messages: list[dict], max_tokens: int,
        tool_choice: dict | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield ("text", delta) pieces, then exactly one ("final", LLMResult)."""
        ...

    async def complete(
        self, *, model: str, system: list[dict], messages: list[dict], max_tokens: int, tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ) -> LLMResult: ...


class LLMNotConfigured(Exception):
    pass


def _to_result(msg) -> LLMResult:
    blocks: list[dict] = []
    for b in msg.content:
        if b.type == "text":
            blocks.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    u = msg.usage
    usage = Usage(
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
    )
    return LLMResult(content=blocks, stop_reason=msg.stop_reason or "end_turn", usage=usage)


class AnthropicLLM:
    def __init__(self, api_key: str | None = None):
        key = api_key or get_settings().anthropic_api_key
        if not key:
            raise LLMNotConfigured("ANTHROPIC_API_KEY is not set")
        from anthropic import AsyncAnthropic

        self.client = AsyncAnthropic(api_key=key, max_retries=3)

    async def stream(self, *, model, system, tools, messages, max_tokens, tool_choice=None):
        kwargs: dict = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        async with self.client.messages.stream(**kwargs) as s:
            async for text in s.text_stream:
                yield ("text", text)
            final = await s.get_final_message()
        yield ("final", _to_result(final))

    async def complete(self, *, model, system, messages, max_tokens, tools=None, tool_choice=None):
        kwargs: dict = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        return _to_result(await self.client.messages.create(**kwargs))


_override: LLM | None = None


def get_llm() -> LLM:
    return _override or AnthropicLLM()


def set_llm(llm: LLM | None) -> None:
    """Tests install a fake here."""
    global _override
    _override = llm
