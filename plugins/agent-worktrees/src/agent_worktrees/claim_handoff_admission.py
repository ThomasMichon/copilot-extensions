"""Claim-bundle admission, including immutable producer handoff constraints."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .claim_handoffs import ClaimBundle
    from .tracking_claims import ClaimRef, ResourceClaim


def bundle_state(bundle: ClaimBundle, state: str, *, reason: str | None = None) -> ClaimBundle:
    from .tracking import _now_iso

    return replace(
        bundle, state=state, updated_at=_now_iso(),
        reason=bundle.reason if reason is None else reason,
    )


def parse_source_owner_refs(
    raw: object, claims: Sequence[dict], source: ClaimRef, *,
    error_type: type[Exception],
) -> tuple[tuple[str, str], ...]:
    from .tracking_claims import parse_claim_ref

    if not isinstance(raw, dict):
        raise error_type("claim bundle source-owner proof must be a mapping")
    worktree_refs = {claim["ref"] for claim in claims if claim["kind"] == "worktree"}
    for child, owner in raw.items():
        if child not in worktree_refs or not isinstance(owner, str):
            raise error_type("claim bundle contains invalid source-owner proof")
        parsed = parse_claim_ref(owner)
        if parsed is None or not parsed.is_qualified or (
            parsed.project, parsed.worktree_id
        ) != (source.project, source.worktree_id):
            raise error_type("claim bundle source-owner proof names another worktree")
    return tuple(raw.items())


def snapshot_claim(claim: ResourceClaim) -> dict:
    snapshot = claim.to_dict()
    snapshot.pop("handoff_bundle", None)
    snapshot["state"] = claim.state or "active"
    return copy.deepcopy(snapshot)


def validate_producer_uniqueness(
    claims: Sequence[dict], *, error_type: type[Exception],
) -> None:
    """Reject ambiguous producer references and tokens without collapsing claims."""
    refs: dict[str, bool] = {}
    tokens: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            raise error_type("claim bundle entry must be a mapping")
        ref = claim.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            raise error_type("claim bundle entry requires a nonempty string ref")
        present = "producer" in claim
        if present and not isinstance(claim["producer"], dict):
            raise error_type(f"claim bundle producer metadata must be a mapping: {ref}")
        if ref in refs and (refs[ref] or present):
            raise error_type("ambiguous duplicate producer reference")
        refs[ref] = refs.get(ref, False) or present
        metadata = claim.get("producer")
        token = metadata.get("token") if isinstance(metadata, dict) else None
        if present and isinstance(token, str):
            if token in tokens:
                raise error_type("ambiguous duplicate producer token")
            tokens.add(token)


def validate_producer_offer(
    claims: Sequence[ResourceClaim], source: ClaimRef, consumer: ClaimRef,
    *, machine: str, error_type: type[Exception],
) -> None:
    from . import resource_producer
    producers = [claim for claim in claims if claim.has_producer]
    if not producers:
        return
    if len(producers) != len(claims):
        raise error_type("offer disposable producer claims in a separate bundle")
    _require_local_producer_actors(source, consumer, machine=machine, error_type=error_type)
    if any(not resource_producer.valid_metadata(claim.producer) or claim.producer["phase"] != "bound"
           or resource_producer.claim_ref(claim.producer) != claim.ref for claim in producers):
        raise error_type("only valid bound producer claims can be offered; pending obligations remain owned")
    validate_producer_uniqueness(
        [snapshot_claim(claim) for claim in claims], error_type=error_type,
    )


def _require_local_producer_actors(
    source: ClaimRef, consumer: ClaimRef, *, error_type: type[Exception],
    machine: str | None = None, accepted: bool = False,
) -> None:
    from . import config as cfg
    from .claim_handoff_accept_support import local_record_ref, same_local_records

    try:
        if machine is None:
            machine = cfg.load_config(include_control_plane_related_pr=False).machine
        local = (
            local_record_ref(consumer, machine=machine) if accepted
            else same_local_records(source, consumer, machine=machine)
        )
    except (OSError, ValueError) as exc:
        raise error_type(f"cannot establish producer admission authority: {exc}") from exc
    if not local:
        raise error_type("disposable producer handoff requires local source and consumer")


def validate_supported_claims(
    bundle: ClaimBundle, *, supported_kinds: frozenset[str],
    qualified_ref: Callable[[str, str], ClaimRef], error_type: type[Exception],
) -> None:
    from . import resource_producer
    producer_claims = [c for c in bundle.claims if "producer" in c]
    if producer_claims:
        if len(producer_claims) != len(bundle.claims):
            raise error_type("offer disposable producer claims in a separate bundle")
        _require_local_producer_actors(
            qualified_ref(bundle.source, "source"), qualified_ref(bundle.consumer, "consumer"),
            accepted=bundle.state == "accepted", error_type=error_type,
        )
        if any(not resource_producer.valid_metadata(c["producer"]) or c["producer"]["phase"] != "bound"
               or resource_producer.claim_ref(c["producer"]) != c["ref"] for c in producer_claims):
            raise error_type("only valid bound producer claims can be transferred; pending obligations remain owned")
        validate_producer_uniqueness(bundle.claims, error_type=error_type)
        return
    unsupported = sorted(
        snapshot["kind"]
        for snapshot in bundle.claims
        if snapshot["kind"] not in supported_kinds
    )
    if unsupported:
        raise error_type(
            "claim bundle contains unsupported claim kinds: " + ", ".join(unsupported)
        )
