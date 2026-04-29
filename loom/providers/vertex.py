"""Vertex AI provider using the official `anthropic[vertex]` SDK.

Authentication: HashiCorp Vault hands us a short-lived Google OAuth access
token. We wrap it in a ``google.oauth2.credentials.Credentials`` object and
hand that to ``AnthropicVertex``. Before each request we ask the VaultClient
for a fresh token; if the cached one is still valid VaultClient returns it,
otherwise it re-fetches from Vault.
"""

from __future__ import annotations

import json
from threading import Event
from typing import Any, Iterator, Optional

from anthropic import AnthropicVertex
from google.oauth2.credentials import Credentials

from ..config import VertexConfig
from ..vault import VaultClient
from .base import Done, Message, StreamEvent, TextDelta, Tool, ToolCall, ToolCallEvent


class VertexProvider:
    name = "vertex"

    def __init__(self, cfg: VertexConfig, *, vault: VaultClient) -> None:
        self._cfg = cfg
        self._vault = vault
        self.model = cfg.model
        self._client: Optional[AnthropicVertex] = None
        self._client_token: Optional[str] = None

    # ----- credential management --------------------------------------------

    def _get_client(self) -> AnthropicVertex:
        token = self._vault.get_gcp_access_token()
        if self._client is None or token != self._client_token:
            creds = Credentials(token=token)
            self._client = AnthropicVertex(
                project_id=self._cfg.project_id,
                region=self._cfg.region,
                credentials=creds,
            )
            self._client_token = token
        return self._client

    # ----- translation -------------------------------------------------------

    @staticmethod
    def _split_system(messages: list[Message]) -> tuple[str, list[Message]]:
        system_parts: list[str] = []
        rest: list[Message] = []
        for m in messages:
            if m.role == "system":
                if m.content:
                    system_parts.append(m.content)
            else:
                rest.append(m)
        return "\n\n".join(system_parts), rest

    @staticmethod
    def _to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
        """Convert internal messages to Anthropic Messages API format.

        Tool results in the Anthropic API ride on a ``user`` turn as a list of
        ``tool_result`` content blocks. We coalesce consecutive tool messages
        into the next user turn (creating a synthetic one if needed).
        """
        out: list[dict[str, Any]] = []
        pending_tool_blocks: list[dict[str, Any]] = []

        def flush_tool_results() -> None:
            if pending_tool_blocks:
                out.append({"role": "user", "content": list(pending_tool_blocks)})
                pending_tool_blocks.clear()

        for m in messages:
            if m.role == "tool":
                pending_tool_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id or "",
                        "content": m.content or "",
                    }
                )
                continue

            flush_tool_results()

            if m.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                if not blocks:
                    blocks.append({"type": "text", "text": ""})
                out.append({"role": "assistant", "content": blocks})
            else:
                # user
                out.append({"role": "user", "content": m.content or ""})

        flush_tool_results()
        return out

    @staticmethod
    def _to_anthropic_tools(tools: list[Tool]) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

    # ----- streaming ---------------------------------------------------------

    def stream(
        self,
        messages: list[Message],
        tools: list[Tool],
        *,
        max_tokens: int,
        temperature: float,
        cancel: Optional[Event] = None,
    ) -> Iterator[StreamEvent]:
        client = self._get_client()
        system, convo = self._split_system(messages)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": self._to_anthropic_messages(convo),
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._to_anthropic_tools(tools)

        # Tool-use blocks come as: content_block_start (with partial input),
        # then a sequence of input_json_delta events, then content_block_stop.
        active_tool: dict[int, dict[str, Any]] = {}
        finish_reason: Optional[str] = None

        with client.messages.stream(**kwargs) as stream:
            try:
                for event in stream:
                    if cancel is not None and cancel.is_set():
                        finish_reason = "interrupted"
                        break

                    et = getattr(event, "type", None)

                    if et == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if block is not None and getattr(block, "type", None) == "tool_use":
                            active_tool[event.index] = {
                                "id": getattr(block, "id", ""),
                                "name": getattr(block, "name", ""),
                                "input_json": "",
                            }

                    elif et == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta is None:
                            continue
                        dt = getattr(delta, "type", None)
                        if dt == "text_delta":
                            text = getattr(delta, "text", "")
                            if text:
                                yield TextDelta(text=text)
                        elif dt == "input_json_delta":
                            slot = active_tool.get(event.index)
                            if slot is not None:
                                slot["input_json"] += getattr(delta, "partial_json", "")

                    elif et == "content_block_stop":
                        slot = active_tool.pop(event.index, None)
                        if slot is not None:
                            try:
                                args = (
                                    json.loads(slot["input_json"])
                                    if slot["input_json"]
                                    else {}
                                )
                            except json.JSONDecodeError:
                                args = {"_raw": slot["input_json"]}
                            yield ToolCallEvent(
                                tool_call=ToolCall(
                                    id=slot["id"], name=slot["name"], arguments=args
                                )
                            )

                    elif et == "message_delta":
                        delta = getattr(event, "delta", None)
                        if delta is not None:
                            sr = getattr(delta, "stop_reason", None)
                            if sr:
                                finish_reason = sr

                    elif et == "message_stop":
                        break
            finally:
                # Closing the context manager aborts the underlying HTTP stream
                # if we exited early (e.g. cancellation).
                pass

        yield Done(stop_reason=finish_reason or "end_turn")
