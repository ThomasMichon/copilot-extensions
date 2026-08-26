"""Durable transaction intent for affirmative resource-claim handoff.

A bundle is an offer, never an ownership authority. Until a later acceptance
transaction commits, the source worktree's ordinary claim ledger remains the
single source of truth. This module owns only the machine-local intent/state
needed to offer, inspect, decline, and cancel an exact bundle safely.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from . import config as cfg
from . import tracking


REGISTRY_VERSION = 1
BUNDLE_VERSION = 1
STATES = frozenset({
    "offering", "offered", "accepted",
    "declining", "declined", "cancelling", "cancelled",
})
TERMINAL_STATES = frozenset({"accepted", "declined", "cancelled"})


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
    if ref.machine != machine:
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
    if record.status in {"finalizing", "finalized", "orphaned"}:
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
                if source_record.status in {"finalizing", "finalized", "orphaned"}:
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
    return _canonical_ref(_qualified_ref(left, "actor")) == _canonical_ref(
        _qualified_ref(right, "bundle actor")
    )


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
                # The registry terminal state is authoritative. Retry only
                # best-effort-cleans a marker that may remain after a crash;
                # source claims may legitimately have progressed or vanished.
                source_ref = _qualified_ref(bundle.source, "bundle source")
                source_path = _record_path(source_ref)
                if source_path.exists():
                    with tracking._RecordLock(
                            source_path, require_sidecar=True):
                        source_record = tracking.load_record(source_path)
                        dirty = False
                        for claim in source_record.resources:
                            if claim.handoff_bundle == bundle.bundle_id:
                                claim.handoff_bundle = ""
                                dirty = True
                        if dirty:
                            tracking.save_record(
                                source_record, source_path,
                                preserve_handoff_reservations=False)
                return bundle
            terminal_retry = bundle.state == action
            if bundle.state in TERMINAL_STATES and not terminal_retry:
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
                        by_ref[ref].handoff_bundle not in {
                            "", bundle.bundle_id}
                        for ref in refs if ref in by_ref):
                    raise ClaimHandoffError(
                        "source claims are reserved by a different bundle")
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
                if not terminal_retry:
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
                else:
                    changed = bundle
                dirty = False
                for ref in refs:
                    if (ref in by_ref
                            and by_ref[ref].handoff_bundle == bundle.bundle_id):
                        by_ref[ref].handoff_bundle = ""
                        dirty = True
                if dirty:
                    tracking.save_record(
                        source_record, source_path,
                        preserve_handoff_reservations=False)
                return changed
    except ClaimHandoffError:
        raise
    except Exception as exc:
        raise ClaimHandoffError(
            f"cannot transition claim bundle {bundle_id}: {exc}"
        ) from exc
