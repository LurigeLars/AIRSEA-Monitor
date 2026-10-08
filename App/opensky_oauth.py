"""OpenSky OAuth2 client-credentials for aggregated shadow observations only.

No secrets, bearer tokens, or aircraft-level observations are logged or persisted.
"""
from __future__ import annotations

import json
import os
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"


class OpenSkyOAuth:
    def __init__(self, client_id: str, client_secret: str):
        if not all(isinstance(x, str) and 3 <= len(x) <= 4096 for x in (client_id, client_secret)):
            raise ValueError("Missing or malformed OpenSky OAuth credentials")
        self._client_id = client_id
        self._client_secret = client_secret
        self._token = None
        self._refresh_by = 0.0

    @classmethod
    def from_environment(cls) -> "OpenSkyOAuth | None":
        client_id = os.environ.get("OPENSKY_CLIENT_ID")
        secret = os.environ.get("OPENSKY_CLIENT_SECRET")
        if client_id is None and secret is None:
            return None  # Original anonymous mode is supported if never configured.
        if not client_id or not secret:
            raise ValueError("OpenSky OAuth credentials only partly configured")
        return cls(client_id, secret)

    def invalidate(self) -> None:
        self._token = None
        self._refresh_by = 0.0

    def bearer(self) -> str:
        if self._token and time.monotonic() < self._refresh_by:
            return self._token
        form = urlencode({
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }).encode("utf-8")
        req = Request(TOKEN_URL, data=form, method="POST", headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        })
        with urlopen(req, timeout=15, context=ssl.create_default_context()) as resp:
            if resp.status != 200:
                raise RuntimeError("OpenSky authentication HTTP failure")
            raw = resp.read(8193)
        if len(raw) > 8192:
            raise ValueError("OpenSky authentication response too large")
        result = json.loads(raw)
        token = result.get("access_token") if isinstance(result, dict) else None
        if not isinstance(token, str) or not 12 <= len(token) <= 8192:
            raise ValueError("OpenSky authentication token missing")
        expires_in = int(result.get("expires_in", 1800))
        if expires_in <= 0:
            raise ValueError("OpenSky authentication token expiration invalid")
        self._token = token
        self._refresh_by = time.monotonic() + max(1, expires_in - min(60, expires_in // 4))
        return token

    def fetch_states(self, fetch_json, states_url: str) -> dict:
        """Reauthenticate and retry once on a real HTTP 401. No anonymous fallback."""
        try:
            return fetch_json(states_url, bearer_token=self.bearer())
        except HTTPError as exc:
            if exc.code != 401:
                raise
            self.invalidate()
            return fetch_json(states_url, bearer_token=self.bearer())
