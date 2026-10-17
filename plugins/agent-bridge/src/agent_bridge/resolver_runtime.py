"""Private, opt-in capture of the registry that is actually serving requests.

No discovery, executable probing, callback import or provider subprocess occurs.
Only known CLI resolvers without in-process fallbacks can cross this boundary.
Credentials in agent environment/MCP configuration remain private payload data.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import TypeAdapter
from zdd.private_protocol import PrivateIPCError, Refusal

from .agent_registry_common import AgentConfig, NamespaceAgentInfo
from .agent_registry_namespace import CliNamespaceResolver, RestrictedCliNamespaceResolver
from .agent_registry_resolver import AgentResolver
from .cold_store_sources import ColdStoreManifest, ColdStoreProviderRegistry
from .provider_sources import ProviderManifest
from .reader_admission import ReaderAdmission
from .topology import MachineConfig


@dataclass
class _NamespaceState:
    kind: Literal["cli", "restricted-cli"]
    binstub: str
    command: list[str] | None
    cache: tuple[float, list[NamespaceAgentInfo]] | None
    admission: ReaderAdmission
    timed_out: bool
    native_success: bool


@dataclass
class _ResolverState:
    schema: Literal[1]
    agents: dict[str, AgentConfig]
    machines: dict[str, MachineConfig]
    local_key: str | None
    local_platform: Literal["windows", "wsl", "linux"]
    topology_errors: list[str]
    topology_warnings: list[str]
    agent_aliases: dict[str, str | None]
    ssh_aliases: dict[str, tuple[str, int]]
    ambiguous_ssh_aliases: list[str]
    namespaces: dict[str, _NamespaceState]
    incomplete_namespaces: list[str]
    provider_entries: dict[str, ProviderManifest]
    provider_manifests: dict[str, ProviderManifest]
    provider_namespaces: list[str]
    provider_scan_ts: float
    provider_scan_ttl: float
    apply_generation: int
    cold_entries: dict[str, ColdStoreManifest]
    cold_manifests: dict[str, ColdStoreManifest]
    cold_scan_ts: float
    cold_scan_ttl: float


_ADAPTER = TypeAdapter(_ResolverState)


def _encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _decode(value: object) -> _ResolverState:
    """Reject coercion, unknown fields and unsupported values, without secret errors."""
    try:
        wire = _encode(value)
        state = _ADAPTER.validate_json(wire, strict=True)
        if _encode(json.loads(_ADAPTER.dump_json(state))) != wire:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if (
            any(key != agent.name for key, agent in state.agents.items())
            or any(key != machine.key for key, machine in state.machines.items())
            or (state.local_key is not None and state.local_key not in state.machines)
            or any(alias is not None and alias not in state.agents
                   for alias in state.agent_aliases.values())
            or len(set(state.provider_namespaces)) != len(state.provider_namespaces)
            or set(state.provider_namespaces) != set(state.provider_manifests)
            or not set(state.provider_namespaces) <= set(state.namespaces)
            or state.apply_generation < 0
            or min(state.provider_scan_ts, state.provider_scan_ttl,
                   state.cold_scan_ts, state.cold_scan_ttl) < 0
            or any(namespace.admission.probing for namespace in state.namespaces.values())
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        for key, index in state.ssh_aliases.values():
            if key not in state.machines or not 0 <= index < len(state.machines[key].ssh_environments):
                raise PrivateIPCError(Refusal.AUTHORITY)
        for prefix, manifest in state.provider_manifests.items():
            namespace = state.namespaces[prefix]
            if (
                prefix != manifest.namespace
                or not manifest.command
                or namespace.command != list(manifest.command)
                or namespace.kind != ("restricted-cli" if manifest.restricted else "cli")
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
        if any(key != manifest.capability for key, manifest in state.cold_manifests.items()):
            raise PrivateIPCError(Refusal.AUTHORITY)
        return state
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


def capture_resolver(resolver: AgentResolver) -> dict[str, object]:
    """Detach current applied state; refuse concurrent scans and opaque callbacks.

    The serving application's thread must call this without an intervening
    await. The scan lock is acquired nonblocking, never held across discovery.
    """
    if (
        type(resolver) is not AgentResolver
        or type(resolver.cold_store) is not ColdStoreProviderRegistry
        or any(type(agent) is not AgentConfig for agent in resolver.agents.values())
        or any(type(machine) is not MachineConfig for machine in resolver.machines.values())
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    if not resolver._scan_lock.acquire(blocking=False):
        raise PrivateIPCError(Refusal.AUTHORITY)
    try:
        if resolver._scan_attempts_in_flight:
            raise PrivateIPCError(Refusal.AUTHORITY)
        namespaces = {}
        for prefix, namespace in resolver.namespace_resolvers.items():
            if (
                type(namespace) not in (CliNamespaceResolver, RestrictedCliNamespaceResolver)
                or namespace._fallback is not None
                or prefix != namespace.prefix
                or (namespace.pending_list_task is not None and not namespace.pending_list_task.done())
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            namespaces[prefix] = _NamespaceState(
                "restricted-cli" if type(namespace) is RestrictedCliNamespaceResolver else "cli",
                namespace._binstub, namespace._command, namespace._list_cache,
                namespace._list_admission, namespace._list_timed_out, namespace._list_native_success,
            )
        ssh_aliases = {}
        for alias, (machine, environment) in resolver._alias_index.items():
            if resolver.machines.get(machine.key) is not machine:
                raise PrivateIPCError(Refusal.AUTHORITY)
            indices = [i for i, env in enumerate(machine.ssh_environments) if env is environment]
            if len(indices) != 1:
                raise PrivateIPCError(Refusal.AUTHORITY)
            ssh_aliases[alias] = (machine.key, indices[0])
        local = resolver._local_machine
        if local is not None and resolver.machines.get(local.key) is not local:
            raise PrivateIPCError(Refusal.AUTHORITY)
        cold = resolver.cold_store
        state = _ResolverState(
            1, resolver.agents, resolver.machines, local.key if local else None,
            resolver._local_platform, resolver.topology_errors, resolver.topology_warnings,
            resolver._agent_alias_index, ssh_aliases, sorted(resolver._ambiguous_ssh_aliases),
            namespaces, resolver.incomplete_namespaces, resolver._provider_entries,
            resolver._provider_manifests, sorted(resolver._provider_namespaces),
            resolver._provider_scan_ts, resolver._provider_scan_ttl, resolver._apply_generation,
            cold._entries, cold._manifests, cold._scan_ts, cold._scan_ttl,
        )
        detached = json.loads(_encode(asdict(state)))
        _decode(detached)
        return detached
    except (TypeError, ValueError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None
    finally:
        resolver._scan_lock.release()


def restore_resolver(payload: object) -> AgentResolver:
    """Build an isolated resolver from private state, without rescanning disk.

    Restored caches/admission timestamps share the same native monotonic clock;
    this component supports only same-execution-space successor handoff.
    """
    state = _decode(payload)
    try:
        resolver = AgentResolver(
            state.agents, state.machines, topology_errors=state.topology_errors,
            topology_warnings=state.topology_warnings,
            resolved_local_identity=(state.local_key, state.local_platform),
        )
        resolver._topology_warnings = state.topology_warnings
        resolver._agent_alias_index = state.agent_aliases
        resolver._alias_index = {
            alias: (state.machines[key], state.machines[key].ssh_environments[index])
            for alias, (key, index) in state.ssh_aliases.items()
        }
        resolver._ambiguous_ssh_aliases = set(state.ambiguous_ssh_aliases)
        for prefix, saved in state.namespaces.items():
            cls = RestrictedCliNamespaceResolver if saved.kind == "restricted-cli" else CliNamespaceResolver
            namespace = cls(prefix, saved.binstub, command=saved.command)
            namespace._list_cache = saved.cache
            namespace._list_admission = saved.admission
            namespace._list_timed_out = saved.timed_out
            namespace._list_native_success = saved.native_success
            resolver._namespace_resolvers[prefix] = namespace
        resolver._last_incomplete_namespaces = state.incomplete_namespaces
        resolver._provider_entries = state.provider_entries
        resolver._provider_manifests = state.provider_manifests
        resolver._provider_namespaces = set(state.provider_namespaces)
        resolver._provider_scan_ts = state.provider_scan_ts
        resolver._provider_scan_ttl = state.provider_scan_ttl
        resolver._apply_generation = state.apply_generation
        cold = resolver.cold_store
        cold._entries, cold._manifests = state.cold_entries, state.cold_manifests
        cold._scan_ts, cold._scan_ttl = state.cold_scan_ts, state.cold_scan_ttl
        return resolver
    except (TypeError, ValueError, KeyError, AttributeError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None
