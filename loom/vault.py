"""HashiCorp Vault client for AppRole auth + reading GCP OAuth tokens.

Designed to feed Vertex AI: we authenticate via AppRole, read the configured
secret path (typically Vault's GCP secrets engine roleset, e.g.
``gcp/token/<roleset>``), and extract a Google OAuth access token.

The client caches the Vault session token and the GCP access token, refreshing
each just before expiry so the agent loop never hits a stale-credential 401.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Optional

import requests

from .config import VaultConfig


# Refresh credentials this many seconds before they actually expire so we
# never present a token that's about to die mid-request.
_REFRESH_LEEWAY_SECONDS = 60


class VaultError(RuntimeError):
    pass


@dataclass
class _Cached:
    value: str
    expires_at: float  # epoch seconds


class VaultClient:
    """Thread-safe Vault AppRole client with GCP token extraction."""

    def __init__(self, cfg: VaultConfig, *, timeout: float = 10.0) -> None:
        if not cfg.configured:
            raise VaultError(
                "VaultConfig is not fully configured (need url, role_id, secret_id, token_path)."
            )
        self._cfg = cfg
        self._timeout = timeout
        self._session = requests.Session()
        self._lock = Lock()
        self._client_token: Optional[_Cached] = None
        self._gcp_token: Optional[_Cached] = None

    # ----- private helpers ---------------------------------------------------

    def _headers(self, with_token: bool = False) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._cfg.namespace:
            h["X-Vault-Namespace"] = self._cfg.namespace
        if with_token and self._client_token:
            h["X-Vault-Token"] = self._client_token.value
        return h

    def _login(self) -> _Cached:
        url = f"{self._cfg.url.rstrip('/')}/v1/auth/{self._cfg.approle_mount}/login"
        body = {"role_id": self._cfg.role_id, "secret_id": self._cfg.secret_id}
        resp = self._session.post(url, json=body, headers=self._headers(), timeout=self._timeout)
        if resp.status_code != 200:
            raise VaultError(f"Vault AppRole login failed [{resp.status_code}]: {resp.text}")
        data = resp.json().get("auth") or {}
        token = data.get("client_token")
        lease = int(data.get("lease_duration") or 0)
        if not token:
            raise VaultError(f"Vault AppRole login returned no client_token: {data}")
        # If lease is 0 (root token), treat it as "long lived" but still refresh occasionally.
        ttl = lease if lease > 0 else 3600
        return _Cached(value=token, expires_at=time.time() + ttl)

    def _ensure_session(self) -> str:
        cached = self._client_token
        if cached and cached.expires_at - _REFRESH_LEEWAY_SECONDS > time.time():
            return cached.value
        self._client_token = self._login()
        return self._client_token.value

    def _read_secret(self, path: str) -> dict[str, Any]:
        self._ensure_session()
        path = path.strip("/")
        url = f"{self._cfg.url.rstrip('/')}/v1/{path}"
        resp = self._session.get(url, headers=self._headers(with_token=True), timeout=self._timeout)
        if resp.status_code == 403:
            # Token may have expired between calls; re-login and retry once.
            self._client_token = self._login()
            resp = self._session.get(
                url, headers=self._headers(with_token=True), timeout=self._timeout
            )
        if resp.status_code != 200:
            raise VaultError(f"Vault read {path!r} failed [{resp.status_code}]: {resp.text}")
        return resp.json()

    @staticmethod
    def _extract_gcp_token(payload: dict[str, Any]) -> tuple[str, int]:
        """Pull an OAuth access token + ttl out of a Vault read response.

        Handles both KV-v2 (``data.data``) and direct payloads (``data``), and
        the field names Vault's GCP secrets engine uses (``token`` for OAuth
        rolesets, ``access_token`` as a common alias).
        """
        outer = payload.get("data") or {}
        candidates = [outer]
        if isinstance(outer.get("data"), dict):  # KV v2 wraps in data.data
            candidates.append(outer["data"])

        for d in candidates:
            for field in ("token", "access_token"):
                token = d.get(field)
                if token:
                    expires_at_sec = d.get("expires_at_seconds")
                    ttl = d.get("token_ttl") or d.get("ttl") or payload.get("lease_duration") or 0
                    if expires_at_sec:
                        ttl = max(int(expires_at_sec) - int(time.time()), 0)
                    return token, int(ttl) if ttl else 1800

        raise VaultError(
            "Could not find an OAuth token in Vault response. Expected a "
            "'token' or 'access_token' field under data; got keys: "
            f"{list(outer.keys())}"
        )

    # ----- public API --------------------------------------------------------

    def get_gcp_access_token(self, *, force_refresh: bool = False) -> str:
        """Return a fresh-enough Google OAuth access token."""
        with self._lock:
            cached = self._gcp_token
            if (
                not force_refresh
                and cached
                and cached.expires_at - _REFRESH_LEEWAY_SECONDS > time.time()
            ):
                return cached.value

            payload = self._read_secret(self._cfg.token_path)
            token, ttl = self._extract_gcp_token(payload)
            self._gcp_token = _Cached(value=token, expires_at=time.time() + max(ttl, 60))
            return token

    def close(self) -> None:
        self._session.close()
