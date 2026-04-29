"""Streaming agent loop.

Every iteration:
  1. Send the conversation + the available tools to the provider.
  2. Stream the response, printing text deltas as they arrive.
  3. If the assistant emitted any tool calls, execute them locally, append
     ``role=tool`` results to the conversation, and loop.
  4. Otherwise we have a final answer; return.

The loop is fully cancellable via a ``threading.Event``. When set:
  * an in-flight stream is aborted (the provider checks the event between
    chunks and stops yielding),
  * the partial assistant turn is preserved in conversation history with an
    ``[interrupted]`` marker so the user can give follow-up instructions
    that reference what was already produced,
  * the loop returns immediately without scheduling new tool calls.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from threading import Event
from typing import Optional

from .providers import (
    Done,
    Message,
    Provider,
    TextDelta,
    Tool,
    ToolCallEvent,
)
from .tools.registry import ToolRegistry


@dataclass
class AgentResult:
    stop_reason: str  # "end_turn" | "interrupted" | "max_steps"
    steps: int


class Agent:
    def __init__(
        self,
        provider: Provider,
        registry: ToolRegistry,
        *,
        max_tokens: int,
        temperature: float,
        max_steps: int,
        out=sys.stdout,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_steps = max_steps
        self._out = out

    def _provider_tools(self) -> list[Tool]:
        return self._registry.provider_tools()

    def _print(self, s: str = "") -> None:
        try:
            self._out.write(s)
        except UnicodeEncodeError:
            # Older/legacy Windows consoles can't render every codepoint; fall
            # back to a lossy encode rather than crashing mid-stream.
            enc = getattr(self._out, "encoding", None) or "ascii"
            self._out.write(s.encode(enc, errors="replace").decode(enc, errors="replace"))
        self._out.flush()

    def run(self, messages: list[Message], cancel: Optional[Event] = None) -> AgentResult:
        tools = self._provider_tools()

        for step in range(1, self._max_steps + 1):
            text_buf: list[str] = []
            tool_calls = []
            stop_reason = "end_turn"

            self._print("\n")
            try:
                for evt in self._provider.stream(
                    messages,
                    tools,
                    max_tokens=self._max_tokens,
                    temperature=self._temperature,
                    cancel=cancel,
                ):
                    if isinstance(evt, TextDelta):
                        self._print(evt.text)
                        text_buf.append(evt.text)
                    elif isinstance(evt, ToolCallEvent):
                        tool_calls.append(evt.tool_call)
                    elif isinstance(evt, Done):
                        stop_reason = evt.stop_reason
            except KeyboardInterrupt:
                stop_reason = "interrupted"

            if cancel is not None and cancel.is_set():
                stop_reason = "interrupted"

            self._print("\n")

            assistant = Message(
                role="assistant",
                content="".join(text_buf) or None,
                tool_calls=list(tool_calls),
            )
            messages.append(assistant)

            if stop_reason == "interrupted":
                self._print("[interrupted]\n")
                # Note in history so the model knows it was cut off.
                if assistant.content is None:
                    assistant.content = "[interrupted by user]"
                else:
                    assistant.content += "\n[interrupted by user]"
                return AgentResult(stop_reason="interrupted", steps=step)

            if not tool_calls:
                return AgentResult(stop_reason=stop_reason, steps=step)

            # Execute every tool call, append results, loop.
            for call in tool_calls:
                pretty = _pretty_args(call.arguments)
                self._print(f"[tool] {call.name}({pretty})\n")
                if cancel is not None and cancel.is_set():
                    result = "[skipped: interrupted]"
                else:
                    try:
                        result = self._registry.execute(call.name, call.arguments)
                    except KeyboardInterrupt:
                        result = "[tool interrupted]"
                        if cancel is not None:
                            cancel.set()
                preview = result if len(result) <= 300 else result[:300] + "..."
                self._print(f"  -> {preview}\n")
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        name=call.name,
                        content=result,
                    )
                )

            if cancel is not None and cancel.is_set():
                return AgentResult(stop_reason="interrupted", steps=step)

        self._print(f"\n[max agent steps reached: {self._max_steps}]\n")
        return AgentResult(stop_reason="max_steps", steps=self._max_steps)


def _pretty_args(args: dict) -> str:
    try:
        s = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(args)
    return s if len(s) <= 200 else s[:200] + "..."
