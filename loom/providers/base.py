"""Provider-agnostic message, tool, and streaming-event types.

The agent loop and the CLI deal exclusively with these types. Each provider
is responsible for translating to/from its native wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from typing import Any, Iterator, Optional, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    """A single conversation turn in Loom's internal representation."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None  # role == "tool"
    name: Optional[str] = None  # role == "tool"


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]


# ----- streaming events ------------------------------------------------------


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCallEvent:
    """Emitted when a complete tool call has been parsed from the stream."""

    tool_call: ToolCall


@dataclass
class Done:
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | "interrupted" | ...


StreamEvent = TextDelta | ToolCallEvent | Done


# ----- provider interface ----------------------------------------------------


class Provider(Protocol):
    name: str  # "vertex" | "openrouter"
    model: str

    def stream(
        self,
        messages: list[Message],
        tools: list[Tool],
        *,
        max_tokens: int,
        temperature: float,
        cancel: Optional[Event] = None,
    ) -> Iterator[StreamEvent]:
        """Stream the next assistant turn.

        ``cancel`` is an optional Event that, when set, instructs the provider
        to abort the in-flight HTTP request and return promptly. Providers
        emit a final ``Done`` event regardless of how the stream ended.
        """


def build_provider(cfg) -> Provider:
    """Construct the Provider configured by a LoomConfig.

    Imported lazily so users without (e.g.) the anthropic SDK can still run
    OpenRouter, and vice versa.
    """
    if cfg.provider == "openrouter":
        from .openrouter import OpenRouterProvider

        return OpenRouterProvider(cfg.openrouter)
    if cfg.provider == "vertex":
        from .vertex import VertexProvider
        from ..vault import VaultClient

        vault = VaultClient(cfg.vault)
        return VertexProvider(cfg.vertex, vault=vault)
    raise ValueError(f"Unknown provider: {cfg.provider!r}")
