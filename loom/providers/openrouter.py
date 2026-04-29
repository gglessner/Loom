"""OpenRouter (OpenAI-compatible) streaming provider."""

from __future__ import annotations

import json
from threading import Event
from typing import Any, Iterator, Optional

import requests

from ..config import OpenRouterConfig
from .base import Done, Message, StreamEvent, TextDelta, Tool, ToolCall, ToolCallEvent


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, cfg: OpenRouterConfig) -> None:
        self._cfg = cfg
        self.model = cfg.model
        self._session = requests.Session()

    # ----- translation -------------------------------------------------------

    @staticmethod
    def _to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id or "",
                        "name": m.name or "",
                        "content": m.content or "",
                    }
                )
                continue

            entry: dict[str, Any] = {"role": m.role, "content": m.content or ""}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
            out.append(entry)
        return out

    @staticmethod
    def _to_openai_tools(tools: list[Tool]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
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
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = self._to_openai_tools(tools)

        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "HTTP-Referer": "https://github.com/loom-ai/loom",
            "X-Title": "Loom",
        }
        url = f"{self._cfg.base_url.rstrip('/')}/chat/completions"

        # tool_call accumulators keyed by index (OpenAI streams arguments in chunks)
        partials: dict[int, dict[str, Any]] = {}
        finish_reason: Optional[str] = None

        with self._session.post(
            url, json=payload, headers=headers, stream=True, timeout=600
        ) as resp:
            if resp.status_code != 200:
                body = resp.text
                resp.close()
                raise RuntimeError(f"OpenRouter error [{resp.status_code}]: {body}")

            try:
                for raw in resp.iter_lines(decode_unicode=True):
                    if cancel is not None and cancel.is_set():
                        finish_reason = "interrupted"
                        break
                    if not raw:
                        continue
                    if not raw.startswith("data: "):
                        continue
                    chunk = raw[6:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        evt = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue

                    choice = (evt.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}

                    text = delta.get("content")
                    if text:
                        yield TextDelta(text=text)

                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = partials.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]

                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
            finally:
                resp.close()

        for idx in sorted(partials):
            slot = partials[idx]
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": slot["arguments"]}
            yield ToolCallEvent(
                tool_call=ToolCall(
                    id=slot["id"] or f"call_{idx}",
                    name=slot["name"],
                    arguments=args,
                )
            )

        yield Done(stop_reason=finish_reason or "end_turn")
