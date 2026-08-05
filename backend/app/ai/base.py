"""Provider-agnostic message and streaming types.

Every LLM vendor this app talks to (Ollama, Groq, Gemini, OpenRouter, HF)
speaks the OpenAI chat-completions wire format, so these types map 1:1 onto it.
Nodes and the agent loop depend only on these types, never on a vendor SDK.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class SystemMessage:
    content: str
    role: Literal["system"] = "system"


@dataclass
class UserMessage:
    content: str
    role: Literal["user"] = "user"


@dataclass
class ToolCallRequest:
    """A tool invocation the model asked for (never executed by the model)."""

    id: str
    name: str
    arguments_json: str

    @property
    def arguments(self) -> dict[str, Any]:
        """Parsed arguments; empty dict when the model emitted invalid JSON."""
        try:
            parsed = json.loads(self.arguments_json or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}


@dataclass
class AssistantMessage:
    content: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    role: Literal["assistant"] = "assistant"


@dataclass
class ToolResultMessage:
    tool_call_id: str
    content: str
    role: Literal["tool"] = "tool"


ChatMessage = SystemMessage | UserMessage | AssistantMessage | ToolResultMessage


@dataclass
class ToolDefinition:
    """OpenAI function-calling tool schema."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class TextDelta:
    """One streamed token chunk."""

    text: str


@dataclass
class StreamDone:
    """Terminal stream event: full text, any tool calls, and usage."""

    text: str
    tool_calls: list[ToolCallRequest]
    usage: Usage


StreamEvent = TextDelta | StreamDone


class AIProvider(Protocol):
    """What every chat provider must implement."""

    name: str

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.6,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> Any:  # AsyncIterator[StreamEvent] — Protocol can't express async generators cleanly
        ...
