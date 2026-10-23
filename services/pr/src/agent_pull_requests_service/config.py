"""Explicit trusted host configuration, distinct from remote request input."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_pull_requests.watch_gateway import WatchScopeBinding
from agent_pull_requests.watch_observer import NativeObservationBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore, json_object
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


def _fields(value: object, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or not required <= value.keys() or (
        value.keys() - required - optional
    ):
        raise ValueError("invalid host configuration fields")
    return value


def _collection(value: object) -> list:
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

    @property
    def status_path(self) -> Path:
        return self.root / "service-status.json"

    def store(self) -> ReplayStore:
        return ReplayStore(self.root, self.namespace)


def load_config(path: Path) -> HostConfig:
    path = _path(str(path))
    with path.open("rb") as stream:
        raw = stream.read(_LIMIT + 1)
    if len(raw) > _LIMIT:
        raise ValueError("host configuration exceeds its byte budget")
    value = _fields(json_object(raw), {
        "schema", "version", "root", "namespace", "fleet", "gateway", "slot",
        "gateway_origin", "proof_key_file", "access_token_file", "bindings", "observations",
    }, {"gateway_ca", "interval", "operation_seconds", "drain_timeout"})
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
    config = HostConfig(
        root=_path(value["root"]), namespace=value["namespace"],
        fleet=value["fleet"], gateway=value["gateway"], slot=value["slot"],
        gateway_origin=origin(value["gateway_origin"]),
        proof_key_file=_path(value["proof_key_file"]),
        access_token_file=_path(value["access_token_file"]),
        bindings=tuple(bindings), observations=tuple(observations),
        gateway_ca=_path(value["gateway_ca"]) if "gateway_ca" in value else None,
        interval=_seconds(value.get("interval", 5)),
        operation_seconds=_seconds(value.get("operation_seconds", 60)),
        drain_timeout=_seconds(value.get("drain_timeout", 60)),
    )
    config.store()
    return config
