"""Explicit trusted host configuration, distinct from remote request input."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet, Any

from agent_pull_requests.watch_gateway import WatchScopeBinding
from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.native_read import NativeReadHandler
from agent_pull_requests.review_workspace import _scope
from agent_pull_requests.watch_observer import NativeObservationBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayServiceIdentity, ReplayStore, json_object
from agent_pull_requests.workspace_operations import WorkspaceOperations
from fleet_manager.gateway_service_channel import origin
from fleet_manager.gateway_service_context import identifier

_LIMIT = 1024 * 1024


def _path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("absolute host reference required")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("absolute host reference required")
    return path


def _fields(
    value: object, required: AbstractSet[str], optional: AbstractSet[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= value.keys() or (
        value.keys() - required - optional
    ):
        raise ValueError("invalid host configuration fields")
    return value


def _collection(value: object) -> list[Any]:
    if not isinstance(value, list) or not 1 <= len(value) <= 128:
        raise ValueError("host bindings require 1..128 entries")
    return value


def _argv(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 64 or any(
        not isinstance(item, str) or not item or "\0" in item for item in value
    ) or sum(len(item.encode("utf-8")) for item in value) > 16384:
        raise ValueError("invalid host command reference")
    return tuple(value)


def _seconds(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 300:
        raise ValueError("host budgets require integer seconds in 1..300")
    return value


@dataclass(frozen=True)
class WorkspaceConfig:
    readers: tuple[tuple[str, str, NativeReadHandler], ...]
    cache_bytes: int
    cache_generations: int

    def cache(self, store: ReplayStore) -> GenerationCache:
        return GenerationCache(
            store.root, store.namespace, max_bytes=self.cache_bytes,
            max_generations=self.cache_generations, service_identity=store.service_identity,
        )

    def operations(
        self, store: ReplayStore, checkpoint: Callable[[], None], seconds: int,
    ) -> dict[tuple[str, str], WorkspaceOperations]:
        cache = self.cache(store)
        return {
            (principal, target): WorkspaceOperations(
                store, cache, scope=dict(reader.scope), read_owner=reader,
                binding_ref=reader.binding_ref, checkpoint=checkpoint,
                max_work_seconds=seconds, conversations=True,
            )
            for principal, target, reader in self.readers
        }


@dataclass(frozen=True)
class HostConfig:
    root: Path
    namespace: str
    fleet: str
    gateway: str
    slot: str
    gateway_origin: str
    proof_key_file: Path
    access_token_file: Path
    bindings: tuple[WatchScopeBinding, ...]
    observations: tuple[NativeObservationBinding, ...]
    gateway_ca: Path | None = None
    interval: int = 5
    operation_seconds: int = 60
    drain_timeout: int = 60
    workspaces: WorkspaceConfig | None = None

    @property
    def status_path(self) -> Path:
        return self.root / "service-status.json"

    def store(self) -> ReplayStore:
        return ReplayStore(
            self.root, self.namespace,
            service_identity=ReplayServiceIdentity(self.fleet, self.gateway, self.slot),
        )

    def provision(self) -> None:
        store = self.store()
        if self.workspaces is not None and (
            self.root.is_symlink() or self.root.exists() and any(self.root.iterdir())
        ):
            raise ValueError("workspace provisioning requires a fresh dedicated root")
        store.provision(conversations=self.workspaces is not None)
        if self.workspaces is not None:
            self.workspaces.cache(store).provision()


def _workspaces(value: object, bindings: list[WatchScopeBinding],
                observations: list[NativeObservationBinding], seconds: int) -> WorkspaceConfig:
    selected = _fields(value, {"bindings", "cache_bytes", "cache_generations"})
    cache_bytes, cache_generations = selected["cache_bytes"], selected["cache_generations"]
    if (
        type(cache_bytes) is not int or not 65536 <= cache_bytes <= 1024 * 1024 * 1024
        or type(cache_generations) is not int or not 1 <= cache_generations <= 10000
    ):
        raise ValueError("workspace cache requires explicit bounded capacities")
    scopes = {(b.principal, b.target): b.scope for b in bindings}
    native = {(b.account, b.repo): b for b in observations}
    readers: list[tuple[str, str, NativeReadHandler]] = []
    for raw in _collection(selected["bindings"]):
        entry = _fields(raw, {"principal", "target", "command", "scope"})
        key = (entry["principal"], entry["target"])
        if key not in scopes or any((p, t) == key for p, t, _ in readers):
            raise ValueError("workspace requires one existing admitted caller/target")
        scope = _scope({**_fields(entry["scope"], {
            "provider", "endpoint", "project_id", "repository_id", "principal_id",
        }), "number": 1})
        scope.pop("number")
        admitted = scopes[key]
        observation = native[(admitted.account, admitted.repo)]
        if observation.credential_command is None:
            raise ValueError("workspace requires the existing typed credential owner")
        readers.append((*key, NativeReadHandler(
            command=_argv(entry["command"]), credential_command=observation.credential_command,
            repo=admitted.repo, scope=scope, binding_ref=entry["target"],
            max_work_seconds=seconds,
        )))
    return WorkspaceConfig(tuple(readers), cache_bytes, cache_generations)


def load_config(path: Path) -> HostConfig:
    path = _path(str(path))
    with path.open("rb") as stream:
        raw = stream.read(_LIMIT + 1)
    if len(raw) > _LIMIT:
        raise ValueError("host configuration exceeds its byte budget")
    value = _fields(json_object(raw), {
        "schema", "version", "root", "namespace", "fleet", "gateway", "slot",
        "gateway_origin", "proof_key_file", "access_token_file", "bindings", "observations",
    }, {"gateway_ca", "interval", "operation_seconds", "drain_timeout", "workspaces"})
    if value["schema"] != "agent-pull-requests.service" or (
        type(value["version"]) is not int or value["version"] != 1
    ):
        raise ValueError("unsupported host configuration")
    for name in ("fleet", "gateway", "slot"):
        identifier(value[name])
    bindings = []
    for binding in _collection(value["bindings"]):
        binding = _fields(binding, {"principal", "target", "consumer", "account", "repo"})
        bindings.append(WatchScopeBinding(
            binding["principal"], binding["target"],
            ConsumerScope(binding["consumer"], binding["account"], binding["repo"]),
        ))
    observations = []
    for binding in _collection(value["observations"]):
        binding = _fields(binding, {
            "account", "repo", "command", "credential_command",
        }, {"prefix"})
        observations.append(NativeObservationBinding(
            binding["account"], binding["repo"], binding["command"],
            _argv(binding["prefix"]) if "prefix" in binding else None,
            _argv(binding["credential_command"]),
        ))
    # Reject ambiguous mappings before acquiring custody or reading credentials.
    if len({(b.principal, b.target) for b in bindings}) != len(bindings) or (
        len({(b.account, b.repo) for b in observations}) != len(observations)
    ) or any((b.scope.account, b.scope.repo) not in {
        (o.account, o.repo) for o in observations
    } for b in bindings):
        raise ValueError("ambiguous or missing native observation scope")
    seconds = _seconds(value.get("operation_seconds", 60))
    config = HostConfig(
        root=_path(value["root"]), namespace=value["namespace"],
        fleet=value["fleet"], gateway=value["gateway"], slot=value["slot"],
        gateway_origin=origin(value["gateway_origin"]),
        proof_key_file=_path(value["proof_key_file"]),
        access_token_file=_path(value["access_token_file"]),
        bindings=tuple(bindings), observations=tuple(observations),
        gateway_ca=_path(value["gateway_ca"]) if "gateway_ca" in value else None,
        interval=_seconds(value.get("interval", 5)),
        operation_seconds=seconds,
        drain_timeout=_seconds(value.get("drain_timeout", 60)),
        workspaces=_workspaces(value["workspaces"], bindings, observations, seconds)
        if "workspaces" in value else None,
    )
    config.store()
    return config
