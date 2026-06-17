"""Concrete auth injectors.

Token acquisition reuses the ``credential_relay`` host-credential sources
(``az_login``, ``gh_auth``, ``git_credential``) so this plugin does not
re-implement ``az`` / ``gh`` / GCM shell-outs, caching, or expiry handling. Each
source returns git-credential-protocol ``key=value`` text; :func:`parse_response`
pulls the token/password out of it.
"""

from __future__ import annotations

import base64
import os
from urllib.parse import urlsplit

from ..config import AuthSpec, BridgeConfig
from .base import AuthInjector, NoneInjector, TokenInjector


def parse_response(text: str | None) -> dict[str, str]:
    """Parse git-credential-protocol ``key=value`` lines into a dict."""
    out: dict[str, str] = {}
    if not text:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _token_from(text: str | None) -> str | None:
    fields = parse_response(text)
    return fields.get("token") or fields.get("password")


class EnvInjector(TokenInjector):
    """Token from a host environment variable (``env``) or a literal (``static``)."""

    name = "env"

    async def _acquire(self) -> str | None:
        if self.spec.source_env:
            return os.environ.get(self.spec.source_env)
        return self.spec.value


class EntraInjector(TokenInjector):
    """Entra ID / Azure access token via ``credential_relay.sources.az_login``."""

    name = "entra"

    def __init__(self, spec: AuthSpec, *, timeout: float = 30.0) -> None:
        super().__init__(spec)
        self._timeout = timeout
        self._source = self._new_source()

    @staticmethod
    def _new_source():
        from credential_relay.sources.az_login import AzLoginSource

        # The bridge config is itself the allowlist boundary, so permit any scope
        # the operator configured (resource/scope is fixed per bridge file).
        return AzLoginSource(allowed_resources=["*"])

    async def invalidate(self) -> None:
        await super().invalidate()
        self._source = self._new_source()  # drop the source's internal token cache

    async def _acquire(self) -> str | None:
        fields: dict[str, str] = {}
        if self.spec.scope:
            fields["scope"] = self.spec.scope
        elif self.spec.resource:
            fields["resource"] = self.spec.resource
        if self.spec.tenant:
            fields["tenant"] = self.spec.tenant
        resp = await self._source.resolve("get-azure-token", fields, timeout=self._timeout)
        return _token_from(resp)


class GhInjector(TokenInjector):
    """GitHub token via ``credential_relay.sources.gh_auth``."""

    name = "gh"

    def __init__(self, spec: AuthSpec, *, timeout: float = 30.0) -> None:
        super().__init__(spec)
        self._timeout = timeout
        from credential_relay.sources.gh_auth import GhAuthSource

        self._source = GhAuthSource()

    async def _acquire(self) -> str | None:
        resp = await self._source.resolve("get-github-token", {}, timeout=self._timeout)
        return _token_from(resp)


class GitCredentialInjector(AuthInjector):
    """HTTP Basic from Git Credential Manager via ``credential_relay.sources.git_credential``.

    The host is derived from the upstream ``server.url``. Produces an
    ``Authorization: Basic base64(user:token)`` header for HTTP transports and a
    token-only env var for stdio transports.
    """

    name = "git-credential"

    def __init__(self, spec: AuthSpec, host: str, *, timeout: float = 30.0) -> None:
        self.spec = spec
        self._host = host
        self._timeout = timeout
        self._cached: dict[str, str] | None = None
        from credential_relay.sources.git_credential import GitCredentialSource

        self._source = GitCredentialSource()

    async def invalidate(self) -> None:
        self._cached = None

    async def _creds(self) -> dict[str, str]:
        if self._cached is None:
            resp = await self._source.resolve(
                "get", {"protocol": "https", "host": self._host}, timeout=self._timeout
            )
            self._cached = parse_response(resp)
        return self._cached

    async def headers(self) -> dict[str, str]:
        creds = await self._creds()
        user = creds.get("username", "")
        secret = creds.get("password") or creds.get("token")
        if not secret:
            return {}
        raw = f"{user}:{secret}".encode()
        return {self.spec.header: f"Basic {base64.b64encode(raw).decode()}"}

    async def child_env(self) -> dict[str, str]:
        creds = await self._creds()
        secret = creds.get("password") or creds.get("token")
        if not secret or not self.spec.target_env:
            return {}
        return {self.spec.target_env: secret}


def build_injector(cfg: BridgeConfig) -> AuthInjector:
    """Construct the auth injector for a bridge config."""
    kind = cfg.auth.normalized_kind
    if kind == "none":
        return NoneInjector()
    if kind == "env":
        return EnvInjector(cfg.auth)
    if kind == "entra":
        return EntraInjector(cfg.auth, timeout=cfg.timeout)
    if kind == "gh":
        return GhInjector(cfg.auth, timeout=cfg.timeout)
    if kind == "git-credential":
        host = urlsplit(cfg.server.url or "").hostname or ""
        return GitCredentialInjector(cfg.auth, host=host, timeout=cfg.timeout)
    raise ValueError(f"unknown auth kind: {cfg.auth.kind}")
