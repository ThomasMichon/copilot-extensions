"""Pure, conservative projections of the existing disposable-resource ledger."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from . import resource_producer as producer, tracking


@dataclass(frozen=True)
class ObservedClaim:
    """One producer claim read from its owning tracking record."""

    owner: str
    kind: str
    ref: str
    state: str
    handoff_bundle: str
    metadata: object


def qualified_owner(value: object) -> bool:
    """Accept only an exact qualified worktree ref, without a session."""
    if not isinstance(value, str):
        return False
    parsed = tracking.parse_claim_ref(value)
    return bool(
        parsed and parsed.is_qualified and not parsed.session
        and parsed.canonical() == value
        and len(value.split("/")) == 3
        and all(part not in {"", ".", ".."} for part in value.split("/"))
        and not any(ord(char) < 32 or char in "\\\x7f" for char in value)
    )


def issue(code: str, subject: str, message: str) -> dict[str, str]:
    """Build an explicit uncertainty, not a safely-empty result."""
    return {"code": code, "subject": subject, "message": message}


def _immutable(metadata: dict) -> str:
    return producer.canonical_json({
        key: value for key, value in metadata.items()
        if key not in {"phase", "closeout", "transfer"}
    })


def _resolve_group(
    group: list[ObservedClaim], issues: list[dict[str, str]],
) -> str | None:
    """Require one connected creator-to-terminal chain, never pick a winner."""
    by_owner = {row.owner: row for row in group}
    metadata_by_owner = {row.owner: row.metadata for row in group if isinstance(row.metadata, dict)}
    subject = group[0].ref
    if len(by_owner) != len(group):
        issues.append(issue("duplicate-token", subject, "multiple token entries in one owner record"))
        return None
    first = group[0]
    if any(
        (row.kind, row.ref, _immutable(metadata_by_owner[row.owner]))
        != (first.kind, first.ref, _immutable(metadata_by_owner[first.owner]))
        for row in group
    ):
        issues.append(issue("token-mismatch", subject, "token entries disagree on immutable metadata"))
        return None
    incoming: dict[str, int] = defaultdict(int)
    for row in group:
        if row.handoff_bundle:
            issues.append(issue("handoff-incomplete", row.owner, "handoff marker remains on the claim"))
        metadata = metadata_by_owner[row.owner]
        if metadata["phase"] != "transferred":
            continue
        receipt = metadata["transfer"]
        if (
            receipt["source"] != row.owner
            or not qualified_owner(receipt["consumer"])
            or receipt["consumer"] not in by_owner
        ):
            issues.append(issue("transfer-unresolved", row.owner, "accepted transfer recipient/source is missing or invalid"))
            return None
        incoming[receipt["consumer"]] += 1
    creator = metadata_by_owner[first.owner]["creator"]
    roots = [owner for owner in by_owner if not incoming[owner]]
    if roots != [creator] or any(count > 1 for count in incoming.values()):
        issues.append(issue("transfer-ambiguous", subject, "ownership is not a single creator-rooted chain"))
        return None
    seen: set[str] = set()
    owner = creator
    while owner not in seen:
        seen.add(owner)
        metadata = metadata_by_owner[owner]
        if metadata["phase"] != "transferred":
            if len(seen) != len(group):
                break
            return owner
        owner = metadata["transfer"]["consumer"]
    issues.append(issue("transfer-ambiguous", subject, "transfer chain is cyclic or disconnected"))
    return None


def ownership_snapshot(
    rows: list[ObservedClaim], scan_issues: list[dict[str, str]], *,
    owner: str | None = None, kind: str | None = None,
    namespace: str | None = None, identity: dict | None = None,
) -> dict:
    """Project a local observational snapshot; filters never hide uncertainty."""
    issues = list(scan_issues)
    groups: dict[str, list[ObservedClaim]] = defaultdict(list)
    valid: list[ObservedClaim] = []
    valid_ids: set[int] = set()
    identities: dict[int, str] = {}
    for row in rows:
        metadata = row.metadata
        if (
            not qualified_owner(row.owner) or not isinstance(metadata, dict)
            or not producer.valid_metadata(metadata)
            or producer.claim_ref(metadata) != row.ref
        ):
            issues.append(issue("invalid-producer", row.owner, "producer metadata or exact callback ref is invalid"))
            continue
        valid.append(row)
        valid_ids.add(id(row))
        if "identity" in metadata:
            identities[id(row)] = producer.canonical_json(metadata["identity"])
        groups[metadata["token"]].append(row)
    terminals = {token: _resolve_group(group, issues) for token, group in groups.items()}
    subjects: dict[tuple[str, str, str], list[ObservedClaim]] = defaultdict(list)
    for row in valid:
        metadata = row.metadata
        assert isinstance(metadata, dict)
        if metadata["phase"] == "bound":
            key = (row.kind, metadata["namespace"], identities[id(row)])
            subjects[key].append(row)
    for key, claims in subjects.items():
        if len(claims) > 1:
            issues.append(issue("duplicate-identity", producer.canonical_json(list(key)), "multiple bound claims name the same exact resource"))
    complete = not issues
    identity_json = producer.canonical_json(identity) if identity is not None else None
    entries = []
    for row in rows:
        metadata = row.metadata if isinstance(row.metadata, dict) else {}
        is_valid = id(row) in valid_ids
        if owner is not None and row.owner != owner:
            continue
        if kind is not None and row.kind != kind:
            continue
        if namespace is not None and (not is_valid or metadata["namespace"] != namespace):
            continue
        if identity_json is not None and (
            not is_valid or "identity" not in metadata
            or identities[id(row)] != identity_json
        ):
            continue
        wire = producer.wire_metadata(row.metadata)
        terminal = terminals.get(metadata["token"]) if is_valid else None
        current_owner = None
        resolution = "unknown"
        if complete and terminal is not None:
            terminal_row = next(item for item in groups[metadata["token"]] if item.owner == terminal)
            terminal_metadata = terminal_row.metadata
            assert isinstance(terminal_metadata, dict)
            if terminal_metadata["phase"] != "closed":
                current_owner = terminal
            resolution = "current" if row.owner == current_owner else "history"
        entries.append({
            "record_owner": row.owner, "kind": row.kind, "ref": row.ref,
            "state": row.state, "handoff_bundle": row.handoff_bundle,
            "producer": wire,
            "creator": metadata["creator"] if is_valid else None,
            "phase": metadata["phase"] if is_valid else "invalid",
            "current_owner": current_owner, "resolution": resolution,
            "execution_space": "recorded-not-probed",
        })
    return {
        "schema": "copilot-extensions.producer-ownership", "version": 1,
        "scope": "local-registered-projects",
        "authority": "observational-ledger-snapshot",
        "complete": complete, "issues": issues, "entries": entries,
        "resolution": "unknown" if not complete else ("observed" if entries else "not-observed"),
        "transactional": False, "absence_proof": False, "cleanup_authority": False,
    }
