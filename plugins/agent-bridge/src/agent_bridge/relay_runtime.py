"""Private capture/restore of known relay sources and explicit token-gate state.

Restoration constructs an unstarted server. It never reads token-store files,
mints credentials, starts listeners or invokes arbitrary serialized callbacks.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import asdict, dataclass
from typing import Literal

from credential_relay.registry import TokenRegistry
from credential_relay.server import CredentialRelayServer, RelayPolicy
from credential_relay.sources.az_login import AzLoginSource
from credential_relay.sources.gh_auth import GhAuthSource
from credential_relay.sources.git_credential import GitCredentialSource
from credential_relay.sources.injected_token import InjectedTokenSource
from credential_relay.token_gate import RelayTokenGate
from pydantic import TypeAdapter
from zdd.private_protocol import PrivateIPCError, Refusal

from .agent_registry_relay import FileTokenAuthorizer, FileTokenValidator
from .resolver_runtime import _encode


@dataclass
class _SourceState:
    kind: Literal["gh-auth", "git-credential", "az-login", "injected-azure-token"]
    hosts: list[str]
    account: str | None
    ttl: float | int | None
    cache: list[tuple[tuple[str, ...], str, float]]
    resources: list[str]
    env_var: str | None


@dataclass
class _CallbackState:
    kind: Literal["file-validator", "file-authorizer", "token-registry"]
    path: str | None
    resources: list[str]
    tokens: list[str]


@dataclass
class _RelayState:
    schema: Literal[1]
    port: int
    ado_host: str | None
    allowed_actions: list[str]
    allowed_hosts: list[str]
    sources: list[_SourceState]
    gate: Literal["none", "validator", "authorizer"]
    required_actions: list[str]
    validators: list[_CallbackState]
    authorizers: list[_CallbackState]
    static_resources: list[str]
    account_policy: str | None = None


_ADAPTER = TypeAdapter(_RelayState)


def _source_state(source) -> _SourceState:
    cache = []
    hosts = []
    account = ttl = env_var = None
    resources = []
    if type(source) is GhAuthSource:
        expected = {"_github_hosts", "_account"}
        hosts, account = sorted(source._github_hosts), source._account
    elif type(source) is GitCredentialSource:
        expected = {
            "_cache_ttl", "_github_username", "_username_resolver", "_github_hosts",
            "_cache", "_inflight", "_lock",
        }
        if source._username_resolver is not None or source._inflight or source._lock.locked():
            raise PrivateIPCError(Refusal.AUTHORITY)
        hosts, account, ttl = sorted(source._github_hosts), source._github_username, source._cache_ttl
        cache = [(key, value, expiry) for key, (value, expiry) in sorted(source._cache.items())]
    elif type(source) is AzLoginSource:
        expected = {"_allowed_resources", "_cache_ttl_override", "_cache"}
        resources, ttl = sorted(source._allowed_resources), source._cache_ttl_override
        cache = [(key, value, expiry) for key, (value, expiry) in sorted(source._cache.items())]
    elif type(source) is InjectedTokenSource:
        expected = {"_allowed_resources", "_env_var"}
        resources, env_var = sorted(source._allowed_resources), source._env_var
    else:
        raise PrivateIPCError(Refusal.AUTHORITY)
    if set(vars(source)) != expected:
        raise PrivateIPCError(Refusal.AUTHORITY)
    return _SourceState(source.name, hosts, account, ttl, cache, resources, env_var)


def _callback_state(callback) -> _CallbackState:
    if type(callback) is FileTokenValidator:
        return _CallbackState("file-validator", str(callback._path), [], [])
    if type(callback) is FileTokenAuthorizer:
        return _CallbackState("file-authorizer", str(callback._path), sorted(callback._static), [])
    if (
        inspect.ismethod(callback)
        and type(callback.__self__) is TokenRegistry
        and callback.__func__ is TokenRegistry.validate
    ):
        registry = callback.__self__
        if not registry._lock.acquire(blocking=False):
            raise PrivateIPCError(Refusal.AUTHORITY)
        try:
            return _CallbackState("token-registry", None, [], sorted(registry._tokens))
        finally:
            registry._lock.release()
    raise PrivateIPCError(Refusal.AUTHORITY)


def _decode(payload: object) -> _RelayState:
    try:
        wire = _encode(payload)
        state = _ADAPTER.validate_json(wire, strict=True)
        normalized = json.loads(_ADAPTER.dump_json(state))
        if state.account_policy is None:
            normalized.pop("account_policy")
        if _encode(normalized) != wire or not 0 <= state.port <= 65535:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if state.gate == "none" and (
            state.required_actions or state.validators or state.authorizers or state.static_resources
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        if state.gate == "validator" and state.authorizers:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if state.gate == "authorizer" and not state.authorizers:
            raise PrivateIPCError(Refusal.AUTHORITY)
        for source in state.sources:
            if source.kind == "git-credential" and (
                source.ttl is None or any(len(key) != 3 for key, _value, _expiry in source.cache)
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            if source.kind == "injected-azure-token" and (
                not source.env_var or "=" in source.env_var or "\0" in source.env_var
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
        return state
    except (TypeError, ValueError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


def capture_relay(server: CredentialRelayServer) -> dict[str, object]:
    """Detach current effective source, cache and authorization state in memory.

    Called synchronously by the serving application thread; active connections
    refuse rather than copying an in-flight source operation into another owner.
    """
    if type(server) is not CredentialRelayServer or type(server.policy) is not RelayPolicy:
        raise PrivateIPCError(Refusal.AUTHORITY)
    if server.stats.active_connections:
        raise PrivateIPCError(Refusal.AUTHORITY)
    try:
        callback = server.token_authorizer or server.token_validator
        gate_kind = "none"
        validators = authorizers = []
        static_resources = []
        if callback is not None:
            if (
                not inspect.ismethod(callback)
                or type(callback.__self__) is not RelayTokenGate
                or callback.__func__ is not (
                    RelayTokenGate.authorize if server.token_authorizer else RelayTokenGate.validate
                )
                or (server.token_authorizer is not None and server.token_validator is not None)
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            gate = callback.__self__
            gate_kind = "authorizer" if server.token_authorizer else "validator"
            validators = [_callback_state(value) for value in gate.validators]
            authorizers = [_callback_state(value) for value in gate.authorizers]
            static_resources = sorted(gate.static_resources)
        state = _RelayState(
            1, server.port, server.ado_host, sorted(server.policy.allowed_actions),
            list(server.policy.allowed_hosts), [_source_state(source) for source in server.sources],
            gate_kind, sorted(server.token_required_actions), validators, authorizers, static_resources,
        )
        if server.account_relay is not None:
            from credential_relay.account_relay import AccountRelay
            if type(server.account_relay) is not AccountRelay:
                raise PrivateIPCError(Refusal.AUTHORITY)
            if server.account_relay._tasks:
                raise PrivateIPCError(Refusal.AUTHORITY)
            state.account_policy = server.account_relay.policy_path
        payload = json.loads(_encode(asdict(state)))
        if state.account_policy is None:
            payload.pop("account_policy")
        _decode(payload)
        return payload
    except (TypeError, ValueError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


def _restore_source(state: _SourceState):
    if state.kind == "gh-auth":
        source = GhAuthSource(account=state.account, github_hosts=state.hosts)
    elif state.kind == "git-credential":
        source = GitCredentialSource(
            state.ttl, github_username=state.account, github_hosts=state.hosts,
        )
        source._cache = {tuple(key): (value, expiry) for key, value, expiry in state.cache}
    elif state.kind == "az-login":
        if any(len(key) != 2 for key, _value, _expiry in state.cache):
            raise PrivateIPCError(Refusal.AUTHORITY)
        source = AzLoginSource(state.resources, cache_ttl_override=state.ttl)
        source._cache = {tuple(key): (value, expiry) for key, value, expiry in state.cache}
    else:
        source = InjectedTokenSource(state.resources, env_var=state.env_var)
    if _encode(asdict(_source_state(source))) != _encode(asdict(state)):
        raise PrivateIPCError(Refusal.AUTHORITY)
    return source


def _restore_callback(state: _CallbackState):
    if state.kind == "file-validator":
        callback = FileTokenValidator(state.path)
    elif state.kind == "file-authorizer":
        callback = FileTokenAuthorizer(state.path, state.resources)
    else:
        registry = TokenRegistry()
        for token in state.tokens:
            registry.add(token)
        callback = registry.validate
    if _encode(asdict(_callback_state(callback))) != _encode(asdict(state)):
        raise PrivateIPCError(Refusal.AUTHORITY)
    return callback


def restore_relay(payload: object) -> CredentialRelayServer:
    """Restore effective sources/gates without starting or binding the server."""
    state = _decode(payload)
    try:
        gate = RelayTokenGate(
            tuple(_restore_callback(value) for value in state.validators),
            tuple(_restore_callback(value) for value in state.authorizers),
            frozenset(state.static_resources),
        )
        server = CredentialRelayServer(
            port=state.port, sources=[_restore_source(value) for value in state.sources],
            policy=RelayPolicy(frozenset(state.allowed_actions), list(state.allowed_hosts)),
            ado_host=state.ado_host,
            token_required_actions=frozenset(state.required_actions),
            token_validator=gate.validate if state.gate == "validator" else None,
            token_authorizer=gate.authorize if state.gate == "authorizer" else None,
        )
        if state.account_policy is not None:
            from credential_relay.account_relay import AccountRelay
            server.account_relay = AccountRelay(state.account_policy)
        # None is effective state, not permission to reload the child's env.
        server.ado_host = state.ado_host
        if _encode(capture_relay(server)) != _encode(payload):
            raise PrivateIPCError(Refusal.AUTHORITY)
        return server
    except (TypeError, ValueError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None
