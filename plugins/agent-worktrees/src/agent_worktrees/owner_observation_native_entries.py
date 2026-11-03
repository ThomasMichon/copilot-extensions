"""Semantic entries from complete, strongly excluded canonical save evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from . import owner_observation_contract as c
from .tracking_save_capture import Identity, SaveReceipt

DOMAINS = {
    "claim_add": "claims",
    "claim_release": "claims",
    "claim_settle": "claims",
    "status_disposition_write": "disposition",
    "effort_bind": "binding",
    "effort_release": "binding",
}


def scope(identity: Identity) -> tuple:
    return (
        identity.project,
        identity.worktree_id,
        identity.incarnation,
        identity.machine,
        identity.machine_identity_version,
        identity.environment,
        identity.execution_machine,
    )


def digest(receipt: SaveReceipt) -> str:
    value = asdict(receipt)
    for state in ("before", "after"):
        for item in value[state]["domains"]:
            raw = item["data"]
            item["data"] = None if raw is None else raw.decode("utf-8")
        fence = value[state]["fence"]
        value[state]["fence"] = None if fence is None else fence.decode("utf-8")
    # Each part has its own existing bound; this hash is not a wire envelope.
    result = hashlib.sha256()
    for key in sorted(value):
        raw = json.dumps(value[key], sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        if len(raw) > c.BODY_BYTES:
            raise c.ObservationError("native receipt hash input exceeds bound", "oversized")
        result.update(len(raw).to_bytes(8, "big"))
        result.update(raw)
    return result.hexdigest()


def validate(receipt: SaveReceipt, verb: str) -> None:
    before, after = receipt.before.identity, receipt.after.identity
    if (
        verb not in DOMAINS
        or receipt.coverage != "selected-fields"
        or receipt.exclusion != "sidecar"
        or before is None
        or after is None
        or scope(before) != scope(after)
        or not after.incarnation
        or after.machine_identity_version != 1
        or not after.execution_machine
        or not after.environment
        or receipt.saved_sha256 is None
        or receipt.saved_bytes is None
        or receipt.actor_qualification != "unknown"
        or receipt.actor_id is not None
    ):
        raise c.ObservationError("canonical receipt is not native-qualified", "unqualified-native")
    if type(receipt.saved_bytes) is not int or not 1 <= receipt.saved_bytes <= 256 * 1024:
        raise c.ObservationError("invalid native saved-byte bound")
    c.identity(receipt.receipt_id, "save receipt")
    c.timestamp(receipt.captured_at)
    if len(receipt.saved_sha256) != 64 or any(
        value not in "0123456789abcdef" for value in receipt.saved_sha256
    ):
        raise c.ObservationError("invalid canonical save digest")


def _claims(value: object) -> dict[str, dict]:
    if not isinstance(value, list) or len(value) > 64:
        raise c.ObservationError("claims evidence is omitted", "unqualified-native")
    result = {}
    for item in value:
        if not isinstance(item, dict):
            raise c.ObservationError("invalid saved claim")
        ref = c.reference(item.get("ref"), "saved resource")
        if item.get("has_producer") or item.get("metadata_opaque") or ref in result:
            raise c.ObservationError(
                "producer/ambiguous claims are not native-qualified", "unqualified-native"
            )
        result[ref] = item
    return result


def entries(receipt: SaveReceipt, verb: str) -> list[dict]:
    validate(receipt, verb)
    domain = DOMAINS[verb]
    before = next(item for item in receipt.before.domains if item.domain == domain)
    after = next(item for item in receipt.after.domains if item.domain == domain)
    if before.data is None or after.data is None:
        raise c.ObservationError("selected native evidence is omitted", "unqualified-native")
    old, new = before.value(), after.value()
    changes: list[tuple[str, str, object, object, str | None]] = []
    if domain == "claims":
        prior, current = _claims(old), _claims(new)
        for ref in sorted(prior.keys() | current.keys()):
            a, b = prior.get(ref), current.get(ref)
            if c.encode(a) == c.encode(b):
                continue
            kind = (
                "claim-added"
                if b is not None
                and b.get("state") == "active"
                and (a is None or a.get("state") == "released")
                else "claim-released"
                if a is not None
                and b is not None
                and a.get("state") in {"active", "at-rest"}
                and b.get("state") == "released"
                else "claim-settled"
                if a is not None
                and b is not None
                and a.get("state") == "active"
                and b.get("state") == "at-rest"
                else "source-event"
            )
            changes.append((kind, "claim-record-saved", a, b, ref))
    elif c.encode(old) != c.encode(new):
        if domain == "binding":
            a, b = old["active_effort"], new["active_effort"]
            if a is not None:
                changes.append(("effort-released", "effort-pointer-released", a, None, a["path"]))
            if b is not None:
                changes.append(("effort-bound", "effort-pointer-bound", None, b, b["path"]))
        else:
            changes.append(("source-event", "disposition-saved", old, new, None))
    if len(changes) > 64:
        raise c.ObservationError("native change batch exceeds entry bound", "oversized")
    proof_hash = digest(receipt)
    native_generation = hashlib.sha256(
        c.utf8(c.encode(list(scope(receipt.after.identity))))
    ).hexdigest()
    result = []
    for number, (kind, native, old_value, new_value, ref) in enumerate(changes):
        item = {
            "kind": kind,
            "evidence_kind": "persisted-mutation",
            "native_kind": native,
            "occurred_at": None,
            "observed_at": receipt.captured_at,
            "actor": {
                "kind": "unknown",
                "id": None,
                "qualification": "unknown",
                "session_id": None,
            },
            "correlation": {
                **dict.fromkeys(c.CORRELATION_FIELDS),
                "session_id": receipt.after.identity.head_session_id,
            },
            "relations": []
            if ref is None
            else [{"kind": "resource", "ref": ref, "qualification": "authoritative"}],
            "record_fence": {
                "saved_sha256": receipt.saved_sha256,
                "incarnation": receipt.after.identity.incarnation,
            },
            "source_ref": {
                "source": "canonical-tracking-save",
                "generation": native_generation,
                "record_id": f"{receipt.receipt_id}:{domain}:{number}",
                "payload_ref": None,
            },
            "summary": native,
            "details": None,
            "reason": None,
            "phase": None,
            "payload": {
                "before": old_value,
                "after": new_value,
                "receipt_sha256": proof_hash,
                "capture_head_session_id": receipt.after.identity.head_session_id,
            },
        }
        c.entry(item, post_save=True)
        result.append(item)
    c.encode(result, c.BODY_BYTES)
    return result
