from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml

from . import config as cfg
from . import obligations

if TYPE_CHECKING:
    from . import tracking


def _tracking():
    from . import tracking as tracking_mod

    return tracking_mod

# work still rides on the resource, "at-rest" once that work is safe (merged /
# off-box / itself finalized) but the claim is still held, "released" once the
# owner explicitly lets go. Unknown/absent degrades to "active" so a stray value
# never hides a live claim from the reap-safety check. The canonical vocabulary
# + predicates live in ``obligations``; this tuple is the set of dispositions
# that still mean "held" (claim not torn down) -- both active and at-rest -- used
# by ``is_live`` / ``live_resources`` for reap-safety.
_CLAIM_LIVE_STATES: tuple[str, ...] = ("", "active", "at-rest")


@dataclass
class ClaimRef:
    """A parsed qualified reference to a claimed resource / owning worktree."""

    worktree_id: str
    machine: str | None = None
    project: str | None = None
    session: str | None = None

    @property
    def is_qualified(self) -> bool:
        return bool(self.machine and self.project)

    @property
    def is_anchor(self) -> bool:
        return self.worktree_id == ANCHOR_ID

    def canonical(self) -> str:
        return format_claim_ref(
            self.machine, self.project, self.worktree_id, self.session
        )


ANCHOR_ID = "@anchor"


def format_anchor_ref(
    machine: str | None,
    project: str | None,
    session: str | None = None,
) -> str:
    return format_claim_ref(machine, project, ANCHOR_ID, session)


def format_claim_ref(
    machine: str | None,
    project: str | None,
    worktree_id: str,
    session: str | None = None,
) -> str:
    core = worktree_id
    if machine and project:
        core = f"{machine}/{project}/{worktree_id}"
    return f"{core}#{session}" if session else core


def parse_claim_ref(ref: str) -> ClaimRef | None:
    if not ref:
        return None
    body, _, session = ref.partition("#")
    session_val = session or None
    parts = body.split("/")
    if len(parts) >= 3:
        machine, project = parts[0], parts[1]
        worktree_id = "/".join(parts[2:])
        return ClaimRef(
            worktree_id=worktree_id,
            machine=machine or None,
            project=project or None,
            session=session_val,
        )
    return ClaimRef(worktree_id=body, session=session_val)


@dataclass
class ResourceClaim:
    """One outbound resource a worktree owns (an entry in its claim ledger)."""

    kind: str = "worktree"
    ref: str = ""
    created_at: str = ""
    state: str = "active"
    note: str = ""
    handoff_bundle: str = ""

    @property
    def is_live(self) -> bool:
        return self.state in _CLAIM_LIVE_STATES

    @property
    def is_unsettled(self) -> bool:
        return obligations.blocks_finalize(self.state)

    @property
    def is_at_rest(self) -> bool:
        return obligations.is_at_rest(self.state)

    @property
    def is_abandoned(self) -> bool:
        return obligations.is_abandoned(self.state)


FollowUpState = Literal[
    "open", "resolved", "dismissed", "pending-transfer", "transferred",
]

FOLLOW_UP_OPEN: FollowUpState = "open"
FOLLOW_UP_RESOLVED: FollowUpState = "resolved"
FOLLOW_UP_DISMISSED: FollowUpState = "dismissed"
FOLLOW_UP_PENDING_TRANSFER: FollowUpState = "pending-transfer"
FOLLOW_UP_TRANSFERRED: FollowUpState = "transferred"

_FOLLOW_UP_EFFECTIVE_OPEN: frozenset[str] = frozenset(
    {FOLLOW_UP_OPEN, FOLLOW_UP_PENDING_TRANSFER}
)

FollowUpRefKind = Literal[
    "resource-claim", "dispatch-task", "issue", "pull-request", "file",
    "effort", "other",
]


@dataclass
class FollowUpRef:
    kind: FollowUpRefKind = "other"
    ref: str = ""


