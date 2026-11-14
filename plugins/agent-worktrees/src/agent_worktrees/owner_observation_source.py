"""Detached, deliberately partial facts from one accepted resident roster row."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from . import owner_observation_contract as c
from .owner_observation_history import CanonicalObservation
from .owner_observation_runtime import observed_entry
from .worktrees_observation_contract import semantic_row
from .worktrees_projection import WorktreesProjection

if TYPE_CHECKING:
    from .worktrees_runtime import WorktreesRuntime

FIELDS = frozenset(
    """
id repo creation_nonce
status kind title activity summary active_effort follow_up paused owner_ref
claims_assets claims_links claims_link_count claims_links_complete claims_projection
controllers controller_revision reciprocal_relation current_profile_assignment
checkout_present interface origin last_session_id head_yielded
""".split()
)
TIMESTAMPS = frozenset(
    """
observed_at computed_at updated_at created_at started_at ended_at completed_at
activity_at status_note_at last_resumed_at
""".split()
)


def stable_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: stable_value(item) for key, item in value.items() if key not in TIMESTAMPS}
    if isinstance(value, list):
        return [stable_value(item) for item in value]
    return value


@dataclass(frozen=True)
class AcceptedObservation:
    record: CanonicalObservation
    revision: int


def compact_change(
    previous: CanonicalObservation,
    current: CanonicalObservation,
    batch: str,
    journal: str,
) -> dict:
    value = observed_entry(previous, current, batch, journal)
    before = c.utf8(c.encode(previous.facts, c.BODY_BYTES))
    after = c.utf8(c.encode(current.facts, c.BODY_BYTES))
    value["payload"] = {
        "facts_omitted": True,
        "before_sha256": hashlib.sha256(before).hexdigest(),
        "after_sha256": hashlib.sha256(after).hexdigest(),
        "before_utf8_bytes": len(before),
        "after_utf8_bytes": len(after),
        "capture_source_before": previous.source,
        "capture_source_after": current.source,
        "previous_availability": previous.availability,
        "availability": current.availability,
        "previous_completeness": previous.completeness,
        "completeness": current.completeness,
        "head_before": previous.head_session_id,
        "head_after": current.head_session_id,
    }
    c.entry(value)
    return value


def authority(
    owner: WorktreesRuntime, store: WorktreesProjection, *, read: bool = False,
) -> bool:
    elected = owner._read_elected if read else owner._elected
    if elected is not None and not elected():
        if not read and authority(owner, store, read=True):
            raise c.ObservationError(
                "observation writer is not elected; cached reads remain admitted", "writer-fenced",
            )
        return False
    with owner._lock:
        if owner.store is not store:
            return False
        if not owner._admitting:
            raise c.ObservationError("resident observation admission closed", owner._reason)
    with closing(
        sqlite3.connect(
            store.db_path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=0.1,
        )
    ) as conn:
        return conn.execute(
            "SELECT generation,writer_id FROM worktrees_projection_owner WHERE singleton=1"
        ).fetchone() == (store.generation, store._writer_id)


def previous_generation(path: Path) -> str | None:
    if path.is_symlink():
        raise c.ObservationError("linked optional observation index refused", "unavailable")
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1)
    ) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='owner_observation_owner'"
        ).fetchone()
        if exists is None:
            return None
        row = conn.execute(
            "SELECT generation FROM owner_observation_owner WHERE singleton=1"
        ).fetchone()
        return None if row is None else c.generation(row[0])


def selected(
    owner: WorktreesRuntime,
    store: WorktreesProjection,
    project: str,
    worktree: str,
    *,
    read: bool = True,
) -> AcceptedObservation:
    if not authority(owner, store, read=read):
        raise c.ObservationError("resident roster owner changed", "superseded")
    result = store.read(project, worktree_id=worktree, contract_version=2)
    publication = result["projection"]
    if publication["quality"] != "current" or publication["generation"] != store.generation:
        raise c.ObservationError("selected roster is not current", "unready")
    if len(result["worktrees"]) != 1 or result["worktrees"][0].get("id") != worktree:
        raise c.ObservationError(
            "selected exact worktree is absent from the accepted roster", "not-found"
        )
    row = result["worktrees"][0]
    nonce = row.get("creation_nonce")
    if not isinstance(nonce, str) or not nonce:
        raise c.ObservationError("selected record incarnation is unknown", "unqualified-source")
    c.reference(nonce, "observed record incarnation")
    provenance = (row.get("fact_provenance") or {}).get("record") or {}
    if provenance and provenance.get("quality") != "current":
        raise c.ObservationError("selected record provenance is not current", "unready")
    head = row.get("last_session_id")
    leg = row.get("execution_leg")
    # Listing heads can instead be yielded/resumable or execution-leg fallback targets.
    qualified = (
        provenance.get("quality") == "current"
        and not row.get("head_yielded")
        and (leg is None or leg.get("state") == "disposed")
    )
    if head is not None:
        head = c.identity(head, "observed session")
    head = head if qualified else None
    stable = semantic_row(row)
    facts = {
        "origin": "resident-current-roster",
        "coverage": "sampled-selected-fields-without-timestamp-metadata",
        "head_basis": "asserted-listing-head" if head is not None else "unknown",
        "row": {key: stable_value(value) for key, value in stable.items() if key in FIELDS},
    }
    raw = c.utf8(c.encode(facts, c.BODY_BYTES - 4096))
    facts = c.decode(raw, c.BODY_BYTES - 4096)
    incarnation = hashlib.sha256(c.utf8(c.encode([project, worktree, nonce]))).hexdigest()
    try:
        at = datetime.fromtimestamp(publication["observed_at"], timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError) as error:
        raise c.ObservationError("selected roster timestamp is unrepresentable") from error
    record = CanonicalObservation(
        project,
        worktree,
        "current-roster:" + incarnation,
        head,
        facts,
        {"facts_sha256": hashlib.sha256(raw).hexdigest(), "head_session_id": head},
        at,
        availability="observed" if head is not None else "unknown",
        completeness="partial",
    )
    if not authority(owner, store, read=read):
        raise c.ObservationError("resident changed while selecting roster facts", "superseded")
    return AcceptedObservation(record, publication["revision"])
