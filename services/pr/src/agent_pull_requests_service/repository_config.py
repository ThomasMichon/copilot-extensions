"""Passive parsing of explicit local repository-cache hosting selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import sys

from agent_pull_requests.repository_contract import MAX_TARGETS, RepositoryIdentity
from agent_pull_requests.watch_observer import NativeObservationBinding
from agent_pull_requests.watch_gateway import WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope


def _fields(value: object, names: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError("invalid local repository configuration")
    return value


def _argv(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 64 or any(
        not isinstance(item, str) or not item or "\0" in item for item in value
    ) or not Path(value[0]).is_absolute() or sum(len(s) for s in value) > 16384:
        raise ValueError("explicit bounded native argv required")
    return tuple(value)


@dataclass(frozen=True)
class RepositoryConfig:
    identities: tuple[tuple[str, RepositoryIdentity], ...]
    observations: tuple[NativeObservationBinding, ...]
    bindings: tuple[WatchScopeBinding, ...]
    peers: tuple[tuple[int, str], ...]
    socket_path: Path
    definitions: Any = None
    sponsors: tuple[dict[str, Any], ...] = ()

    @classmethod
    def parse(cls, value: object, namespace: str, root: Path, *, filtered: bool = False) -> RepositoryConfig:
        selected = _fields(value, {"protocol", "socket", "peers", "repositories", "bindings"} | (
            {"definitions", "sponsors"} if filtered else set()
        ))
        expected = "agent-pull-requests.filtered-population/v1" if filtered else "agent-pull-requests.repository-cache/v1"
        if selected["protocol"] != expected:
            raise ValueError("unsupported repository cache protocol")
        if sys.platform != "linux":
            raise ValueError("local repository ingress requires Linux SO_PEERCRED")
        socket_path = Path(selected["socket"])
        if not socket_path.is_absolute() or socket_path.parent != root or len(str(socket_path).encode()) > 100:
            raise ValueError("local socket must be a short absolute child of the dedicated state root")
        collections = ("peers", "repositories", "bindings")
        if any(not isinstance(selected[name], list) or not 1 <= len(selected[name]) <= (
            MAX_TARGETS if name == "repositories" else 128
        ) for name in collections):
            raise ValueError("invalid local configuration cardinality")
        peers: list[tuple[int, str]] = []
        from fleet_manager.gateway_service_context import identifier
        for raw in selected["peers"]:
            peer = _fields(raw, {"uid", "principal"})
            if type(peer["uid"]) is not int or peer["uid"] < 0:
                raise ValueError("local peer requires an exact OS uid")
            peers.append((peer["uid"], identifier(peer["principal"])))
        if len({uid for uid, _ in peers}) != len(peers):
            raise ValueError("ambiguous local peer mapping")
        identities: list[tuple[str, RepositoryIdentity]] = []
        observations: list[NativeObservationBinding] = []
        for raw in selected["repositories"]:
            entry = _fields(raw, {"target", "identity", "prefix", "credential_command", "source_config"})
            target = identifier(entry["target"])
            identity = RepositoryIdentity.parse(entry["identity"])
            if not filtered and identity.provider not in ("github", "gitea"):
                raise ValueError("unselected population driver unsupported")
            if identity.namespace != namespace:
                raise ValueError("repository selection namespace differs from host")
            prefix, credential = _argv(entry["prefix"]), _argv(entry["credential_command"])
            identities.append((target, identity))
            observations.append(NativeObservationBinding(
                identity.key, identity.repo, prefix[0], prefix, credential,
                identity, entry["source_config"],
            ))
        if len({target for target, _ in identities}) != len(identities) or (
            len({identity.key for _, identity in identities}) != len(identities)
        ):
            raise ValueError("ambiguous repository selection")
        by_target = dict(identities)
        bindings: list[WatchScopeBinding] = []
        for raw in selected["bindings"]:
            binding = _fields(raw, {"principal", "target", "consumer"})
            if binding["target"] not in by_target or binding["principal"] not in {
                principal for _, principal in peers
            }:
                raise ValueError("local binding has no admitted principal/target")
            identity = by_target[binding["target"]]
            bindings.append(WatchScopeBinding(
                binding["principal"], binding["target"],
                ConsumerScope(binding["consumer"], identity.key, identity.repo),
            ))
        if len({(b.principal, b.target) for b in bindings}) != len(bindings):
            raise ValueError("ambiguous local consumer binding")
        definitions, sponsors = None, []
        if filtered:
            from pr_filters import Definitions
            from agent_pull_requests.filtered_state import text
            definitions = Definitions.parse(selected["definitions"])
            if not isinstance(selected["sponsors"], list) or len(selected["sponsors"]) > 128:
                raise ValueError("invalid bounded startup sponsors")
            requests = set()
            admitted = {(b.principal, b.target): b.scope.consumer for b in bindings}
            for raw in selected["sponsors"]:
                sponsor = _fields(raw, {"principal", "target", "request_id", "definition", "ttl"})
                key = sponsor["principal"], sponsor["target"]
                if key not in admitted or type(sponsor["ttl"]) is not int or not 1 <= sponsor["ttl"] <= 86400:
                    raise ValueError("startup sponsor requires admitted consumer and finite lifetime")
                request = admitted[key], text(sponsor["request_id"])
                if request in requests:
                    raise ValueError("ambiguous startup sponsorship")
                requests.add(request)
                definitions.resolve(text(sponsor["definition"]))
                sponsors.append(dict(sponsor))
        return cls(tuple(identities), tuple(observations), tuple(bindings), tuple(peers), socket_path,
                   definitions, tuple(sponsors))
