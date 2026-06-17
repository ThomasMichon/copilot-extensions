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
    _token_validators: list[Callable[[str], bool]] = field(default_factory=list)
    token_required_actions: set[str] = field(default_factory=set)
    # Merged Azure-token allowlist across providers. A single AzLoginSource is
    # built from the union so two providers (codespaces + containers) can each
    # contribute resources/scopes instead of racing add_source's first-wins
    # dedup. ``"*"`` means any scope.
    azure_resources: set[str] = field(default_factory=set)
    _azure_enabled: bool = False

    def add_source(self, source: CredentialSource) -> None:
        """Register a credential source (deduped by ``name``; first wins).

        Multiple providers may contribute the same generic source (e.g. both
        codespaces and containers want ``git-credential``); keep only the first
        so routing stays deterministic.
        """
        if any(s.name == source.name for s in self.sources):
            return
        self.sources.append(source)

    def allow_azure_resources(self, resources: list[str]) -> None:
        """Enable Azure-token minting for ``resources`` (merged across providers).

        Each provider contributes the resources/scopes its targets need; the
        builder constructs ONE ``AzLoginSource`` from the union at build time.
        ``"*"`` permits any scope. Pair with :meth:`require_token` when the
        target transport is network-reachable (containers); the SSH-tunnel-
        isolated codespace path presents its own per-codespace token.
        """
        self._azure_enabled = True
        self.azure_resources.update(resources)

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

        Multiple providers may gate the same action (e.g. both containers and
        codespaces gate ``get-azure-token`` with their own per-target token
        stores). Validators are accumulated and checked with **any-match**, so a
        request is accepted if *any* registered provider recognizes its token.
        """
        self._token_validators.append(validator)
        self.token_required_actions.update(actions)

    @property
    def empty(self) -> bool:
        return not self.sources and not self._azure_enabled

    def build(self) -> CredentialRelayServer:
        """Construct a CredentialRelayServer from the accumulated config."""
        sources = list(self.sources)
        if self._azure_enabled and not any(s.name == "az-login" for s in sources):
            from .sources.az_login import AzLoginSource

            sources.append(
                AzLoginSource(allowed_resources=sorted(self.azure_resources))
            )
        policy = RelayPolicy(allowed_hosts=list(self.allowed_hosts))
        kwargs: dict = {"sources": sources, "policy": policy}
        if self.port is not None:
            kwargs["port"] = self.port
        if self.ado_host is not None:
            kwargs["ado_host"] = self.ado_host
        if self.token_required_actions:
            validators = list(self._token_validators)
            kwargs["token_validator"] = lambda tok: any(v(tok) for v in validators)
            kwargs["token_required_actions"] = frozenset(self.token_required_actions)
        return CredentialRelayServer(**kwargs)
