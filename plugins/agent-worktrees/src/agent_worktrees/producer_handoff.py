"""Recipient-first, recoverable local acceptance for disposable producers."""

from __future__ import annotations

import copy
from contextlib import ExitStack
from pathlib import Path

from . import claim_handoffs as handoffs, config as cfg, resource_producer as producer, tracking
from .claim_handoff_admission import validate_producer_uniqueness


def _validate_ledger(record: tracking.WorktreeRecord) -> None:
    validate_producer_uniqueness(
        [handoffs._claim_snapshot(claim) for claim in record.resources],
        error_type=handoffs.ClaimHandoffError,
    )


def _immutable_metadata(metadata: dict) -> dict:
    return {
        key: value for key, value in metadata.items()
        if key not in {"phase", "closeout", "transfer"}
    }


def _validate_consumer(
    bundle: handoffs.ClaimBundle, consumer: tracking.WorktreeRecord,
) -> dict[str, tracking.ResourceClaim]:
    _validate_ledger(consumer)
    by_ref = {claim.ref: claim for claim in consumer.resources}
    by_token = {
        claim.producer["token"]: claim.ref for claim in consumer.resources
        if claim.has_producer and isinstance(claim.producer, dict)
        and isinstance(claim.producer.get("token"), str)
    }
    for snapshot in bundle.claims:
        ref = snapshot["ref"]
        if by_token.get(snapshot["producer"]["token"], ref) != ref:
            raise handoffs.ClaimHandoffError("producer consumer token collision")
        received = by_ref.get(ref)
        if received is None:
            if bundle.state == "accepted":
                raise handoffs.ClaimHandoffError("accepted producer recipient obligation is missing")
            continue
        expected_note = handoffs._note_with_provenance(snapshot["note"], bundle.source)
        if received.kind != snapshot["kind"] or received.note != expected_note or received.created_at != snapshot["created_at"]:
            raise handoffs.ClaimHandoffError("producer consumer claim collision")
        if bundle.state == "accepted":
            if (
                not received.has_producer
                or not producer.valid_metadata(received.producer)
                or received.producer["phase"] not in {"bound", "closed", "transferred"}
                or producer.claim_ref(received.producer) != ref
                or _immutable_metadata(received.producer) != _immutable_metadata(snapshot["producer"])
            ):
                raise handoffs.ClaimHandoffError("accepted producer consumer metadata is invalid")
        elif received.producer != snapshot["producer"] or received.handoff_bundle not in ("", bundle.bundle_id):
            raise handoffs.ClaimHandoffError("producer consumer metadata collision")
    return by_ref


def _clear_consumer_markers(
    bundle: handoffs.ClaimBundle, consumer: tracking.WorktreeRecord, consumer_path: Path,
) -> None:
    dirty = False
    refs = {snapshot["ref"] for snapshot in bundle.claims}
    for claim in consumer.resources:
        if claim.ref in refs and claim.handoff_bundle == bundle.bundle_id:
            claim.handoff_bundle = ""
            dirty = True
    if dirty:
        tracking.save_record(consumer, consumer_path, preserve_handoff_reservations=False)


def _record_matches_ref(record: tracking.WorktreeRecord, ref: tracking.ClaimRef) -> bool:
    return (
        (record.repo, record.worktree_id) == (ref.project, ref.worktree_id)
        and handoffs._same_worktree(
            tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id),
            handoffs._canonical_ref(ref),
        )
    )


