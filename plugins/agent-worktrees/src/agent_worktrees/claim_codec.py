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
        metadata_opaque=(
            raw.get("metadata_opaque", False) is not False
            or "state" in raw and state_raw not in ("active", "at-rest", "released", "abandoned")
            or not isinstance(raw.get("kind", "worktree"), str) or not raw.get("kind", "worktree")
            or not isinstance(raw.get("ref", ""), str)
            or not raw.get("ref") and "producer" not in raw
        ),
    )


def parse_list(raw: object, *, claim_factory: Callable[..., ResourceClaim]) -> list[ResourceClaim]:
    """Retain malformed collections/items as explicit conservative placeholders."""
    if not isinstance(raw, list):
        return [claim_factory(metadata_opaque=True)]
    return [
        parse_mapping(item, claim_factory=claim_factory)
        if isinstance(item, dict) else claim_factory(metadata_opaque=True)
        for item in raw
    ]


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
    if claim.metadata_opaque:
        data["metadata_opaque"] = True
    return data
