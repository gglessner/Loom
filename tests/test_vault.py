"""Vault client tests using a stubbed requests.Session."""

from __future__ import annotations

import time
from typing import Any

import pytest

from loom.config import VaultConfig
from loom.vault import VaultClient, VaultError


class _FakeResp:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self) -> Any:
        return self._payload


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, dict]] = []
        self._responses: list[_FakeResp] = []

    def queue(self, *resps: _FakeResp) -> None:
        self._responses.extend(resps)

    def post(self, url: str, json=None, headers=None, timeout=None) -> _FakeResp:
        self.calls.append(("POST", url, dict(headers or {}), dict(json or {})))
        return self._responses.pop(0)

    def get(self, url: str, headers=None, timeout=None) -> _FakeResp:
        self.calls.append(("GET", url, dict(headers or {}), {}))
        return self._responses.pop(0)

    def close(self) -> None:
        pass


def _make_client(monkeypatch: pytest.MonkeyPatch) -> tuple[VaultClient, _FakeSession]:
    cfg = VaultConfig(
        url="https://vault.example",
        namespace="myns",
        role_id="role-123",
        secret_id="secret-456",
        token_path="gcp/token/example",
    )
    fake = _FakeSession()
    client = VaultClient(cfg)
    client._session = fake  # type: ignore[attr-defined]
    return client, fake


def test_get_gcp_token_extracts_token_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake = _make_client(monkeypatch)
    fake.queue(
        _FakeResp(200, {"auth": {"client_token": "vault-tkn", "lease_duration": 3600}}),
        _FakeResp(200, {"data": {"token": "ya29.fake", "expires_at_seconds": int(time.time()) + 1800}}),
    )

    token = client.get_gcp_access_token()
    assert token == "ya29.fake"

    # Second call should be served from cache, no further HTTP.
    token = client.get_gcp_access_token()
    assert token == "ya29.fake"
    assert len(fake.calls) == 2  # login + read_secret


def test_namespace_header_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake = _make_client(monkeypatch)
    fake.queue(
        _FakeResp(200, {"auth": {"client_token": "t", "lease_duration": 3600}}),
        _FakeResp(200, {"data": {"token": "tok", "token_ttl": 1800}}),
    )
    client.get_gcp_access_token()
    for _, _, headers, _ in fake.calls:
        assert headers.get("X-Vault-Namespace") == "myns"


def test_403_triggers_relogin(monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake = _make_client(monkeypatch)
    fake.queue(
        _FakeResp(200, {"auth": {"client_token": "t1", "lease_duration": 3600}}),
        _FakeResp(403, "expired"),
        _FakeResp(200, {"auth": {"client_token": "t2", "lease_duration": 3600}}),
        _FakeResp(200, {"data": {"token": "tok", "token_ttl": 1800}}),
    )
    token = client.get_gcp_access_token()
    assert token == "tok"
    methods = [c[0] for c in fake.calls]
    assert methods == ["POST", "GET", "POST", "GET"]


def test_missing_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake = _make_client(monkeypatch)
    fake.queue(
        _FakeResp(200, {"auth": {"client_token": "t", "lease_duration": 3600}}),
        _FakeResp(200, {"data": {"unrelated": "value"}}),
    )
    with pytest.raises(VaultError):
        client.get_gcp_access_token()


def test_403_permission_denied_falls_back_to_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """If GET (read) is denied, Loom should retry as POST (update)."""
    client, fake = _make_client(monkeypatch)
    perm_denied = _FakeResp(
        403, '{"errors":["1 error occurred:\\n\\t* permission denied\\n\\n"]}'
    )
    perm_denied.text = '{"errors":["permission denied"]}'  # ensure substring match
    fake.queue(
        _FakeResp(200, {"auth": {"client_token": "t1", "lease_duration": 3600}}),
        perm_denied,  # initial GET -> 403 perm denied
        _FakeResp(200, {"auth": {"client_token": "t2", "lease_duration": 3600}}),  # relogin
        _FakeResp(403, "permission denied"),  # retry GET still 403
        _FakeResp(200, {"data": {"token": "post-tok", "token_ttl": 1800}}),  # POST works
    )
    token = client.get_gcp_access_token()
    assert token == "post-tok"
    methods = [c[0] for c in fake.calls]
    assert methods == ["POST", "GET", "POST", "GET", "POST"]
    # last call should be POST to the secret URL, not the login URL
    last = fake.calls[-1]
    assert last[0] == "POST" and last[1].endswith("/v1/gcp/token/example")


def test_403_permission_denied_after_post_raises_helpful_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both GET and POST are denied, the error should hint at the policy fix."""
    client, fake = _make_client(monkeypatch)
    fake.queue(
        _FakeResp(200, {"auth": {"client_token": "t1", "lease_duration": 3600}}),
        _FakeResp(403, "permission denied"),  # initial GET
        _FakeResp(200, {"auth": {"client_token": "t2", "lease_duration": 3600}}),  # relogin
        _FakeResp(403, "permission denied"),  # retry GET
        _FakeResp(403, "permission denied"),  # POST attempt
        _FakeResp(200, {"auth": {"client_token": "t3", "lease_duration": 3600}}),  # relogin for POST
        _FakeResp(403, "permission denied"),  # POST retry still denied
    )
    with pytest.raises(VaultError) as exc:
        client.get_gcp_access_token()
    msg = str(exc.value)
    assert "permission denied" in msg.lower()
    assert "capabilities" in msg
    assert "gcp/token/example" in msg


@pytest.mark.parametrize(
    "configured_path",
    [
        "gcp/token/example",
        "/gcp/token/example",
        "v1/gcp/token/example",
        "/v1/gcp/token/example",
        "V1/gcp/token/example",
    ],
)
def test_token_path_strips_leading_v1_and_slashes(
    monkeypatch: pytest.MonkeyPatch, configured_path: str
) -> None:
    """The configured token_path is normalised so a leading v1/ doesn't double up."""
    cfg = VaultConfig(
        url="https://vault.example",
        namespace="myns",
        role_id="r",
        secret_id="s",
        token_path=configured_path,
    )
    fake = _FakeSession()
    client = VaultClient(cfg)
    client._session = fake  # type: ignore[attr-defined]
    fake.queue(
        _FakeResp(200, {"auth": {"client_token": "t", "lease_duration": 3600}}),
        _FakeResp(200, {"data": {"token": "tok", "token_ttl": 1800}}),
    )
    client.get_gcp_access_token()
    secret_call = fake.calls[1]
    assert secret_call[0] == "GET"
    assert secret_call[1] == "https://vault.example/v1/gcp/token/example"


def test_kv_v2_shape_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake = _make_client(monkeypatch)
    fake.queue(
        _FakeResp(200, {"auth": {"client_token": "t", "lease_duration": 3600}}),
        _FakeResp(200, {"data": {"data": {"access_token": "kv2-token"}, "metadata": {}}}),
    )
    assert client.get_gcp_access_token() == "kv2-token"
