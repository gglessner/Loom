"""Provider translation + agent loop tests using a stub Provider (no network)."""

from __future__ import annotations

import io
from typing import Iterator, Optional
from threading import Event

import pytest

from loom.agent import Agent
from loom.providers.base import (
    Done,
    Message,
    StreamEvent,
    TextDelta,
    Tool,
    ToolCall,
    ToolCallEvent,
)
from loom.providers.openrouter import OpenRouterProvider
from loom.providers.vertex import VertexProvider
from loom.config import OpenRouterConfig, VertexConfig
from loom.tools.registry import BuiltinTool, ToolRegistry


# ----- translation ----------------------------------------------------------


def _orp() -> OpenRouterProvider:
    return OpenRouterProvider(OpenRouterConfig(api_key="x"))


def test_openrouter_message_translation() -> None:
    msgs = [
        Message(role="system", content="sys"),
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="c1", name="t", arguments={"a": 1})],
        ),
        Message(role="tool", tool_call_id="c1", name="t", content="ok"),
    ]
    out = _orp()._to_openai_messages(msgs)
    assert out[0]["role"] == "system"
    assert out[2]["tool_calls"][0]["function"]["name"] == "t"
    assert out[3]["role"] == "tool" and out[3]["tool_call_id"] == "c1"


def test_vertex_message_translation_coalesces_tool_results() -> None:
    msgs = [
        Message(role="system", content="sys"),
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="thinking",
            tool_calls=[ToolCall(id="c1", name="t", arguments={"x": 1})],
        ),
        Message(role="tool", tool_call_id="c1", name="t", content="result"),
    ]
    system, rest = VertexProvider._split_system(msgs)
    assert system == "sys"
    out = VertexProvider._to_anthropic_messages(rest)
    assert out[0] == {"role": "user", "content": "hi"}
    assistant_blocks = out[1]["content"]
    assert assistant_blocks[0]["type"] == "text"
    assert assistant_blocks[1]["type"] == "tool_use"
    assert out[2]["role"] == "user"
    assert out[2]["content"][0]["type"] == "tool_result"
    assert out[2]["content"][0]["tool_use_id"] == "c1"