@dataclass
class FollowUpRecord:
    """One itemized worktree-local obligation."""

    id: str
    summary: str
    state: FollowUpState = FOLLOW_UP_OPEN
    revision: int = 1
    created_at: str = ""
    updated_at: str = ""
    refs: list[FollowUpRef] = field(default_factory=list)
    result_ref: str | None = None
    transfer_target: str | None = None
    reason: str = ""

    @property
    def is_effective_open(self) -> bool:
        return self.state in _FOLLOW_UP_EFFECTIVE_OPEN

    def to_dict(self) -> dict:
        data: dict[str, object] = {
            "id": self.id,
            "summary": self.summary,
            "state": self.state,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.refs:
            data["refs"] = [{"kind": ref.kind, "ref": ref.ref} for ref in self.refs]
        if self.result_ref:
            data["result_ref"] = self.result_ref
        if self.transfer_target:
            data["transfer_target"] = self.transfer_target
        if self.reason:
            data["reason"] = self.reason
        return data

    @staticmethod
    def from_dict(data: dict) -> FollowUpRecord:
        raw_refs = data.get("refs") or []
        refs = [
            FollowUpRef(kind=str(ref.get("kind", "other")), ref=str(ref.get("ref", "")))
            for ref in raw_refs
            if isinstance(ref, dict)
        ]
        state = str(data.get("state", FOLLOW_UP_OPEN))
        if state not in (
            FOLLOW_UP_OPEN,
            FOLLOW_UP_RESOLVED,
            FOLLOW_UP_DISMISSED,
            FOLLOW_UP_PENDING_TRANSFER,
            FOLLOW_UP_TRANSFERRED,
        ):
            state = FOLLOW_UP_OPEN
        created_raw = data.get("created_at", "")
        if hasattr(created_raw, "isoformat"):
            created_raw = created_raw.isoformat()
        updated_raw = data.get("updated_at", "")
        if hasattr(updated_raw, "isoformat"):
            updated_raw = updated_raw.isoformat()
        return FollowUpRecord(
            id=str(data.get("id", "")),
            summary=str(data.get("summary", "")),
            state=state,  # type: ignore[arg-type]
            revision=int(data.get("revision", 1) or 1),
            created_at=str(created_raw or ""),
            updated_at=str(updated_raw or ""),
            refs=refs,
            result_ref=(str(data["result_ref"]) if data.get("result_ref") else None),
            transfer_target=(
                str(data["transfer_target"]) if data.get("transfer_target") else None
            ),
            reason=str(data.get("reason", "") or ""),
        )


def load_or_create_anchor_record(
    anchor_path: str,
    repo: str,
    machine: str,
    platform_name: str,
    tracking_path: Path,
) -> tracking.WorktreeRecord:
    tracking = _tracking()
    path = tracking_path / f"{ANCHOR_ID}.yaml"
    if path.exists():
        return tracking.load_record(path)
    return tracking.create_new_record(
        ANCHOR_ID,
        ANCHOR_ID,
        anchor_path,
        repo,
        machine,
        platform_name,
        tracking_path,
        pair_kind="anchor",
    )


def claim_handoff_reservation(
    record: tracking.WorktreeRecord,
    claim: ResourceClaim,
) -> str:
    if claim.handoff_bundle:
        return claim.handoff_bundle
    try:
        from . import claim_handoffs

        source = format_claim_ref(record.machine, record.repo, record.worktree_id)
        return claim_handoffs.active_bundle_for_claim(source, claim.ref)
    except Exception:
        return "unverified-handoff-registry"


def reopen_finalized_owner(record: tracking.WorktreeRecord, *, reason: str) -> bool:
    tracking = _tracking()
    if record.status != "finalized":
        return False
    if record.completed_at:
        record.last_finalized_at = record.completed_at
    tracking.update_status(record, "active", save=False)
    record.status_note_at = None
    return True


def _claim_reopens_owner(
    record: tracking.WorktreeRecord,
    claim: ResourceClaim,
) -> bool:
    if not claim.is_live:
        return False
    existing = next((existing for existing in record.resources if existing.ref == claim.ref), None)
    if existing is None:
        return True
    return not existing.is_live


def add_resource_claim(
    record: tracking.WorktreeRecord,
    claim: ResourceClaim,
    *,
    save: bool = True,
) -> ResourceClaim:
    tracking = _tracking()
    if (
        record.status in {"finalizing", "orphaned"}
        or (
            record.kind in tracking.MANAGED_KINDS
            and record.status in {"complete", "completed"}
        )
    ):
        raise ValueError(
            f"owner worktree {record.worktree_id} is {record.status}; "
            "creator ownership is frozen"
        )
    reopens = _claim_reopens_owner(record, claim)
    for existing in record.resources:
        if existing.ref != claim.ref:
            continue
        reservation = claim_handoff_reservation(record, existing)
        if reservation:
            equivalent = (
                existing.kind == claim.kind
                and existing.state == claim.state
                and (not claim.note or existing.note == claim.note)
            )
            if equivalent:
                return existing
            raise ValueError(
                f"claim {claim.ref} is reserved by handoff bundle {reservation}"
            )
        existing.kind = claim.kind
        existing.state = claim.state
        if claim.note:
            existing.note = claim.note
        if claim.created_at:
            existing.created_at = claim.created_at
        if reopens:
            reopen_finalized_owner(record, reason=f"reactivated claim {claim.ref}")
        if save:
            tracking.save_record(record)
        return existing
    record.resources.append(claim)
    if reopens:
        reopen_finalized_owner(record, reason=f"new claim {claim.ref}")
    if save:
        tracking.save_record(record)
    return claim


def settle_resource_claim(
    record: tracking.WorktreeRecord,
    ref: str,
    disposition: str = obligations.AT_REST,
    *,
    save: bool = True,
    path: Path | None = None,
) -> ResourceClaim | None:
    tracking = _tracking()
    match = next((claim for claim in record.resources if claim.ref == ref), None)
    if match is None or claim_handoff_reservation(record, match):
        return None
    match.state = obligations.normalize(disposition)
    if save:
        tracking.save_record(record, path)
    return match


def release_resource_claim(
    record: tracking.WorktreeRecord,
    ref: str,
    *,
    save: bool = True,
    path: Path | None = None,
) -> ResourceClaim | None:
    tracking = _tracking()
    match = next((claim for claim in record.resources if claim.ref == ref), None)
    if match is None or claim_handoff_reservation(record, match):
        return None
    match.state = obligations.RELEASED
    if save:
        tracking.save_record(record, path)
    return match


def effective_open_follow_up_count(record: tracking.WorktreeRecord) -> int:
    items_open = sum(1 for follow_up in record.follow_ups if follow_up.is_effective_open)
    if items_open:
        return items_open
    return 1 if record.follow_up else 0


def _follow_up_id(id_factory: Callable[[], str] | None = None) -> str:
    make = id_factory or (lambda: secrets.token_hex(4))
    return f"fu-{make()}"


def add_follow_up(
    record: tracking.WorktreeRecord,
    summary: str,
    *,
    refs: list[FollowUpRef] | None = None,
    id_factory: Callable[[], str] | None = None,
    save: bool = True,
) -> FollowUpRecord:
    tracking = _tracking()
    if record.status in {"finalizing", "orphaned"}:
        raise ValueError(
            f"owner worktree {record.worktree_id} is {record.status}; "
            "creator ownership is frozen"
        )
    now = tracking._now_iso()
    item = FollowUpRecord(
        id=_follow_up_id(id_factory),
        summary=summary,
        state=FOLLOW_UP_OPEN,
        revision=1,
        created_at=now,
        updated_at=now,
        refs=list(refs or []),
    )
    record.follow_ups.append(item)
    if record.status == "finalized":
        reopen_finalized_owner(record, reason=f"new follow-up {item.id}")
    if save:
        tracking.save_record(record)
    return item


def _resolve_follow_up_item(
    record: tracking.WorktreeRecord,
    follow_up_id: str,
) -> FollowUpRecord | None:
    return next((item for item in record.follow_ups if item.id == follow_up_id), None)


def resolve_follow_up(
    record: tracking.WorktreeRecord,
    follow_up_id: str,
    *,
    result_ref: str | None = None,
    save: bool = True,
) -> FollowUpRecord | None:
    tracking = _tracking()
    item = _resolve_follow_up_item(record, follow_up_id)
    if item is None:
        return None
    item.state = FOLLOW_UP_RESOLVED
    item.result_ref = result_ref
    item.updated_at = tracking._now_iso()
    item.revision += 1
    if save:
        tracking.save_record(record)
    return item


def dismiss_follow_up(
    record: tracking.WorktreeRecord,
    follow_up_id: str,
    *,
    reason: str,
    save: bool = True,
) -> FollowUpRecord | None:
    tracking = _tracking()
    item = _resolve_follow_up_item(record, follow_up_id)
    if item is None:
        return None
    item.state = FOLLOW_UP_DISMISSED
    item.reason = reason
    item.updated_at = tracking._now_iso()
    item.revision += 1
    if save:
        tracking.save_record(record)
    return item


def sweep_abandoned_obligations(
    record: tracking.WorktreeRecord,
    *,
    gone_of: Callable[[ResourceClaim], bool | None],
    safe_of: Callable[[ResourceClaim], bool | None],
    save: bool = True,
    path: Path | None = None,
) -> list[ResourceClaim]:
    tracking = _tracking()
    reclaimed: list[ResourceClaim] = []
    for claim in record.resources:
        if not claim.is_unsettled or claim_handoff_reservation(record, claim):
            continue
        try:
            gone = gone_of(claim)
        except Exception:
            gone = None
        try:
            safe = safe_of(claim)
        except Exception:
            safe = None
        if obligations.should_abandon(gone=gone, safe=safe):
            claim.state = obligations.ABANDONED
            reclaimed.append(claim)
    if reclaimed and save:
        tracking.save_record(record, path)
    return reclaimed


_TERMINAL_OWNER_STATUSES: frozenset[str] = frozenset({"finalized", "orphaned"})


def release_all_resources(
    record: tracking.WorktreeRecord,
    *,
    save: bool = True,
) -> list[ResourceClaim]:
    tracking = _tracking()
    released = [claim for claim in record.resources if claim.is_live and claim.kind != "session"]
    for claim in released:
        claim.state = "released"
    if released and save:
        tracking.save_record(record)
    return released


def orphanage_path(project: str | None = None) -> Path:
    return cfg.project_dir(project) / "orphaned-obligations.yaml"


def load_orphaned_obligations(project: str | None = None) -> list[dict]:
    tracking = _tracking()
    try:
        return tracking.load_orphaned_obligations_strict(project)
    except Exception:
        return []


def _load_orphaned_obligations_strict_local(project: str | None = None) -> list[dict]:
    tracking = _tracking()
    path = tracking.orphanage_path(project)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid orphanage mapping: {path}")
    items = data.get("orphaned", [])
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError(f"invalid orphanage entries: {path}")
    return list(items)


def load_orphaned_obligations_strict(project: str | None = None) -> list[dict]:
    return _load_orphaned_obligations_strict_local(project)


def rehome_abandoned_obligations(
    claims: Iterable[ResourceClaim],
    *,
    source_worktree: str,
    config: object,
    handoff_to: str | None = None,
    project: str | None = None,
) -> list[dict]:
    tracking = _tracking()
    try:
        path = tracking.orphanage_path(project)
        with tracking._RecordLock(path, require_sidecar=True):
            existing = tracking.load_orphaned_obligations_strict(project)
            by_key = {(entry.get("source_worktree"), entry.get("ref")): entry for entry in existing}
            machine = getattr(config, "machine", None)
            proj = project or getattr(config, "repo_name", None)
            now = tracking._now_iso()
            target = (handoff_to or "").strip()
            added: list[dict] = []
            changed = False
            for claim in claims:
                key = (source_worktree, claim.ref)
                prior = by_key.get(key)
                if prior is not None:
                    if target and not (prior.get("handoff_to") or "").strip():
                        prior["handoff_to"] = target
                        changed = True
                    continue
                entry = {
                    "kind": claim.kind,
                    "ref": claim.ref,
                    "note": claim.note or "",
                    "source_worktree": source_worktree,
                    "machine": machine,
                    "project": proj,
                    "disposition": "abandoned",
                    "abandoned_at": now,
                    "handoff_to": target,
                }
                existing.append(entry)
                added.append(entry)
                by_key[key] = entry
                changed = True
            if changed:
                tracking._atomic_write(
                    path,
                    yaml.safe_dump({"orphaned": existing}, sort_keys=False),
                )
            return added
    except Exception:
        return []


def remove_orphaned_obligations(
    keys: Iterable[tuple[str | None, str | None]],
    *,
    project: str | None = None,
) -> int:
    tracking = _tracking()
    try:
        drop = {(key[0], key[1]) for key in keys}
        if not drop:
            return 0
        path = tracking.orphanage_path(project)
        with tracking._RecordLock(path, require_sidecar=True):
            existing = tracking.load_orphaned_obligations_strict(project)
            kept = [
                entry
                for entry in existing
                if (entry.get("source_worktree"), entry.get("ref")) not in drop
            ]
            removed = len(existing) - len(kept)
            if removed <= 0:
                return 0
            if kept:
                tracking._atomic_write(
                    path,
                    yaml.safe_dump({"orphaned": kept}, sort_keys=False),
                )
            elif path.exists():
                path.unlink()
            return removed
    except Exception:
        return 0


def find_orphaned_children(
    tracking_path: Path,
) -> list[tuple[tracking.WorktreeRecord, tracking.WorktreeRecord | None]]:
    tracking = _tracking()
    out: list[tuple[tracking.WorktreeRecord, tracking.WorktreeRecord | None]] = []
    try:
        this_machine = cfg.load_config().machine
    except Exception:
        this_machine = None
    for child in tracking.list_records(tracking_path):
        ref = child.owner_claim_ref
        if ref is None:
            continue
        if ref.machine and this_machine and ref.machine != this_machine:
            continue
        parent = tracking.load_record_by_id(ref.worktree_id)
        if parent is None:
            out.append((child, None))
        elif parent.status in _TERMINAL_OWNER_STATUSES:
            out.append((child, parent))
    return out
