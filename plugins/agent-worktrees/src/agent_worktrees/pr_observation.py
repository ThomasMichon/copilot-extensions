"""Additive, provider-neutral PR gate evidence shared by status and observation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Literal

from .providers.observation_credentials import CredentialSource


GateState = Literal["satisfied", "blocked", "pending", "unknown", "waiting"]
GateOwner = Literal["agent", "human", "external"]


@dataclass(frozen=True)
class Gate:
    id: str
    kind: str
    state: GateState
    owner: GateOwner
    summary: str
    required: bool = True
    waiting_for: tuple[str, ...] = ()
    url: str = ""


@dataclass(frozen=True)
class Vote:
    actor_id: str
    user: str
    vote: int
    required: bool = False
    is_group: bool = False
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApprovalEvent:
    id: str
    actor_id: str
    vote: int
    at: str


@dataclass(frozen=True)
class Discussion:
    id: str
    status: str
    iteration: int | None = None
    comment_id: str = ""
    updated_at: str = ""
    author_id: str = ""
    file_path: str = ""
    line: int | None = None
    awaiting_author: bool = False
    revision_fingerprint: str = ""


@dataclass(frozen=True)
class Policy:
    id: str
    name: str
    state: str
    required: bool
    type_id: str = ""
    kind: str = "agent"
    context_name: str = ""
    context_genre: str = ""
    source_commit: str = ""
    iteration: int | None = None
    stale: bool = False
    build_id: int | None = None
    url: str = ""
    failed_tasks: tuple[dict, ...] = ()


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Observation:
    provider: str
    repo: str
    number: int
    api_base: str
    head_sha: str
    iteration: int | None = None
    source_push_at: str = ""
    votes: tuple[Vote, ...] = ()
    approval_events: tuple[ApprovalEvent, ...] = ()
    discussions: tuple[Discussion, ...] = ()
    policies: tuple[Policy, ...] = ()
    gates: tuple[Gate, ...] = ()
    complete: bool = False
    errors: tuple[str, ...] = ()
    guidance: str = ""
    votes_known: bool = True
    schema: str = field(default="agent-worktrees.pr-observation", init=False)
    version: int = field(default=1, init=False)

    def to_dict(self) -> dict:
        result = asdict(self)
        required = [gate for gate in self.gates if gate.required]
        human = [gate.id for gate in required if gate.owner == "human" and gate.state != "satisfied"]
        unknown = [gate.id for gate in required if gate.state == "unknown"]
        pending = [gate.id for gate in required
                   if gate.owner != "human" and gate.state == "pending"]
        actionable = [gate.id for gate in required
                      if gate.owner == "agent" and gate.state == "blocked"]
        human_only_wait = all(
            gate.state == "satisfied" or gate.owner == "human"
            or (gate.state == "waiting" and gate.waiting_for
                and set(gate.waiting_for) <= set(human))
            for gate in required
        )
        result.update(
            human_required=human, unknown=unknown, pending=pending, agent_actionable=actionable,
            ready_for_human=bool(self.complete and human and not unknown and human_only_wait),
            all_required_satisfied=bool(self.complete and required
                                        and all(g.state == "satisfied" for g in required)),
            fingerprints={
                "votes": fingerprint([asdict(v) for v in self.votes]),
                "approvals": fingerprint([asdict(e) for e in self.approval_events]),
                "discussions": fingerprint([asdict(t) for t in self.discussions]),
                "policies": fingerprint([asdict(p) for p in self.policies]),
                "gates": fingerprint([asdict(g) for g in self.gates]),
            },
        )
        return result


def configured_snapshot(
    provider, repo, number, *, prcfg, api_base="", token=None,
    credential_source: CredentialSource | None = None,
):
    credentials = {}
    if credential_source is not None:
        from .providers.base import ProviderError
        if token is not None or provider.name not in ("github", "azure-devops"):
            raise ProviderError("Renewable observation credentials require a supported read-only binding")
        credentials["credential_source"] = credential_source
    method = getattr(provider, "get_configured_snapshot", None)
    if method is not None:
        snapshot = method(repo, number, api_base=api_base, token=token,
                          observation=getattr(prcfg, "observation", None), **credentials)
        if getattr(snapshot, "observation", None) is not None:
            from .pr_contract import title_is_wip
            holds = {label.casefold() for label in getattr(prcfg, "hold_labels", ())}
            held = bool(holds.intersection(label.casefold() for label in snapshot.labels))
            blocked = (held or snapshot.draft
                       or title_is_wip(snapshot.title, getattr(prcfg, "wip_title_prefixes", ())))
            gate = Gate("publication_ready", "publication", "blocked" if blocked else "satisfied",
                        "agent", "Draft, WIP or repository hold" if blocked else "Published and not held")
            snapshot = replace(snapshot, observation=replace(
                snapshot.observation, gates=(*snapshot.observation.gates, gate)))
        return snapshot
    return provider.get_snapshot(repo, number, api_base=api_base, token=token, **credentials)
