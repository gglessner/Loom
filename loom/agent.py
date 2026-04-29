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

from .colors import COLOR
from .providers import (
    Done,
    Message,
    Provider,
    TextDelta,
    Tool,
    ToolCallEvent,
)
from .tools.registry import ToolRegistry
from .wrapping import StreamWrapper, detect_terminal_width, resolve_wrap_width


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
        wrap: str = "off",
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_steps = max_steps
        self._out = out
        self._wrap_setting = wrap

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

            # On step 2+ a leading newline separates this assistant turn from
            # the previous tool output. On step 1 the user just hit Enter, so
            # we skip it - otherwise we'd insert an awkward blank line right
            # under their prompt.
            if step > 1:
                self._print("\n")

            # Snapshot the terminal width per turn so window resizes between
            # turns are honoured. Wrapping is bypassed (transparent
            # pass-through) when disabled or below the min-width threshold.
            wrap_width = resolve_wrap_width(
                self._wrap_setting, terminal_width=detect_terminal_width()
            )
            wrapper = StreamWrapper(self._print, wrap_width)

            try:
                for evt in self._provider.stream(
                    messages,
                    tools,
                    max_tokens=self._max_tokens,
                    temperature=self._temperature,
                    cancel=cancel,
                ):
                    if isinstance(evt, TextDelta):
                        wrapper.feed(evt.text)
                        text_buf.append(evt.text)
                    elif isinstance(evt, ToolCallEvent):
                        tool_calls.append(evt.tool_call)
                    elif isinstance(evt, Done):
                        stop_reason = evt.stop_reason
            except KeyboardInterrupt:
                stop_reason = "interrupted"
            finally:
                # Flush any buffered partial word so the model's last
                # characters always reach the screen, even on interrupt.
                wrapper.flush()

            if cancel is not None and cancel.is_set():
                stop_reason = "interrupted"

            # Ensure the LLM's text ends with exactly one newline, then add a
            # blank line of separation so the next prompt / [tool] line / next
            # turn isn't visually glued to the model's output. If the model
            # emitted no text at all (tool-only turn), the leading "\n" at the
            # top of this loop already gave us breathing room.
            if text_buf:
                if not "".join(text_buf).endswith("\n"):
                    self._print("\n")
                self._print("\n")
            else:
                self._print("\n")

            assistant = Message(
                role="assistant",
                content="".join(text_buf) or None,
                tool_calls=list(tool_calls),
            )
            messages.append(assistant)

            if stop_reason == "interrupted":
                self._print(COLOR.warning("[interrupted]") + "\n")
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
                tool_line = (
                    COLOR.tool("[tool] ")
                    + COLOR.bold(call.name)
                    + COLOR.dim(f"({pretty})")
                )
                self._print(tool_line + "\n")
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
                self._print(COLOR.dim(f"  -> {preview}") + "\n")
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

        self._print("\n" + COLOR.warning(f"[max agent steps reached: {self._max_steps}]") + "\n")
        return AgentResult(stop_reason="max_steps", steps=self._max_steps)


def _pretty_args(args: dict) -> str:
    try:
        s = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(args)
    return s if len(s) <= 200 else s[:200] + "..."
