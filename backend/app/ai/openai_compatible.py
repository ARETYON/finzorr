"""The one provider class for every OpenAI-wire-compatible vendor.

Ollama (`{OLLAMA_URL}/v1`), Groq, Gemini's OpenAI endpoint, OpenRouter and
HF Inference Providers all differ only by base_url + api_key + model names,
so a single class covers all of them — adding a vendor is registry config,
never a new code path.
"""

from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from app.ai.base import (
    AssistantMessage,
    ChatMessage,
    StreamDone,
    StreamEvent,
    SystemMessage,
    TextDelta,
    ToolCallRequest,
    ToolDefinition,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def _to_wire(msg: ChatMessage) -> dict[str, Any]:
    """Serialize an internal message to the OpenAI wire format."""
    if isinstance(msg, SystemMessage | UserMessage):
        return {"role": msg.role, "content": msg.content}
    if isinstance(msg, AssistantMessage):
        wire: dict[str, Any] = {"role": "assistant", "content": msg.content or None}
        if msg.tool_calls:
            wire["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments_json},
                }
                for tc in msg.tool_calls
            ]
        return wire
    if isinstance(msg, ToolResultMessage):
        return {"role": "tool", "tool_call_id": msg.tool_call_id, "content": msg.content}
    raise TypeError(f"unknown message type: {type(msg)!r}")


class OpenAICompatibleProvider:
    """Streams chat completions (with tool-calling) from any OpenAI-style API."""

    def __init__(self, name: str, base_url: str, api_key: str) -> None:
        self.name = name
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "unused")

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.6,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield TextDelta events, then exactly one StreamDone.

        Tool-call argument deltas are accumulated internally and surfaced only
        in the terminal StreamDone (they are not human-visible mid-stream).
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [_to_wire(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = [t.to_openai() for t in tools]
        if response_format:
            kwargs["response_format"] = response_format

        text_parts: list[str] = []
        # index -> partial tool call accumulation
        partial: dict[int, dict[str, str]] = {}
        usage = Usage()

        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if chunk.usage is not None:
                usage = Usage(
                    prompt_tokens=chunk.usage.prompt_tokens or 0,
                    completion_tokens=chunk.usage.completion_tokens or 0,
                )
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                text_parts.append(delta.content)
                yield TextDelta(text=delta.content)
            for tc in delta.tool_calls or []:
                slot = partial.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function is not None:
                    if tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function.arguments:
                        slot["args"] += tc.function.arguments

        tool_calls = [
            ToolCallRequest(
                id=slot["id"] or f"call_{i}", name=slot["name"], arguments_json=slot["args"]
            )
            for i, slot in sorted(partial.items())
            if slot["name"]
        ]
        yield StreamDone(text="".join(text_parts), tool_calls=tool_calls, usage=usage)