def test_vertex_tool_translation() -> None:
    tools = [Tool(name="t", description="d", input_schema={"type": "object", "properties": {}})]
    out = VertexProvider._to_anthropic_tools(tools)
    assert out == [{"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}]


def test_openrouter_tool_translation() -> None:
    tools = [Tool(name="t", description="d", input_schema={"type": "object", "properties": {}})]
    out = _orp()._to_openai_tools(tools)
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "t"


def test_openrouter_propagates_verify_flag() -> None:
    p = OpenRouterProvider(OpenRouterConfig(api_key="x"), verify=False)
    assert p._session.verify is False
    p2 = OpenRouterProvider(OpenRouterConfig(api_key="x"), verify="/etc/ssl/corp.pem")
    assert p2._session.verify == "/etc/ssl/corp.pem"


# ----- Vertex auth-retry ----------------------------------------------------


class _FakeStreamCM:
    """Stand-in for anthropic SDK's stream context manager."""

    def __init__(self, events=None, raise_on_enter=None):
        self._events = events or []
        self._raise_on_enter = raise_on_enter
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        if self._raise_on_enter is not None:
            raise self._raise_on_enter
        return iter(self._events)

    def __exit__(self, exc_type, exc, tb):
        self.exited = True
        return False


class _FakeMessages:
    def __init__(self, cms):
        self._cms = list(cms)
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return self._cms.pop(0)


class _FakeAnthropicClient:
    def __init__(self, cms):
        self.messages = _FakeMessages(cms)


class _FakeVault:
    def __init__(self):
        self.tokens = ["stale-token", "fresh-token"]
        self.force_refresh_count = 0
        self.calls = 0

    def get_gcp_access_token(self, *, force_refresh: bool = False) -> str:
        self.calls += 1
        if force_refresh:
            self.force_refresh_count += 1
            return self.tokens[1]
        return self.tokens[0]


def _make_vertex_with_fakes(stream_cms, vault) -> VertexProvider:
    """Build a VertexProvider that bypasses real AnthropicVertex/vault."""
    p = VertexProvider.__new__(VertexProvider)
    p._cfg = VertexConfig(project_id="proj", region="us-east5", model="claude-opus-4-6")
    p._vault = vault
    p._verify = True
    p.model = p._cfg.model
    p._client = None
    p._client_token = None

    fake_clients = [_FakeAnthropicClient([cm]) for cm in stream_cms]
    fake_clients_iter = iter(fake_clients)

    def fake_get_client(*, force_refresh: bool = False):
        vault.get_gcp_access_token(force_refresh=force_refresh)
        return next(fake_clients_iter)

    p._get_client = fake_get_client  # type: ignore[assignment]
    return p


def _auth_error() -> Exception:
    """Construct a real anthropic.AuthenticationError without an HTTP round-trip."""
    import httpx
    from anthropic import AuthenticationError

    req = httpx.Request("POST", "https://example.invalid/")
    resp = httpx.Response(401, request=req)
    return AuthenticationError("Unauthorized", response=resp, body=None)


def test_vertex_retries_once_on_authentication_error() -> None:
    """Stale GCP token -> force-refresh + rebuild client + retry once."""
    # First stream open raises 401; second yields a normal completion.
    bad_cm = _FakeStreamCM(raise_on_enter=_auth_error())
    good_cm = _FakeStreamCM(events=[])  # no events -> just emits Done

    vault = _FakeVault()
    provider = _make_vertex_with_fakes([bad_cm, good_cm], vault)

    events = list(
        provider.stream(
            [Message(role="user", content="hi")],
            [],
            max_tokens=10,
            temperature=0.0,
        )
    )

    # The retry actually happened.
    assert vault.force_refresh_count == 1
    # Both context managers were entered; the failed one was not __exited__
    # (because __enter__ raised), the successful one was properly closed.
    assert bad_cm.entered is True and bad_cm.exited is False
    assert good_cm.entered is True and good_cm.exited is True
    # We still produced a terminal Done event for the agent loop.
    assert isinstance(events[-1], Done)
    assert events[-1].stop_reason == "end_turn"


def test_vertex_does_not_retry_more_than_once() -> None:
    """Two consecutive 401s should bubble up; we do not loop forever."""
    bad1 = _FakeStreamCM(raise_on_enter=_auth_error())
    bad2 = _FakeStreamCM(raise_on_enter=_auth_error())
    vault = _FakeVault()
    provider = _make_vertex_with_fakes([bad1, bad2], vault)

    from anthropic import AuthenticationError

    with pytest.raises(AuthenticationError):
        list(
            provider.stream(
                [Message(role="user", content="hi")],
                [],
                max_tokens=10,
                temperature=0.0,
            )
        )
    assert vault.force_refresh_count == 1
    assert bad1.entered is True
    assert bad2.entered is True


# ----- agent loop -----------------------------------------------------------


class _ScriptedProvider:
    """Provider that yields a pre-recorded sequence of streams."""

    name = "scripted"
    model = "scripted"

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self._scripts = list(scripts)
        self.calls = 0

    def stream(
        self,
        messages: list[Message],
        tools: list[Tool],
        *,
        max_tokens: int,
        temperature: float,
        cancel: Optional[Event] = None,
    ) -> Iterator[StreamEvent]:
        self.calls += 1
        for evt in self._scripts.pop(0):
            yield evt


def _registry_with(handler) -> ToolRegistry:
    r = ToolRegistry()
    r.register(
        BuiltinTool(
            name="t",
            description="d",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
        )
    )
    return r


def test_agent_runs_tool_then_finishes() -> None:
    provider = _ScriptedProvider(
        [
            [
                ToolCallEvent(tool_call=ToolCall(id="c1", name="t", arguments={})),
                Done(stop_reason="tool_use"),
            ],
            [
                TextDelta(text="all done"),
                Done(stop_reason="end_turn"),
            ],
        ]
    )
    registry = _registry_with(lambda args: "tool ran")
    out = io.StringIO()
    agent = Agent(provider, registry, max_tokens=100, temperature=0.0, max_steps=5, out=out)

    messages = [Message(role="user", content="go")]
    result = agent.run(messages)

    assert result.stop_reason == "end_turn"
    assert provider.calls == 2
    roles = [m.role for m in messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert "all done" in messages[-1].content


def test_agent_respects_max_steps() -> None:
    # Provider always asks for another tool call.
    def loop_step():
        return [
            ToolCallEvent(tool_call=ToolCall(id="c", name="t", arguments={})),
            Done(stop_reason="tool_use"),
        ]

    provider = _ScriptedProvider([loop_step() for _ in range(10)])
    registry = _registry_with(lambda args: "...")
    out = io.StringIO()
    agent = Agent(provider, registry, max_tokens=100, temperature=0.0, max_steps=3, out=out)
    result = agent.run([Message(role="user", content="go")])
    assert result.stop_reason == "max_steps"
    assert result.steps == 3


def test_agent_interrupt_keeps_partial() -> None:
    cancel = Event()

    def script_stream(messages, tools, *, max_tokens, temperature, cancel=None):
        # Emit some text, then mark cancel before the next provider call.
        yield TextDelta(text="hello ")
        cancel.set()
        yield TextDelta(text="world")
        yield Done(stop_reason="end_turn")

    class _P:
        name = "p"
        model = "m"

        def stream(self, *a, **kw):
            return script_stream(*a, **kw)

    registry = _registry_with(lambda args: "")
    out = io.StringIO()
    agent = Agent(_P(), registry, max_tokens=100, temperature=0.0, max_steps=3, out=out)
    messages = [Message(role="user", content="go")]
    result = agent.run(messages, cancel=cancel)
    assert result.stop_reason == "interrupted"
    assert any("interrupted" in (m.content or "") for m in messages if m.role == "assistant")
