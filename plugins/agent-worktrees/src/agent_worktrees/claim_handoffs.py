"""Durable transaction intent for affirmative resource-claim handoff.

A bundle is an offer, never an ownership authority. Until a later acceptance
transaction commits, the source worktree's ordinary claim ledger remains the
single source of truth. This module owns only the machine-local intent/state
needed to offer, inspect, accept, decline, and cancel an exact bundle safely.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from . import claim_history, config as cfg, tracking
from .claim_handoff_accept_support import (
    acquire_bundle_fence,
    load_accept_bundle,
    release_bundle_fence,
    remote_accept_source,
    same_machine as _same_machine,
)
from .lease_config import load_lease_settings
from .lease_store import GitLeaseStore, LeaseLost

REGISTRY_VERSION = 1
BUNDLE_VERSION = 1
STATES = frozenset({
    "offering", "offered", "accepting", "accepted",
    "declining", "declined", "cancelling", "cancelled",
})
TERMINAL_STATES = frozenset({"accepted", "declined", "cancelled"})
_LEASEABLE_KINDS = frozenset({"codespace", "container", "task"})
_LEDGER_ONLY_KINDS = frozenset({"pr", "workdir"})
_SUPPORTED_ACCEPT_KINDS = frozenset({"worktree", *_LEASEABLE_KINDS, *_LEDGER_ONLY_KINDS})
_PROVENANCE_PREFIX = "prior owner: "


class ClaimHandoffError(RuntimeError):
    """A safe, user-actionable claim-handoff failure."""


@dataclass(frozen=True)
class ClaimBundle:
    """One exact claim-bundle offer and its durable state."""

    bundle_id: str
    state: str
    source: str
    consumer: str
    claims: tuple[dict[str, str], ...]
    offered_at: str
    updated_at: str
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        """Render the stable JSON/YAML bundle schema."""
        data: dict[str, object] = {
            "version": BUNDLE_VERSION,
            "id": self.bundle_id,
            "state": self.state,
            "source": self.source,
            "consumer": self.consumer,
            "claims": [dict(claim) for claim in self.claims],
            "offered_at": self.offered_at,
            "updated_at": self.updated_at,
        }
        if self.reason:
            data["reason"] = self.reason
        return data


def registry_path() -> Path:
    """Return the machine-local claim-handoff intent registry."""
    return cfg.install_dir() / "claim-handoffs.yaml"


def _qualified_ref(ref: str, field: str) -> tracking.ClaimRef:
    parsed = tracking.parse_claim_ref(ref)
    if parsed is None or not parsed.is_qualified or parsed.session:
        raise ClaimHandoffError(
            f"{field} must be machine/project/worktree_id (got {ref!r})"
        )
    return parsed


def _canonical_ref(parsed: tracking.ClaimRef) -> str:
    return tracking.format_claim_ref(
        parsed.machine, parsed.project, parsed.worktree_id
    )


def _bundle_from_dict(raw: object) -> ClaimBundle:
    if not isinstance(raw, dict):
        raise ClaimHandoffError("claim-handoff registry contains a non-mapping bundle")
    if raw.get("version") != BUNDLE_VERSION:
        raise ClaimHandoffError(
            f"unsupported claim-bundle version: {raw.get('version')!r}"
        )
    bundle_id = raw.get("id")
    state = raw.get("state")
    source = raw.get("source")
    consumer = raw.get("consumer")
    offered_at = raw.get("offered_at")
    updated_at = raw.get("updated_at")
    claims_raw = raw.get("claims")
    if not all(isinstance(value, str) and value for value in (
        bundle_id, state, source, consumer, offered_at, updated_at
    )):
        raise ClaimHandoffError("claim-handoff registry contains an incomplete bundle")
    if state not in STATES:
        raise ClaimHandoffError(f"invalid claim-bundle state: {state!r}")
    _qualified_ref(source, "bundle source")
    _qualified_ref(consumer, "bundle consumer")
    if not isinstance(claims_raw, list) or not claims_raw:
        raise ClaimHandoffError("claim bundle must contain at least one claim")
    claims: list[dict[str, str]] = []
    refs: set[str] = set()
    for item in claims_raw:
        if not isinstance(item, dict):
            raise ClaimHandoffError("claim bundle contains a non-mapping claim")
        kind = item.get("kind")
        ref = item.get("ref")
        created_at = item.get("created_at", "")
        claim_state = item.get("state", "active")
        note = item.get("note", "")
        if not isinstance(kind, str) or not kind or not isinstance(ref, str) or not ref:
            raise ClaimHandoffError("claim bundle contains an incomplete claim")
        if ref in refs:
            raise ClaimHandoffError(f"claim bundle contains duplicate ref: {ref}")
        if not all(isinstance(value, str) for value in (
            created_at, claim_state, note
        )):
            raise ClaimHandoffError(f"claim bundle contains invalid metadata: {ref}")
        refs.add(ref)
        claims.append({
            "kind": kind,
            "ref": ref,
            "created_at": created_at,
            "state": claim_state,
            "note": note,
        })
    reason = raw.get("reason", "")
    if not isinstance(reason, str):
        raise ClaimHandoffError("claim bundle reason must be a string")
    return ClaimBundle(
        bundle_id=bundle_id,
        state=state,
        source=source,
        consumer=consumer,
        claims=tuple(claims),
        offered_at=offered_at,
        updated_at=updated_at,
        reason=reason,
    )


def _load_registry_strict(path: Path) -> list[ClaimBundle]:
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(tracking._read_text_with_retry(path))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ClaimHandoffError(
            f"cannot read claim-handoff registry {path}: {exc}"
        ) from exc
    if not isinstance(data, dict) or data.get("version") != REGISTRY_VERSION:
        raise ClaimHandoffError(f"invalid claim-handoff registry: {path}")
    raw_bundles = data.get("bundles")
    if not isinstance(raw_bundles, list):
        raise ClaimHandoffError(f"invalid claim-handoff bundle list: {path}")
    bundles = [_bundle_from_dict(raw) for raw in raw_bundles]
    ids = [bundle.bundle_id for bundle in bundles]
    if len(set(ids)) != len(ids):
        raise ClaimHandoffError("claim-handoff registry contains duplicate bundle ids")
    return bundles


def _save_registry(path: Path, bundles: list[ClaimBundle]) -> None:
    content = yaml.safe_dump(
        {
            "version": REGISTRY_VERSION,
            "bundles": [bundle.to_dict() for bundle in bundles],
        },
        sort_keys=False,
    )
    try:
        tracking._atomic_write(path, content)
    except OSError as exc:
        raise ClaimHandoffError(
            f"cannot write claim-handoff registry {path}: {exc}"
        ) from exc


def _record_path(ref: tracking.ClaimRef) -> Path:
    return cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml"


def _load_actor_record(
    ref: tracking.ClaimRef, *, role: str, machine: str
) -> tuple[Path, tracking.WorktreeRecord]:
    if ref.machine != machine and not tracking.legacy_wsl_owner_ref_is_local(ref):
        raise ClaimHandoffError(
            f"{role} {ref.canonical()} is cross-machine; Phase 1 supports "
            "same-machine handoff only"
        )
    path = _record_path(ref)
    if not path.exists():
        raise ClaimHandoffError(f"{role} worktree not found: {ref.canonical()}")
    try:
        record = tracking.load_record(path)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise ClaimHandoffError(
            f"cannot read {role} worktree record {path}: {exc}"
        ) from exc
    if record.worktree_id != ref.worktree_id:
        raise ClaimHandoffError(f"{role} worktree record identity mismatch: {path}")
    # ``finalized`` is deliberately not blocked: finalize is a non-terminal
    # safe-to-prune assertion, not a frozen end state -- a finalized worktree
    # may still legitimately act as a handoff source or consumer for
    # follow-up work. Only the in-flight ``finalizing`` RMW window and a
    # genuinely broken ``orphaned`` record are frozen.
    if record.status in {"finalizing", "orphaned"}:
        raise ClaimHandoffError(
            f"{role} worktree {ref.canonical()} is {record.status}"
        )
    return path, record


def _claim_snapshot(claim: tracking.ResourceClaim) -> dict[str, str]:
    return {
        "kind": claim.kind,
        "ref": claim.ref,
        "created_at": claim.created_at,
        "state": claim.state or "active",
        "note": claim.note,
    }


def _bundle_state(bundle: ClaimBundle, state: str, *, reason: str | None = None) -> ClaimBundle:
    return replace(
        bundle,
        state=state,
        updated_at=tracking._now_iso(),
        reason=bundle.reason if reason is None else reason,
    )


def _load_record_from_path(path: Path, *, role: str) -> tracking.WorktreeRecord:
    try:
        record = tracking.load_record(path)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise ClaimHandoffError(
            f"cannot read {role} worktree record {path}: {exc}"
        ) from exc
    if record.status in {"finalizing", "orphaned"}:
        raise ClaimHandoffError(
            f"{role} worktree "
            f"{tracking.format_claim_ref(record.machine, record.repo, record.worktree_id)} "
            f"is {record.status}"
        )
    return record


def _ordered_lock_paths(*paths: Path) -> tuple[Path, ...]:
    unique: dict[str, Path] = {}
    for path in paths:
        unique[str(path)] = path
    return tuple(unique[key] for key in sorted(unique))


def _note_with_provenance(note: str, source: str) -> str:
    marker = f"{_PROVENANCE_PREFIX}{source}"
    if marker in note:
        return note
    return f"{note}; {marker}" if note else marker


def _transferred_claim(
    snapshot: dict[str, str], *, source: str
) -> tracking.ResourceClaim:
    return tracking.ResourceClaim(
        kind=snapshot["kind"],
        ref=snapshot["ref"],
        created_at=snapshot.get("created_at", ""),
        state="active",
        note=_note_with_provenance(snapshot.get("note", ""), source),
    )


def _claim_matches(
    claim: tracking.ResourceClaim, snapshot: dict[str, str], *, source: str
) -> bool:
    return (
        claim.kind == snapshot["kind"]
        and claim.ref == snapshot["ref"]
        and claim.created_at == snapshot.get("created_at", "")
        and claim.state == "active"
        and claim.note == _note_with_provenance(snapshot.get("note", ""), source)
        and not claim.handoff_bundle
    )


def _accept_child_path(
    snapshot: dict[str, str], *, bundle_consumer: tracking.ClaimRef
) -> Path:
    parsed = tracking.parse_claim_ref(snapshot["ref"])
    if parsed is None or not parsed.is_qualified:
        raise ClaimHandoffError(
            "worktree claim ref must be qualified "
            f"machine/project/worktree_id (got {snapshot['ref']!r})"
        )
    if parsed.machine != bundle_consumer.machine:
        raise ClaimHandoffError(
            f"worktree claim {snapshot['ref']} targets machine {parsed.machine}, "
            f"but bundle consumer lives on {bundle_consumer.machine}"
        )
    return _record_path(parsed)


def _validate_supported_claims(bundle: ClaimBundle) -> None:
    unsupported = sorted(
        snapshot["kind"]
        for snapshot in bundle.claims
        if snapshot["kind"] not in _SUPPORTED_ACCEPT_KINDS
    )
    if unsupported:
        raise ClaimHandoffError(
            "claim bundle contains unsupported claim kinds: " + ", ".join(unsupported)
        )


def _parse_remote_bundle(stdout: str) -> ClaimBundle | None:
    if not stdout:
        return None
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(stdout[start:end + 1])
    except (TypeError, ValueError):
        return None
    return _bundle_from_dict(payload)


def accept_source(bundle_id: str, *, actor: str) -> ClaimBundle:
    """Settle the source-side half of an acceptance on the source machine."""
    if not bundle_id:
        raise ClaimHandoffError("missing claim-bundle id")
    path = registry_path()
    try:
        with tracking._RecordLock(path, require_sidecar=True):
            bundles, index, bundle = load_accept_bundle(
                path, bundle_id, actor=actor,
                load_registry=_load_registry_strict,
                same_worktree=_same_worktree,
                validate_bundle=_validate_supported_claims,
                error_type=ClaimHandoffError)
            if bundle.state == "accepted":
                return bundle
            source_ref = _qualified_ref(bundle.source, "bundle source")
            source_path = _record_path(source_ref)
            if not source_path.exists():
                raise ClaimHandoffError(f"source worktree not found: {bundle.source}")
            if bundle.state == "offered":
                bundle = _bundle_state(bundle, "accepting")
                bundles[index] = bundle
                _save_registry(path, bundles)
            with tracking._RecordLock(source_path, require_sidecar=True):
                source_record = _load_record_from_path(source_path, role="source")
                source_by_ref = {claim.ref: claim for claim in source_record.resources}
                refs = [snapshot["ref"] for snapshot in bundle.claims]
                missing = [ref for ref in refs if ref not in source_by_ref]
                if missing:
                    if bundle.state == "accepting" and len(missing) == len(refs):
                        accepted = _bundle_state(bundle, "accepted")
                        bundles[index] = accepted
                        _save_registry(path, bundles)
                        return accepted
                    raise ClaimHandoffError(
                        "bundle source claims are missing from the source ledger: "
                        + ", ".join(sorted(missing))
                    )
                for snapshot in bundle.claims:
                    claim = source_by_ref[snapshot["ref"]]
                    if claim.handoff_bundle not in {"", bundle.bundle_id}:
                        raise ClaimHandoffError(
                            f"source claim {snapshot['ref']} is reserved by another bundle"
                        )
                    if _claim_snapshot(claim) != snapshot:
                        raise ClaimHandoffError(
                            f"bundle source claim metadata changed: {snapshot['ref']}"
                        )
                for snapshot in bundle.claims:
                    source_record.resources.remove(source_by_ref[snapshot["ref"]])
                tracking.save_record(source_record, source_path, preserve_handoff_reservations=False)
                for snapshot in bundle.claims:
                    claim_history.record_bundle_transfer(
                        snapshot, event="released", worktree_id=source_record.worktree_id,
                        machine=source_record.machine, bundle_id=bundle.bundle_id,
                        direction="to", counterpart=bundle.consumer, project=source_record.repo,
                    )
                accepted = _bundle_state(bundle, "accepted")
                bundles[index] = accepted
                _save_registry(path, bundles)
                return accepted
    except ClaimHandoffError:
        raise
    except Exception as exc:
        raise ClaimHandoffError(
            f"cannot settle source-side accept for claim bundle {bundle_id}: {exc}"
        ) from exc


def _finish_accept_consumer_side(bundle: ClaimBundle, *, machine: str) -> ClaimBundle:
    consumer_ref = _qualified_ref(bundle.consumer, "bundle consumer")
    if consumer_ref.machine != machine and not tracking.legacy_wsl_owner_ref_is_local(consumer_ref):
        raise ClaimHandoffError(
            f"consumer worktree {bundle.consumer} is on {consumer_ref.machine}, "
            f"not local machine {machine}"
        )
    consumer_path = _record_path(consumer_ref)
    if not consumer_path.exists():
        raise ClaimHandoffError(f"consumer worktree not found: {bundle.consumer}")
    child_paths = [_accept_child_path(snapshot, bundle_consumer=consumer_ref)
                   for snapshot in bundle.claims if snapshot["kind"] == "worktree"]
    try:
        with ExitStack() as stack:
            for record_path in _ordered_lock_paths(consumer_path, *child_paths):
                stack.enter_context(tracking._RecordLock(record_path, require_sidecar=True))
            consumer_record = _load_record_from_path(consumer_path, role="consumer")
            child_records = {
                str(child_path): _load_record_from_path(child_path, role="child")
                for child_path in child_paths
            }
            consumer_by_ref = {claim.ref: claim for claim in consumer_record.resources}
            stores: dict[str, GitLeaseStore] = {}

            for snapshot in bundle.claims:
                ref = snapshot["ref"]
                kind = snapshot["kind"]
                consumer_claim = consumer_by_ref.get(ref)
                if consumer_claim and not _claim_matches(
                    consumer_claim, snapshot, source=bundle.source
                ):
                    raise ClaimHandoffError(
                        f"consumer already holds a conflicting claim: {ref}"
                    )
                if kind == "worktree":
                    child_path = str(
                        _accept_child_path(snapshot, bundle_consumer=consumer_ref)
                    )
                    owner_ref = child_records[child_path].owner_ref or ""
                    if owner_ref not in {bundle.source, bundle.consumer}:
                        raise ClaimHandoffError(
                            f"child worktree {snapshot['ref']} is owned by "
                            f"{owner_ref or 'nobody'}, "
                            "not this bundle's source/consumer"
                        )
                elif kind in _LEASEABLE_KINDS:
                    store = stores.setdefault(kind, GitLeaseStore(load_lease_settings()))
                    try:
                        lease = store.inspect(kind, ref)
                    except Exception as exc:
                        raise ClaimHandoffError(
                            f"cannot inspect {kind} lease {ref}: {exc}"
                        ) from exc
                    if lease is None:
                        raise ClaimHandoffError(
                            f"{kind} claim {ref} has no mirrored lease to transfer"
                        )
                    if lease.record.holder not in {bundle.source, bundle.consumer}:
                        raise ClaimHandoffError(
                            f"{kind} lease {ref} is held by "
                            f"{lease.record.holder}, not this bundle's "
                            "source/consumer"
                        )
                    if lease.record.holder == bundle.source:
                        try:
                            store.transfer(kind, ref, lease.oid, bundle.consumer,
                                           context=lease.record.context)
                        except LeaseLost as exc:
                            raise ClaimHandoffError(
                                f"cannot transfer {kind} lease {ref}: {exc}"
                            ) from exc

            new_refs: list[str] = []
            for snapshot in bundle.claims:
                if snapshot["ref"] not in consumer_by_ref:
                    transferred = _transferred_claim(snapshot, source=bundle.source)
                    tracking.add_resource_claim(consumer_record, transferred, save=False)
                    consumer_by_ref[transferred.ref] = transferred
                    new_refs.append(snapshot["ref"])
                if snapshot["kind"] == "worktree":
                    child_path = str(
                        _accept_child_path(snapshot, bundle_consumer=consumer_ref)
                    )
                    child_records[child_path].owner_ref = bundle.consumer

            tracking.save_record(consumer_record, consumer_path, preserve_handoff_reservations=False)
            # Before child saves -- a later failure there must never suppress this.
            for snapshot in bundle.claims:
                if snapshot["ref"] in new_refs:
                    claim_history.record_bundle_transfer(
                        snapshot, event="claimed", worktree_id=consumer_record.worktree_id,
                        machine=consumer_record.machine, bundle_id=bundle.bundle_id,
                        direction="from", counterpart=bundle.source, project=consumer_record.repo,
                    )
            for child_path, child_record in child_records.items():
                tracking.save_record(child_record, Path(child_path), preserve_handoff_reservations=False)
            return bundle
    except ClaimHandoffError:
        raise
    except Exception as exc:
        raise ClaimHandoffError(
            f"cannot finalize consumer-side accept for claim bundle {bundle.bundle_id}: {exc}"
        ) from exc


def offer(
    source: str,
    consumer: str,
    refs: list[str],
    *,
    machine: str,
    id_factory: Callable[[], str] | None = None,
) -> tuple[ClaimBundle, bool]:
    """Offer exact active source claims; return ``(bundle, created)``.

    An identical live offer is returned unchanged, making command retries
    idempotent. Any overlapping live offer is rejected so a claim cannot be
    nested into competing bundles.
    """
    source_ref = _qualified_ref(source, "source")
    consumer_ref = _qualified_ref(consumer, "consumer")
    source_canonical = _canonical_ref(source_ref)
    consumer_canonical = _canonical_ref(consumer_ref)
    if source_canonical == consumer_canonical:
        raise ClaimHandoffError("source and consumer must be different worktrees")
    if not refs:
        raise ClaimHandoffError("claims handoff offer requires at least one <ref>")
    if any(not isinstance(ref, str) or not ref for ref in refs):
        raise ClaimHandoffError("claim refs must be non-empty strings")
    if len(set(refs)) != len(refs):
        raise ClaimHandoffError("claims handoff offer contains duplicate refs")
    requested = tuple(sorted(refs))
    path = registry_path()
    try:
        with tracking._RecordLock(path, require_sidecar=True):
            bundles = _load_registry_strict(path)
            identical: ClaimBundle | None = None
            identical_index: int | None = None
            for index, bundle in enumerate(bundles):
                if (bundle.state not in {"offering", "offered"}
                        or bundle.source != source_canonical):
                    continue
                existing = tuple(sorted(claim["ref"] for claim in bundle.claims))
                if existing == requested and bundle.consumer == consumer_canonical:
                    identical = bundle
                    identical_index = index
                    break
                overlap = sorted(set(existing).intersection(requested))
                if overlap:
                    raise ClaimHandoffError(
                        "claims already belong to an offered bundle: "
                        + ", ".join(overlap)
                    )
            source_path, _ = _load_actor_record(
                source_ref, role="source", machine=machine
            )
            _load_actor_record(consumer_ref, role="consumer", machine=machine)
            with tracking._RecordLock(source_path, require_sidecar=True):
                source_record = tracking.load_record(source_path)
                if source_record.status in {"finalizing", "orphaned"}:
                    raise ClaimHandoffError(
                        f"source worktree {source_canonical} is "
                        f"{source_record.status}"
                    )
                by_ref = {claim.ref: claim for claim in source_record.resources}
                missing = [ref for ref in refs if ref not in by_ref]
                if missing:
                    raise ClaimHandoffError(
                        "source does not own claims: " + ", ".join(sorted(missing))
                    )
                inactive = [
                    ref for ref in refs if not by_ref[ref].is_unsettled
                ]
                if inactive:
                    raise ClaimHandoffError(
                        "only active finalize-blocking claims may be offered: "
                        + ", ".join(sorted(inactive))
                    )
                if identical is not None:
                    snapshots = tuple(
                        _claim_snapshot(by_ref[ref]) for ref in sorted(refs)
                    )
                    if snapshots != identical.claims:
                        raise ClaimHandoffError(
                            "existing offer no longer matches source claim "
                            "metadata")
                    mismatched = [
                        ref for ref in refs
                        if by_ref[ref].handoff_bundle not in {
                            "", identical.bundle_id}
                    ]
                    if mismatched:
                        raise ClaimHandoffError(
                            "existing offer lost its source reservations: "
                            + ", ".join(sorted(mismatched))
                        )
                    if identical.state == "offered":
                        return identical, False
                    for ref in refs:
                        by_ref[ref].handoff_bundle = identical.bundle_id
                    tracking.save_record(
                        source_record, source_path,
                        preserve_handoff_reservations=False)
                    offered = ClaimBundle(
                        bundle_id=identical.bundle_id,
                        state="offered",
                        source=identical.source,
                        consumer=identical.consumer,
                        claims=identical.claims,
                        offered_at=identical.offered_at,
                        updated_at=tracking._now_iso(),
                    )
                    bundles[identical_index] = offered
                    _save_registry(path, bundles)
                    return offered, False
                reserved = [
                    ref for ref in refs if by_ref[ref].handoff_bundle
                ]
                if reserved:
                    raise ClaimHandoffError(
                        "claims already reserved by another handoff: "
                        + ", ".join(sorted(reserved))
                    )
                snapshots = tuple(
                    _claim_snapshot(by_ref[ref]) for ref in sorted(refs)
                )
                now = tracking._now_iso()
                make_id = id_factory or (lambda: secrets.token_hex(16))
                bundle_id = make_id()
                if not bundle_id or any(
                        bundle.bundle_id == bundle_id for bundle in bundles):
                    raise ClaimHandoffError(
                        "could not allocate a unique claim-bundle id")
                bundle = ClaimBundle(
                    bundle_id=bundle_id,
                    state="offering",
                    source=source_canonical,
                    consumer=consumer_canonical,
                    claims=snapshots,
                    offered_at=now,
                    updated_at=now,
                )
                bundles.append(bundle)
                _save_registry(path, bundles)
                for ref in refs:
                    by_ref[ref].handoff_bundle = bundle_id
                tracking.save_record(
                    source_record, source_path,
                    preserve_handoff_reservations=False)
                offered = ClaimBundle(
                    bundle_id=bundle.bundle_id,
                    state="offered",
                    source=bundle.source,
                    consumer=bundle.consumer,
                    claims=bundle.claims,
                    offered_at=bundle.offered_at,
                    updated_at=tracking._now_iso(),
                )
                bundles[-1] = offered
                _save_registry(path, bundles)
                return offered, True
    except ClaimHandoffError:
        raise
    except Exception as exc:
        raise ClaimHandoffError(f"cannot offer claim bundle: {exc}") from exc


def show(bundle_id: str) -> ClaimBundle:
    """Load one bundle by id, failing closed on registry corruption."""
    if not bundle_id:
        raise ClaimHandoffError("missing claim-bundle id")
    bundles = _load_registry_strict(registry_path())
    match = next(
        (bundle for bundle in bundles if bundle.bundle_id == bundle_id), None
    )
    if match is None:
        raise ClaimHandoffError(f"claim bundle not found: {bundle_id}")
    return match


def accept(
    bundle_id: str,
    *,
    actor: str,
    machine: str,
) -> ClaimBundle:
    """Accept a bundle as its consumer and atomically transfer ownership."""
    if not bundle_id:
        raise ClaimHandoffError("missing claim-bundle id")
    path = registry_path()
    try:
        with tracking._RecordLock(path, require_sidecar=True):
            _, _, bundle = load_accept_bundle(
                path, bundle_id, actor=actor,
                load_registry=_load_registry_strict,
                same_worktree=_same_worktree,
                validate_bundle=_validate_supported_claims,
                error_type=ClaimHandoffError)
        source_ref = _qualified_ref(bundle.source, "bundle source")
        consumer_ref = _qualified_ref(bundle.consumer, "bundle consumer")
        if _same_machine(source_ref.machine, consumer_ref.machine):
            if bundle.state != "accepted":
                bundle = accept_source(bundle_id, actor=actor)
            return _finish_accept_consumer_side(bundle, machine=machine)
        store, fence = acquire_bundle_fence(
            bundle_id=bundle.bundle_id, source=bundle.source,
            consumer=bundle.consumer, actor=actor,
            error_type=ClaimHandoffError)
        try:
            with tracking._RecordLock(path, require_sidecar=True):
                _, _, bundle = load_accept_bundle(
                    path, bundle_id, actor=actor,
                    load_registry=_load_registry_strict,
                    same_worktree=_same_worktree,
                    validate_bundle=_validate_supported_claims,
                    error_type=ClaimHandoffError)
            source_ref = _qualified_ref(bundle.source, "bundle source")
            if bundle.state != "accepted":
                if _same_machine(source_ref.machine, machine):
                    bundle = accept_source(bundle_id, actor=actor)
                else:
                    bundle = remote_accept_source(
                        source_machine=source_ref.machine, source_project=source_ref.project,
                        bundle_id=bundle.bundle_id, actor=actor,
                        parse_bundle=_parse_remote_bundle, error_type=ClaimHandoffError)
            return _finish_accept_consumer_side(bundle, machine=machine)
        finally:
            release_bundle_fence(store, bundle_id=bundle.bundle_id, token=fence.oid,
                                 error_type=ClaimHandoffError)
    except ClaimHandoffError:
        raise
    except LeaseLost as exc:
        raise ClaimHandoffError(
            f"claim-bundle fence for {bundle_id} was lost: {exc}"
        ) from exc
    except Exception as exc:
        raise ClaimHandoffError(
            f"cannot accept claim bundle {bundle_id}: {exc}"
        ) from exc


def active_bundle_ids_for_source(source: str) -> tuple[str, ...]:
    """Return nonterminal bundle ids whose creator is ``source``."""
    canonical = _canonical_ref(_qualified_ref(source, "source"))
    return tuple(
        bundle.bundle_id
        for bundle in _load_registry_strict(registry_path())
        if bundle.source == canonical and bundle.state not in TERMINAL_STATES
    )


def active_bundle_for_claim(source: str, claim_ref: str) -> str:
    """Return the nonterminal bundle reserving ``claim_ref``, or empty."""
    canonical = _canonical_ref(_qualified_ref(source, "source"))
    for bundle in _load_registry_strict(registry_path()):
        if bundle.source != canonical or bundle.state in TERMINAL_STATES:
            continue
        if any(claim["ref"] == claim_ref for claim in bundle.claims):
            return bundle.bundle_id
    return ""


def _same_worktree(left: str, right: str) -> bool:
    left_ref = _qualified_ref(left, "actor")
    right_ref = _qualified_ref(right, "bundle actor")
    return tracking.claim_refs_equivalent(left_ref, right_ref)


def transition(
    bundle_id: str,
    *,
    actor: str,
    action: str,
    reason: str,
) -> ClaimBundle:
    """Decline as consumer or cancel as source, atomically and idempotently."""
    if action not in {"declined", "cancelled"}:
        raise ClaimHandoffError(f"unsupported claim-bundle transition: {action}")
    if not reason.strip():
        verb = "decline" if action == "declined" else "cancel"
        raise ClaimHandoffError(f"claims handoff {verb} requires --reason")
    path = registry_path()
    try:
        with tracking._RecordLock(path, require_sidecar=True):
            bundles = _load_registry_strict(path)
            index = next(
                (i for i, bundle in enumerate(bundles)
                 if bundle.bundle_id == bundle_id),
                None,
            )
            if index is None:
                raise ClaimHandoffError(f"claim bundle not found: {bundle_id}")
            bundle = bundles[index]
            expected_actor = (
                bundle.consumer if action == "declined" else bundle.source
            )
            if not _same_worktree(actor, expected_actor):
                role = "consumer" if action == "declined" else "source"
                raise ClaimHandoffError(
                    f"only bundle {role} {expected_actor} may mark it {action}"
                )
            if bundle.state == action:
                source_ref = _qualified_ref(bundle.source, "bundle source")
                source_path = _record_path(source_ref)
                if source_path.exists():
                    with tracking._RecordLock(source_path, require_sidecar=True):
                        source_record = tracking.load_record(source_path)
                        dirty = False
                        for claim in source_record.resources:
                            if claim.handoff_bundle == bundle.bundle_id:
                                claim.handoff_bundle = ""
                                dirty = True
                        if dirty:
                            tracking.save_record(
                                source_record,
                                source_path,
                                preserve_handoff_reservations=False,
                            )
                return bundle
            if bundle.state in TERMINAL_STATES:
                raise ClaimHandoffError(
                    f"claim bundle {bundle_id} is already {bundle.state}"
                )
        if (
            _qualified_ref(bundle.source, "bundle source").machine
            != _qualified_ref(bundle.consumer, "bundle consumer").machine
        ):
            store, fence = acquire_bundle_fence(
                bundle_id=bundle.bundle_id, source=bundle.source,
                consumer=bundle.consumer, actor=actor,
                error_type=ClaimHandoffError)
        else:
            store = fence = None
        try:
            with tracking._RecordLock(path, require_sidecar=True):
                bundles = _load_registry_strict(path)
                index = next(
                    (i for i, bundle in enumerate(bundles)
                     if bundle.bundle_id == bundle_id),
                    None,
                )
                if index is None:
                    raise ClaimHandoffError(f"claim bundle not found: {bundle_id}")
                bundle = bundles[index]
                starting_state = bundle.state
                expected_actor = (
                    bundle.consumer if action == "declined" else bundle.source
                )
                if not _same_worktree(actor, expected_actor):
                    role = "consumer" if action == "declined" else "source"
                    raise ClaimHandoffError(
                        f"only bundle {role} {expected_actor} may mark it {action}"
                    )
                if bundle.state == action:
                    return bundle
                if bundle.state in TERMINAL_STATES:
                    raise ClaimHandoffError(
                        f"claim bundle {bundle_id} is already {bundle.state}"
                    )
                intermediate_state = (
                    "declining" if action == "declined" else "cancelling")
                allowed = {"offered", intermediate_state, action}
                if action == "cancelled":
                    allowed.update({"offering", "declining"})
                if bundle.state not in allowed:
                    raise ClaimHandoffError(
                        f"claim bundle {bundle_id} is {bundle.state}, not offered")
                transition_reason = (
                    bundle.reason
                    if bundle.state in {intermediate_state, action}
                    else reason.strip())
                if bundle.state in {"offered", "offering"}:
                    bundle = ClaimBundle(
                        bundle_id=bundle.bundle_id,
                        state=intermediate_state,
                        source=bundle.source,
                        consumer=bundle.consumer,
                        claims=bundle.claims,
                        offered_at=bundle.offered_at,
                        updated_at=tracking._now_iso(),
                        reason=transition_reason,
                    )
                    bundles[index] = bundle
                    _save_registry(path, bundles)
                source_ref = _qualified_ref(bundle.source, "bundle source")
                source_path = _record_path(source_ref)
                source_missing = not source_path.exists()
                cancel_recovery = (
                    action == "cancelled"
                    and starting_state in {
                        "offering", "declining", "cancelling"})
                if source_missing:
                    if not cancel_recovery:
                        raise ClaimHandoffError(
                            f"source worktree not found: {bundle.source}")
                    changed = ClaimBundle(
                        bundle_id=bundle.bundle_id,
                        state=action,
                        source=bundle.source,
                        consumer=bundle.consumer,
                        claims=bundle.claims,
                        offered_at=bundle.offered_at,
                        updated_at=tracking._now_iso(),
                        reason=transition_reason,
                    )
                    bundles[index] = changed
                    _save_registry(path, bundles)
                    return changed
                with tracking._RecordLock(source_path, require_sidecar=True):
                    source_record = tracking.load_record(source_path)
                    if (source_record.status in {
                            "finalizing", "finalized", "orphaned"}
                            and not cancel_recovery):
                        raise ClaimHandoffError(
                            f"source worktree {bundle.source} is "
                            f"{source_record.status}")
                    by_ref = {claim.ref: claim for claim in source_record.resources}
                    refs = [claim["ref"] for claim in bundle.claims]
                    invalid = [
                        ref for ref in refs
                        if ref not in by_ref
                        or by_ref[ref].handoff_bundle not in {
                            "", bundle.bundle_id}
                    ]
                    if invalid and not cancel_recovery:
                        raise ClaimHandoffError(
                            "bundle source reservations are missing or changed: "
                            + ", ".join(sorted(invalid))
                        )
                    if not cancel_recovery and any(
                            _claim_snapshot(by_ref[ref]) != snapshot
                            for ref, snapshot in zip(
                                refs, bundle.claims, strict=True)
                            if ref in by_ref):
                        raise ClaimHandoffError(
                            "bundle source claim metadata changed")
                    if not cancel_recovery and any(
                            by_ref[ref].state != "active"
                            for ref in refs if ref in by_ref):
                        raise ClaimHandoffError(
                            "bundle source claim disposition changed")
                    changed = ClaimBundle(
                        bundle_id=bundle.bundle_id,
                        state=action,
                        source=bundle.source,
                        consumer=bundle.consumer,
                        claims=bundle.claims,
                        offered_at=bundle.offered_at,
                        updated_at=tracking._now_iso(),
                        reason=transition_reason,
                    )
                    bundles[index] = changed
                    _save_registry(path, bundles)
                    dirty = False
                    for ref in refs:
                        if (ref in by_ref
                                and by_ref[ref].handoff_bundle == bundle.bundle_id):
                            by_ref[ref].handoff_bundle = ""
                            dirty = True
                    if dirty:
                        tracking.save_record(
                            source_record,
                            source_path,
                            preserve_handoff_reservations=False,
                        )
                    return changed
        finally:
            if store is not None and fence is not None:
                release_bundle_fence(store, bundle_id=bundle.bundle_id, token=fence.oid,
                                     error_type=ClaimHandoffError)
    except ClaimHandoffError:
        raise
    except LeaseLost as exc:
        raise ClaimHandoffError(
            f"claim-bundle fence for {bundle_id} was lost: {exc}"
        ) from exc
    except Exception as exc:
        raise ClaimHandoffError(
            f"cannot transition claim bundle {bundle_id}: {exc}"
        ) from exc
