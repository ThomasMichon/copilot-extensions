"""Resource-claim record encoding, including opaque producer metadata."""
from __future__ import annotations

from collections.abc import Callable

from .tracking_claims import ResourceClaim


def parse_mapping(raw: dict, *, claim_factory: Callable[..., ResourceClaim]) -> ResourceClaim:
    kind = str(raw.get("kind", "worktree")) or "worktree"
    state_raw = raw.get("state")
    state = state_raw if state_raw in ("active", "at-rest", "released", "abandoned") else "active"
    return claim_factory(
        kind=kind,
        ref=str(raw.get("ref", "")),
        created_at=str(raw.get("created_at", "")),
        state=state,
        note=str(raw.get("note", "")),
        handoff_bundle=str(raw.get("handoff_bundle", "")),
        producer=raw.get("producer"),
        producer_present="producer" in raw,
    )


def yaml_mapping(claim: ResourceClaim) -> dict[str, object]:
    data: dict[str, object] = {"kind": claim.kind, "ref": claim.ref}
    if claim.created_at:
        data["created_at"] = claim.created_at
    if claim.state and claim.state != "active":
        data["state"] = claim.state
    if claim.note:
        data["note"] = claim.note
    if claim.handoff_bundle:
        data["handoff_bundle"] = claim.handoff_bundle
    if claim.has_producer:
        data["producer"] = claim.producer
    return data