def accept_producers(bundle_id: str, *, actor: str, machine: str | None = None):
    """Persist the active recipient before discharging any source obligation."""
    if machine is None:
        try:
            machine = cfg.load_config(include_control_plane_related_pr=False).machine
        except (OSError, ValueError) as exc:
            raise handoffs.ClaimHandoffError(
                f"cannot establish producer handoff caller identity: {exc}"
            ) from exc
    path = handoffs.registry_path()
    with tracking._RecordLock(path, require_sidecar=True):
        bundles, index, bundle = handoffs.load_accept_bundle(
            path, bundle_id, actor=actor, load_registry=handoffs._load_registry_strict,
            same_worktree=handoffs._same_worktree,
            validate_bundle=handoffs._validate_supported_claims,
            error_type=handoffs.ClaimHandoffError,
        )
        source_ref = handoffs._qualified_ref(bundle.source, "source")
        consumer_ref = handoffs._qualified_ref(bundle.consumer, "consumer")
        if not handoffs.local_record_ref(consumer_ref, machine=machine):
            raise handoffs.ClaimHandoffError("producer handoff cannot mutate a remote owner")
        consumer_path = handoffs._record_path(consumer_ref)
        if bundle.state == "accepted":
            with tracking._RecordLock(consumer_path, require_sidecar=True):
                consumer = handoffs._load_record_from_path(consumer_path, role="consumer")
                if not _record_matches_ref(consumer, consumer_ref):
                    raise handoffs.ClaimHandoffError("producer handoff record identity mismatch")
                _validate_consumer(bundle, consumer)
                _clear_consumer_markers(bundle, consumer, consumer_path)
                return bundle
        if not handoffs.same_local_records(source_ref, consumer_ref, machine=machine):
            raise handoffs.ClaimHandoffError("producer handoff cannot mutate a remote owner")
        source_path = handoffs._record_path(source_ref)
        with ExitStack() as stack:
            for record_path in handoffs._ordered_lock_paths(source_path, consumer_path):
                stack.enter_context(tracking._RecordLock(record_path, require_sidecar=True))
            source = handoffs._load_record_from_path(source_path, role="source")
            consumer = handoffs._load_record_from_path(consumer_path, role="consumer")
            for record, ref in ((source, source_ref), (consumer, consumer_ref)):
                if not _record_matches_ref(record, ref):
                    raise handoffs.ClaimHandoffError("producer handoff record identity mismatch")
            if consumer.status in producer.FROZEN_OWNER_STATUSES:
                raise handoffs.ClaimHandoffError(
                    f"producer consumer is {consumer.status}; ownership cannot be reopened"
                )
            evidence = {
                "accepted": True, "bundle": bundle.bundle_id,
                "source": bundle.source, "consumer": bundle.consumer,
            }
            _validate_ledger(source)
            source_by_ref = {c.ref: c for c in source.resources}
            consumer_by_ref = _validate_consumer(bundle, consumer)
            for snapshot in bundle.claims:
                ref = snapshot["ref"]
                original = source_by_ref.get(ref)
                received = consumer_by_ref.get(ref)
                transferred = {**snapshot["producer"], "phase": "transferred", "transfer": evidence}
                if original is None or original.producer not in (snapshot["producer"], transferred):
                    raise handoffs.ClaimHandoffError("producer source metadata changed or is missing")
                if original.producer == snapshot["producer"] and handoffs._claim_snapshot(original) != snapshot:
                    raise handoffs.ClaimHandoffError("producer source snapshot changed")
                if original.handoff_bundle not in ("", bundle.bundle_id):
                    raise handoffs.ClaimHandoffError("producer source is reserved by another bundle")
                if received is None and original.producer == transferred:
                    raise handoffs.ClaimHandoffError("accepted producer recipient obligation is missing")
            bundle = handoffs._bundle_state(bundle, "accepting")
            bundles[index] = bundle
            handoffs._save_registry(path, bundles)
            for snapshot in bundle.claims:
                if snapshot["ref"] not in consumer_by_ref:
                    received = tracking.ResourceClaim(
                        kind=snapshot["kind"], ref=snapshot["ref"],
                        created_at=snapshot["created_at"], state="active",
                        note=handoffs._note_with_provenance(snapshot["note"], bundle.source),
                        producer=copy.deepcopy(snapshot["producer"]), producer_present=True,
                        handoff_bundle=bundle.bundle_id,
                    )
                    consumer.resources.append(received)
            with producer.transaction(consumer_path):
                tracking.save_record(consumer, consumer_path, preserve_handoff_reservations=False)
            for snapshot in bundle.claims:
                original = source_by_ref[snapshot["ref"]]
                original.producer = {**snapshot["producer"], "phase": "transferred", "transfer": evidence}
                original.state, original.handoff_bundle = "released", ""
            with producer.transaction(source_path):
                tracking.save_record(source, source_path, preserve_handoff_reservations=False)
            accepted = handoffs._bundle_state(bundle, "accepted")
            bundles[index] = accepted
            handoffs._save_registry(path, bundles)
            _clear_consumer_markers(accepted, consumer, consumer_path)
            for snapshot in bundle.claims:
                for record, event, direction, counterpart in (
                    (source, "released", "to", bundle.consumer),
                    (consumer, "claimed", "from", bundle.source),
                ):
                    handoffs.claim_history.record_bundle_transfer(
                        snapshot, event=event, worktree_id=record.worktree_id,
                        machine=record.execution_machine, bundle_id=bundle.bundle_id,
                        direction=direction, counterpart=counterpart, project=record.repo,
                    )
            return accepted
