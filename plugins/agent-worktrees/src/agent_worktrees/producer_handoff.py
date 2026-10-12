"""Recipient-first, recoverable local acceptance for disposable producers."""

from __future__ import annotations

import copy
from contextlib import ExitStack

from . import claim_handoffs as handoffs, resource_producer as producer, tracking


def accept_producers(bundle_id: str, *, actor: str):
    """Persist the active recipient before discharging any source obligation."""
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
        if not handoffs._same_machine(source_ref.machine, consumer_ref.machine, project=consumer_ref.project):
            raise handoffs.ClaimHandoffError("producer handoff cannot mutate a remote owner")
        source_path = handoffs._record_path(source_ref)
        consumer_path = handoffs._record_path(consumer_ref)
        with ExitStack() as stack:
            for record_path in handoffs._ordered_lock_paths(source_path, consumer_path):
                stack.enter_context(tracking._RecordLock(record_path, require_sidecar=True))
            source = handoffs._load_record_from_path(source_path, role="source")
            consumer = handoffs._load_record_from_path(consumer_path, role="consumer")
            for record, ref in ((source, source_ref), (consumer, consumer_ref)):
                if (record.machine, record.repo, record.worktree_id) != (ref.machine, ref.project, ref.worktree_id):
                    raise handoffs.ClaimHandoffError("producer handoff record identity mismatch")
            if consumer.status in {"finalized", "complete", "completed"}:
                raise handoffs.ClaimHandoffError("producer consumer is terminal; ownership cannot be reopened")
            evidence = {
                "accepted": True, "bundle": bundle.bundle_id,
                "source": bundle.source, "consumer": bundle.consumer,
            }
            source_by_ref = {c.ref: c for c in source.resources}
            consumer_by_ref = {c.ref: c for c in consumer.resources}
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
                if received is not None:
                    expected_note = handoffs._note_with_provenance(snapshot["note"], bundle.source)
                    if received.kind != snapshot["kind"] or received.note != expected_note or received.created_at != snapshot["created_at"]:
                        raise handoffs.ClaimHandoffError("producer consumer claim collision")
                    if bundle.state == "accepted":
                        # A retry must not resurrect a recipient that has since closed
                        # or transferred its obligation onward.
                        if not producer.valid_metadata(received.producer) or received.producer["token"] != snapshot["producer"]["token"]:
                            raise handoffs.ClaimHandoffError("accepted producer consumer metadata is invalid")
                    elif received.producer != snapshot["producer"] or received.handoff_bundle not in ("", bundle.bundle_id):
                        raise handoffs.ClaimHandoffError("producer consumer metadata collision")
                elif bundle.state == "accepted" or original.producer == transferred:
                    raise handoffs.ClaimHandoffError("accepted producer recipient obligation is missing")
            if bundle.state == "accepted":
                dirty = False
                for claim in consumer.resources:
                    if claim.handoff_bundle == bundle.bundle_id:
                        claim.handoff_bundle = ""
                        dirty = True
                if dirty:
                    tracking.save_record(consumer, consumer_path, preserve_handoff_reservations=False)
                return bundle
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
            for claim in consumer.resources:
                if claim.handoff_bundle == bundle.bundle_id:
                    claim.handoff_bundle = ""
            tracking.save_record(consumer, consumer_path, preserve_handoff_reservations=False)
            for snapshot in bundle.claims:
                for record, event, direction, counterpart in (
                    (source, "released", "to", bundle.consumer),
                    (consumer, "claimed", "from", bundle.source),
                ):
                    handoffs.claim_history.record_bundle_transfer(
                        snapshot, event=event, worktree_id=record.worktree_id,
                        machine=record.machine, bundle_id=bundle.bundle_id,
                        direction=direction, counterpart=counterpart, project=record.repo,
                    )
            return accepted
