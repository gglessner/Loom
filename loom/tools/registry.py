"""Tool registry. Builtin tools register themselves here at import time and
external sources (MCP servers) can also be registered through ``ToolRegistry``.

Each tool exposes:
  * ``name`` / ``description``
  * ``input_schema`` (JSON Schema, sent to the model)
  * ``run(args)``: synchronous callable returning a string

Tools should be defensive: catch their own exceptions and return a string
explaining the failure rather than raising. The agent loop already wraps
calls in a try/except, but error messages get fed straight back to the model
so it's worth making them informative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..providers.base import Tool


ToolHandler = Callable[[dict[str, Any]], str]


@dataclass
class BuiltinTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def to_provider_tool(self) -> Tool:
        return Tool(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )


class ToolRegistry:
    """Holds all tools the agent can invoke (builtin + MCP-discovered)."""

    def __init__(self) -> None:
        self._tools: dict[str, BuiltinTool] = {}

    def register(self, tool: BuiltinTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered.")
        self._tools[tool.name] = tool

    def register_many(self, tools: Iterable[BuiltinTool]) -> None:
        for t in tools:
            self.register(t)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> BuiltinTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def provider_tools(self) -> list[Tool]:
        return [t.to_provider_tool() for t in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"[unknown tool] {name}"
        try:
            result = tool.handler(arguments or {})
        except Exception as e:  # pragma: no cover - defensive
            return f"[tool error] {type(e).__name__}: {e}"
        return result if isinstance(result, str) else str(result)


# ----- builtin tool collection ------------------------------------------------


def builtin_tools() -> list[BuiltinTool]:
    """Return every builtin Loom tool. Imported lazily to avoid import cycles."""
    from . import filesystem, search, shell, excel

    return [
        *filesystem.TOOLS,
        *search.TOOLS,
        *shell.TOOLS,
        *excel.TOOLS,
    ]
