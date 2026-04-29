"""Real MCP support via the official ``mcp`` Python SDK.

We run a single asyncio event loop on a dedicated background thread and keep
each configured stdio MCP server alive for the lifetime of the Loom session.
The synchronous agent loop schedules ``call_tool`` coroutines onto this loop
via ``run_coroutine_threadsafe``, which gives us:

  * one persistent process per MCP server (no per-call spawn cost),
  * thread-safe access from the synchronous CLI,
  * a clean shutdown path that closes sessions and the loop.

If the ``mcp`` SDK or a server fails to start we print a warning and continue
without that server - Loom is fully functional with just builtin tools.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import AsyncExitStack
from typing import Any, Optional

from .config import MCPServerConfig
from .tools.registry import BuiltinTool, ToolRegistry


log = logging.getLogger("loom.mcp")


class MCPRuntime:
    """Owns the background asyncio loop + every connected MCP session."""

    def __init__(self, servers: list[MCPServerConfig]) -> None:
        self._servers = servers
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._sessions: dict[str, Any] = {}  # name -> ClientSession
        self._stack: Optional[AsyncExitStack] = None
        self._started = threading.Event()
        self._start_error: Optional[BaseException] = None

    # ----- public API --------------------------------------------------------

    def start_and_register(self, registry: ToolRegistry) -> list[str]:
        """Spin up the loop, connect to every server, register their tools.

        Returns the list of server names that connected successfully.
        """
        if not self._servers:
            return []

        self._thread = threading.Thread(
            target=self._run_loop, name="loom-mcp", daemon=True
        )
        self._thread.start()
        self._started.wait()
        if self._start_error is not None:
            raise self._start_error

        connected: list[str] = []
        for server in self._servers:
            try:
                tools = self._submit(self._connect(server)).result(timeout=30)
            except Exception as e:
                log.warning("MCP server %r failed to start: %s", server.name, e)
                print(f"[warn] MCP server {server.name!r} failed to start: {e}")
                continue
            connected.append(server.name)
            for tool in tools:
                registry.register(self._wrap_tool(server.name, tool))
        return connected

    def stop(self) -> None:
        if self._loop is None:
            return
        try:
            self._submit(self._shutdown()).result(timeout=10)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ----- internals ---------------------------------------------------------

    def _submit(self, coro):
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _run_loop(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._stack = AsyncExitStack()
        except BaseException as e:  # pragma: no cover - defensive
            self._start_error = e
        finally:
            self._started.set()
        if self._loop is not None:
            self._loop.run_forever()
            try:
                self._loop.run_until_complete(self._stack.aclose() if self._stack else asyncio.sleep(0))
            except Exception:
                pass
            self._loop.close()

    async def _connect(self, server: MCPServerConfig) -> list[Any]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=server.command, args=server.args, env=server.env or None
        )
        assert self._stack is not None
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        listing = await session.list_tools()
        self._sessions[server.name] = session
        return list(getattr(listing, "tools", []) or [])

    async def _shutdown(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._sessions.clear()

    def _wrap_tool(self, server_name: str, mcp_tool: Any) -> BuiltinTool:
        """Adapt an MCP tool descriptor to a Loom BuiltinTool."""
        name = getattr(mcp_tool, "name", "") or "tool"
        description = getattr(mcp_tool, "description", "") or ""
        schema = getattr(mcp_tool, "inputSchema", None) or {"type": "object", "properties": {}}

        # Namespace MCP tool names so they can't collide with builtins.
        loom_name = f"mcp__{server_name}__{name}"

        async def _call(args: dict[str, Any]) -> str:
            session = self._sessions.get(server_name)
            if session is None:
                return f"[mcp error] server {server_name!r} not connected"
            result = await session.call_tool(name, args)
            return _format_mcp_result(result)

        def handler(args: dict[str, Any]) -> str:
            future = self._submit(_call(args))
            try:
                return future.result(timeout=120)
            except Exception as e:
                return f"[mcp error] {type(e).__name__}: {e}"

        return BuiltinTool(
            name=loom_name,
            description=f"[{server_name}] {description}".strip(),
            input_schema=schema,
            handler=handler,
        )


def _format_mcp_result(result: Any) -> str:
    """MCP tool results are a list of content blocks; flatten them to text."""
    parts: list[str] = []
    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
            continue
        data = getattr(block, "data", None)
        if data is not None:
            parts.append(f"[binary data: {len(data)} bytes]")
            continue
        parts.append(str(block))
    if getattr(result, "isError", False):
        parts.insert(0, "[mcp tool reported error]")
    return "\n".join(parts) if parts else "(no content)"
