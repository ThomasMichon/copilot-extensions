"""Provider source-injection API for the credential relay.

agent-bridge runs the relay in its daemon and discovers provider plugins that
inject credential sources for their targets. Each provider exposes::

    # <provider_pkg>/relay_provider.py
    def register_relay(builder: RelayBuilder) -> None:
        builder.add_source(MySource())

agent-bridge collects all providers into one ``RelayBuilder`` and constructs a
single :class:`~credential_relay.server.CredentialRelayServer` from it. This
keeps the relay framework target-agnostic: the bridge owns the server, providers
contribute the concrete sources/policy/port for their targets.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from .server import CredentialRelayServer, RelayPolicy
from .sources import CredentialSource


class TokenRegistry:
    """Thread-safe set of valid per-session relay tokens.

    Container targets are reached over ``host.docker.internal`` (network
    reachable), so token-gated actions (e.g. Azure token requests) require a
    secret minted per connection. The container resolver mints a token, adds it
    here for the session, and discards it on disconnect.
    """

    def __init__(self) -> None:
        self._tokens: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def mint() -> str:
        """Return a fresh URL-safe secret token."""
        return secrets.token_hex(32)

    def add(self, token: str) -> None:
        with self._lock:
            self._tokens.add(token)

    def discard(self, token: str) -> None:
        with self._lock:
            self._tokens.discard(token)

    def validate(self, token: str) -> bool:
        """Constant-time-ish membership check (token must be non-empty)."""
        if not token:
            return False
        with self._lock:
            # compare_digest against each candidate avoids early-exit timing
            return any(secrets.compare_digest(token, t) for t in self._tokens)


@dataclass
class RelayBuilder:
    """Accumulates provider-injected relay configuration.

    Providers call :meth:`add_source` (and optionally :meth:`allow_hosts`,
    :meth:`set_port`, :meth:`set_ado_host`) from their ``register_relay`` hook.
    The bridge then calls :meth:`build` to construct the server.
    """

    sources: list[CredentialSource] = field(default_factory=list)
    allowed_hosts: list[str] = field(default_factory=list)
    port: int | None = None
    ado_host: str | None = None
    _token_validator: Callable[[str], bool] | None = None
    token_required_actions: set[str] = field(default_factory=set)

    def add_source(self, source: CredentialSource) -> None:
        """Register a credential source (deduped by ``name``; first wins).

        Multiple providers may contribute the same generic source (e.g. both
        codespaces and containers want ``git-credential``); keep only the first
        so routing stays deterministic.
        """
        if any(s.name == source.name for s in self.sources):
            return
        self.sources.append(source)

    def allow_hosts(self, hosts: list[str]) -> None:
        """Extend the relay policy host allowlist (empty list = open policy)."""
        self.allowed_hosts.extend(hosts)

    def set_port(self, port: int | None) -> None:
        """Pin the relay port (e.g. the codespaces SSH-forwarded port)."""
        if port is not None:
            self.port = port

    def set_ado_host(self, ado_host: str | None) -> None:
        """Default ADO host for bare ``get-access-token`` requests."""
        if ado_host:
            self.ado_host = ado_host

    def require_token(
        self, actions: list[str], validator: Callable[[str], bool]
    ) -> None:
        """Gate ``actions`` behind a shared-secret checked by ``validator``.

        ``validator(token) -> bool`` is called with the request's ``auth`` field.
        A :class:`TokenRegistry`'s ``.validate`` is one such validator; providers
        with cross-process token state (e.g. a file-backed store) pass their own.
        Open actions stay ungated so the codespace relay path is unaffected.
        """
        self._token_validator = validator
        self.token_required_actions.update(actions)

    @property
    def empty(self) -> bool:
        return not self.sources

    def build(self) -> CredentialRelayServer:
        """Construct a CredentialRelayServer from the accumulated config."""
        policy = RelayPolicy(allowed_hosts=list(self.allowed_hosts))
        kwargs: dict = {"sources": list(self.sources), "policy": policy}
        if self.port is not None:
            kwargs["port"] = self.port
        if self.ado_host is not None:
            kwargs["ado_host"] = self.ado_host
        if self.token_required_actions:
            kwargs["token_validator"] = self._token_validator
            kwargs["token_required_actions"] = frozenset(self.token_required_actions)
        return CredentialRelayServer(**kwargs)
