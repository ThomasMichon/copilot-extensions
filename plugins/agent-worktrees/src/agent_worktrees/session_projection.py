"""Reciprocal, rebuildable worktree metadata beside one Copilot session."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable, Literal

from . import sessions

SCHEMA_VERSION = 2
MAX_SUPPORTED_SCHEMA_VERSION = 2
SIDECAR_NAME = "agent-worktrees.json"
MAX_BYTES = 128 * 1024
MAX_RELATIONS = 128
MAX_RELATION_TOMBSTONES = 128
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
SyncOutcome = Literal["written", "current", "blocked", "deferred"]
RecordLoader = Callable[[str, str], Any | None]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RESTORED_VALIDATED_STATUSES = {
    "restored-validated",
    "restored-validated-incomplete",
}


class ProjectionError(ValueError):
    """The session projection cannot be read or updated safely."""


class ProjectionUnavailable(ProjectionError):
    """Projection path validation failed for a potentially transient reason."""


class UnsupportedProjectionVersion(ProjectionError):
    """The projection was written by a newer incompatible implementation."""


class RestoredProjectionReadOnly(ProjectionError):
    """The session tree was restored and is not writable local provenance."""


class MissingSessionTree(ProjectionError):
    """The exact local session-state root or session directory is absent."""


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ProjectionError(f"cannot inspect projection path {path}: {exc}") from exc
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _valid_session_id(session_id: str) -> bool:
    return bool(
        session_id
        and session_id not in {".", ".."}
        and "/" not in session_id
        and "\\" not in session_id
        and "\x00" not in session_id
        and Path(session_id).name == session_id
    )


def _validate_session_directory(
    root: Path,
    session_dir: Path,
    session_id: str,
) -> None:
    if _is_reparse(root):
        raise ProjectionError(f"unsafe or missing session-state root: {root}")
    if _is_reparse(session_dir):
        raise ProjectionError(f"unsafe or missing session directory: {session_dir}")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_session = session_dir.resolve(strict=True)
    except OSError as exc:
        raise ProjectionUnavailable(
            f"cannot resolve projection session directory {session_dir}: {exc}"
        ) from exc
    if (
        resolved_session.parent != resolved_root
        or resolved_session.name != session_id
    ):
        raise ProjectionError(
            f"session directory identity does not match {session_id!r}"
        )


def _session_paths(
    session_id: str,
    *,
    writing: bool = False,
) -> tuple[Path, Path, Path]:
    if not _valid_session_id(session_id):
        raise ProjectionError(f"invalid session id {session_id!r}")
    root = sessions._session_state_dir()
    if not root.is_dir():
        raise MissingSessionTree(f"missing session-state root: {root}")
    session_dir = root / session_id
    if not session_dir.is_dir():
        raise MissingSessionTree(f"missing session directory: {session_dir}")
    _validate_session_directory(root, session_dir, session_id)
    if writing and (session_dir / "rescued-origin.json").exists():
        raise RestoredProjectionReadOnly(
            f"restored session is read-only: {session_id}"
        )
    target = session_dir / SIDECAR_NAME
    if target.exists():
        if _is_reparse(target) or not target.is_file():
            raise ProjectionError(f"unsafe projection target: {target}")
    lock_base = root / ".agent-worktrees-locks" / f"{session_id}.json"
    temp_dir = root / ".agent-worktrees-tmp"
    if not writing:
        return target, lock_base, temp_dir
    for managed_dir in (lock_base.parent, temp_dir):
        managed_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not managed_dir.is_dir() or _is_reparse(managed_dir):
            raise ProjectionError(f"unsafe projection runtime directory: {managed_dir}")
        if os.name != "nt":
            try:
                managed_dir.chmod(0o700)
            except OSError:
                pass
    return target, lock_base, temp_dir


def _empty_projection(
    session_id: str,
    *,
    history_complete: bool = False,
    tombstone_overflow: bool = True,
) -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "session_id": session_id,
        "relations": [],
        "relation_tombstones": [],
        "tombstone_sequence": 0,
        "history_complete": history_complete,
        "overflow": False,
        "omitted_relations": 0,
        "tombstone_overflow": tombstone_overflow,
    }


def is_restored(session_id: str) -> bool:
    """Whether an exact session tree carries restored-origin provenance."""
    target, _lock_base, _temp_dir = _session_paths(session_id)
    return (target.parent / "rescued-origin.json").exists()


def read(session_id: str) -> dict[str, Any] | None:
    """Read one exact projection.

    Returns ``None`` only when the validated session directory exists but its
    sidecar does not. Invalid identities, unsafe paths, and malformed or
    unsupported projections raise :class:`ProjectionError`.
    """
    target, _lock_base, _temp_dir = _session_paths(session_id)
    if not target.exists():
        return None
    try:
        with target.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise ProjectionError(f"cannot read projection {target}: {exc}") from exc
    if len(raw) > MAX_BYTES:
        raise ProjectionError(f"projection exceeds {MAX_BYTES} bytes")
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectionError(f"invalid projection JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ProjectionError("projection must be a JSON object")
    version = loaded.get("version")
    if not isinstance(version, int):
        raise ProjectionError("projection version must be an integer")
    if version > MAX_SUPPORTED_SCHEMA_VERSION:
        raise UnsupportedProjectionVersion(
            "projection version "
            f"{version} is newer than supported {MAX_SUPPORTED_SCHEMA_VERSION}"
        )
    if version not in {1, 2}:
        raise ProjectionError(f"unsupported projection version {version}")
    if loaded.get("session_id") != session_id:
        raise ProjectionError("projection session_id does not match its directory")
    relations = loaded.get("relations")
    if not isinstance(relations, list):
        raise ProjectionError("projection relations must be an array")
    if any(not isinstance(relation, dict) for relation in relations):
        raise ProjectionError("projection relations must contain JSON objects")
    if version == 2:
        required = {
            "relations",
            "relation_tombstones",
            "tombstone_sequence",
            "history_complete",
            "overflow",
            "omitted_relations",
            "tombstone_overflow",
        }
        missing = sorted(required - loaded.keys())
        if missing:
            raise ProjectionError(
                "v2 projection is missing required field(s): "
                + ", ".join(missing)
            )
    tombstones = loaded.get("relation_tombstones", [])
    if not isinstance(tombstones, list):
        raise ProjectionError("projection relation_tombstones must be an array")
    if any(not isinstance(tombstone, dict) for tombstone in tombstones):
        raise ProjectionError(
            "projection relation_tombstones must contain JSON objects"
        )
    if version == 2:
        projection_metadata(loaded)
        if len(relations) > MAX_RELATIONS:
            raise ProjectionError(
                f"projection relations exceed the v2 cap of {MAX_RELATIONS}"
            )
        if len(tombstones) > MAX_RELATION_TOMBSTONES:
            raise ProjectionError(
                "projection relation_tombstones exceed the v2 cap of "
                f"{MAX_RELATION_TOMBSTONES}"
            )
        digests: set[str] = set()
        sequences: set[int] = set()
        for tombstone in tombstones:
            digest = tombstone.get("key_sha256")
            revision = tombstone.get("relation_revision")
            sequence = tombstone.get("sequence")
            if (
                not isinstance(digest, str)
                or _SHA256_RE.fullmatch(digest) is None
                or type(revision) is not int
                or revision < 0
                or type(sequence) is not int
                or sequence < 0
            ):
                raise ProjectionError("invalid v2 projection tombstone")
            if digest in digests or sequence in sequences:
                raise ProjectionError("duplicate v2 projection tombstone")
            digests.add(digest)
            sequences.add(sequence)
        relation_keys = [_relation_key(relation) for relation in relations]
        if len(set(relation_keys)) != len(relation_keys):
            raise ProjectionError("duplicate v2 projection relation")
        relation_digests = {
            _relation_key_sha256(_relation_key(relation))
            for relation in relations
        }
        if relation_digests & digests:
            raise ProjectionError(
                "v2 relation cannot also have a deletion tombstone"
            )
        tombstone_sequence = loaded.get("tombstone_sequence")
        if (
            type(tombstone_sequence) is not int
            or tombstone_sequence < max(
                (item["sequence"] for item in tombstones),
                default=0,
            )
        ):
            raise ProjectionError("invalid v2 projection tombstone_sequence")
    return loaded


def projection_metadata(projection: dict[str, Any]) -> dict[str, Any]:
    """Normalize versioned completeness fields without changing stored data."""
    version = projection.get("version")
    overflow = projection.get("overflow", False)
    omitted = projection.get("omitted_relations", 0)
    if not isinstance(overflow, bool):
        raise ProjectionError("projection overflow must be a boolean")
    if version == 1:
        if type(omitted) is not int or omitted < 0:
            raise ProjectionError(
                "v1 projection omitted_relations must be a non-negative integer"
            )
        return {
            "version": 1,
            "overflow": overflow,
            "omitted_relations": omitted,
            "history_complete": None,
            "tombstone_overflow": None,
            "relation_set_incomplete": bool(overflow or omitted),
        }
    if version == 2:
        history_complete = projection.get("history_complete")
        tombstone_overflow = projection.get("tombstone_overflow")
        if not isinstance(history_complete, bool):
            raise ProjectionError(
                "v2 projection history_complete must be a boolean"
            )
        if not isinstance(tombstone_overflow, bool):
            raise ProjectionError(
                "v2 projection tombstone_overflow must be a boolean"
            )
        if overflow:
            if omitted is not None:
                raise ProjectionError(
                    "v2 overflow requires omitted_relations to be null"
                )
            if history_complete:
                raise ProjectionError(
                    "v2 overflow requires history_complete to be false"
                )
        elif type(omitted) is not int or omitted != 0:
            raise ProjectionError(
                "complete v2 projection requires omitted_relations to be zero"
            )
        return {
            "version": 2,
            "overflow": overflow,
            "omitted_relations": omitted,
            "history_complete": history_complete,
            "tombstone_overflow": tombstone_overflow,
            "relation_set_incomplete": bool(
                overflow or not history_complete
            ),
        }
    raise ProjectionError(f"unsupported projection version {version}")


def _read_recoverable(session_id: str) -> dict[str, Any]:
    """Read parseable supported state into conservative writable v2 form."""
    target, _lock_base, _temp_dir = _session_paths(session_id)
    if not target.exists():
        return _empty_projection(session_id)
    try:
        with target.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise ProjectionError(f"cannot read projection {target}: {exc}") from exc
    if len(raw) > MAX_BYTES:
        raise ProjectionError(f"projection exceeds {MAX_BYTES} bytes")
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectionError(f"invalid projection JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ProjectionError("projection must be a JSON object")
    version = loaded.get("version")
    if not isinstance(version, int):
        raise ProjectionError("projection version must be an integer")
    if version > MAX_SUPPORTED_SCHEMA_VERSION:
        raise UnsupportedProjectionVersion(
            "projection version "
            f"{version} is newer than supported {MAX_SUPPORTED_SCHEMA_VERSION}"
        )
    if version not in {1, 2}:
        raise ProjectionError(f"unsupported projection version {version}")
    if loaded.get("session_id") != session_id:
        raise ProjectionError("projection session_id does not match its directory")
    relations = loaded.get("relations")
    tombstones = loaded.get("relation_tombstones", [])
    if not isinstance(relations, list) or not isinstance(tombstones, list):
        raise ProjectionError("projection relation fields must be arrays")
    recovered = dict(loaded)
    recovered["relations"] = [
        relation for relation in relations if isinstance(relation, dict)
    ]
    recovered["relation_tombstones"] = [
        tombstone for tombstone in tombstones if isinstance(tombstone, dict)
    ]
    if version == 1:
        return _migrate_v1_projection(
            recovered,
            conservative_reconstruction=True,
        )
    return _recover_v2_projection(recovered)


def _handoff_ordinal(record: Any, session_id: str) -> int | None:
    matching = [
        handoff.ordinal
        for handoff in getattr(record, "handoffs", [])
        if handoff.predecessor == session_id or handoff.successor == session_id
    ]
    return max(matching, default=None)


def bound_relation(record: Any, session_id: str) -> dict[str, Any] | None:
    """Build the bound relation for one session from an authoritative record."""
    entry = record.session_entry(session_id)
    if entry is None:
        return None
    return {
        "project": record.repo,
        "worktree_id": record.worktree_id,
        "role": "bound",
        "relation_revision": entry.relation_revision,
        "head_revision": record.head_revision,
        "is_head": record.resolved_head_session == session_id,
        "lifecycle_state": entry.state,
        "lineage": {
            "predecessor": entry.predecessor,
            "successor": entry.successor,
            "handoff_ordinal": _handoff_ordinal(record, session_id),
        },
    }


def controller_relation(
    record: Any,
    session_id: str,
) -> dict[str, Any] | None:
    """Build one controller relation for an exact controller session."""
    relation = record.controller_for_session(session_id)
    if relation is None:
        return None
    return {
        "project": record.repo,
        "worktree_id": record.worktree_id,
        "role": "controller",
        "relation_revision": relation.relation_revision,
        "controller_revision": record.controller_revision,
        "controller_kind": relation.kind,
        "controller_source": relation.source,
        "controller_ref": relation.controller_ref,
        "controller_session_id": relation.controller_session_id,
        "relation_state": relation.state,
        "created_at": relation.created_at,
        "ended_at": relation.ended_at,
    }


def _safe_identity_token(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and value
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
    )


def _default_record_loader(project: str, worktree_id: str) -> Any | None:
    if not _safe_identity_token(project) or not _safe_identity_token(worktree_id):
        return None
    try:
        from . import config as cfg
        from . import tracking

        tracking_dir = cfg.project_dir(project) / "worktrees"
        path = tracking_dir / f"{worktree_id}.yaml"
        if path.parent != tracking_dir or not path.is_file():
            return None
        return tracking.load_record(path)
    except Exception:
        return None


def _known_relation(relation: dict[str, Any]) -> dict[str, Any]:
    role = relation.get("role")
    keys = {
        "project",
        "worktree_id",
        "role",
        "relation_revision",
    }
    if role == "bound":
        keys.update({
            "head_revision",
            "is_head",
            "lifecycle_state",
            "lineage",
        })
    elif role == "controller":
        keys.update({
            "controller_revision",
            "controller_kind",
            "controller_source",
            "controller_ref",
            "controller_session_id",
            "relation_state",
            "created_at",
            "ended_at",
        })
    known = {key: relation.get(key) for key in keys}
    if role == "bound" and isinstance(known.get("lineage"), dict):
        lineage = known["lineage"]
        known["lineage"] = {
            key: lineage.get(key)
            for key in ("predecessor", "successor", "handoff_ordinal")
        }
    return known


def validate_restored_hint(
    session_id: str,
    projection: dict[str, Any] | None = None,
    *,
    record_loader: RecordLoader = _default_record_loader,
) -> dict[str, Any]:
    """Validate one restored projection's unique bound identity against records."""
    if projection is None:
        projection = read(session_id)
    if projection is None:
        return {"status": "restored-missing-projection"}
    try:
        metadata = projection_metadata(projection)
    except ProjectionError:
        return {"status": "restored-invalid"}
    relation_set_incomplete = bool(metadata["relation_set_incomplete"])
    if metadata["version"] == 1 and relation_set_incomplete:
        return {"status": "restored-incomplete"}
    bound = [
        relation
        for relation in projection.get("relations", [])
        if isinstance(relation, dict) and relation.get("role") == "bound"
    ]
    if not bound:
        return {
            "status": (
                "restored-incomplete"
                if relation_set_incomplete
                else "restored-unbound"
            )
        }
    if len(bound) != 1:
        return {"status": "restored-ambiguous"}
    relation = bound[0]
    project = relation.get("project")
    worktree_id = relation.get("worktree_id")
    if not _safe_identity_token(project) or not _safe_identity_token(worktree_id):
        return {"status": "restored-invalid-identity"}
    try:
        record = record_loader(str(project), str(worktree_id))
    except Exception:
        return {
            "status": "restored-unreadable",
            "project": project,
            "worktree_id": worktree_id,
        }
    if record is None:
        return {
            "status": "restored-foreign",
            "project": project,
            "worktree_id": worktree_id,
        }
    expected = bound_relation(record, session_id)
    if expected is None:
        return {
            "status": "restored-collision",
            "project": project,
            "worktree_id": worktree_id,
        }
    projected_relation_revision = relation.get("relation_revision")
    projected_head_revision = relation.get("head_revision")
    if not isinstance(projected_relation_revision, int) or not isinstance(
        projected_head_revision, int
    ):
        return {"status": "restored-invalid-revision"}
    expected_relation_revision = expected["relation_revision"]
    expected_head_revision = expected["head_revision"]
    if (
        projected_relation_revision < expected_relation_revision
        and projected_head_revision <= expected_head_revision
    ) or (
        projected_relation_revision <= expected_relation_revision
        and projected_head_revision < expected_head_revision
    ):
        return {
            "status": "restored-stale",
            "project": project,
            "worktree_id": worktree_id,
        }
    if (
        projected_relation_revision > expected_relation_revision
        and projected_head_revision >= expected_head_revision
    ) or (
        projected_relation_revision >= expected_relation_revision
        and projected_head_revision > expected_head_revision
    ):
        return {
            "status": "restored-newer",
            "project": project,
            "worktree_id": worktree_id,
        }
    if (
        projected_relation_revision != expected_relation_revision
        or projected_head_revision != expected_head_revision
    ):
        return {
            "status": "restored-collision",
            "project": project,
            "worktree_id": worktree_id,
        }
    if _known_relation(relation) != _known_relation(expected):
        return {
            "status": "restored-collision",
            "project": project,
            "worktree_id": worktree_id,
        }
    return {
        "status": (
            "restored-validated-incomplete"
            if relation_set_incomplete
            else "restored-validated"
        ),
        "project": project,
        "worktree_id": worktree_id,
        "record": record,
    }


def _relation_key(relation: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(relation.get("project") or ""),
        str(relation.get("worktree_id") or ""),
        str(relation.get("role") or ""),
    )


def _relation_key_sha256(relation_key: tuple[str, str, str]) -> str:
    encoded = json.dumps(
        list(relation_key),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _matching_tombstones(
    projection: dict[str, Any],
    relation_key: tuple[str, str, str],
) -> list[dict[str, Any]]:
    tombstones = [
        item
        for item in projection.get("relation_tombstones", [])
        if isinstance(item, dict)
    ]
    if projection.get("version") == 2:
        digest = _relation_key_sha256(relation_key)
        return [
            item for item in tombstones
            if item.get("key_sha256") == digest
        ]
    return [
        item for item in tombstones
        if _relation_key(item) == relation_key
    ]


def _relation_revision(relation: dict[str, Any]) -> int:
    value = relation.get("relation_revision", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _relation_sort_key(
    relation: dict[str, Any],
) -> tuple[int, tuple[str, str, str]]:
    return (_relation_revision(relation), _relation_key(relation))


def _protected_relation(relation: dict[str, Any]) -> bool:
    if relation.get("role") == "bound":
        return True
    state = relation.get("lifecycle_state") or relation.get("relation_state")
    return state not in {"handed-off", "concluded", "ended", "finalized", "terminal"}


def _relation_priority_key(
    relation: dict[str, Any],
) -> tuple[int, int, tuple[str, str, str]]:
    if relation.get("role") == "bound":
        rank = 0
    elif _protected_relation(relation):
        rank = 1
    else:
        rank = 2
    return (rank, -_relation_revision(relation), _relation_key(relation))


def _merge_additive_relation_fields(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if (
            key == "lineage"
            and isinstance(merged.get(key), dict)
            and isinstance(value, dict)
        ):
            merged[key] = dict(merged[key]) | value
        else:
            merged[key] = value
    return merged


def _canonical_json_text(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _dedupe_relations(
    relations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for relation in relations:
        grouped.setdefault(_relation_key(relation), []).append(relation)
    retained = []
    for relation_key in sorted(grouped):
        choices = grouped[relation_key]
        retained.append(max(
            choices,
            key=lambda item: (
                _relation_revision(item),
                (_revision_vector(item) or (-1, -1))[1],
                _canonical_json_text(item),
            ),
        ))
    return retained


def _valid_v2_tombstone(item: dict[str, Any]) -> bool:
    return bool(
        isinstance(item.get("key_sha256"), str)
        and _SHA256_RE.fullmatch(item["key_sha256"]) is not None
        and type(item.get("relation_revision")) is int
        and item["relation_revision"] >= 0
        and type(item.get("sequence")) is int
        and item["sequence"] >= 0
    )


def _compact_tombstones(
    tombstones: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    dropped = False
    by_digest: dict[str, dict[str, Any]] = {}
    for item in tombstones:
        if not _valid_v2_tombstone(item):
            dropped = True
            continue
        normalized = {
            "key_sha256": item["key_sha256"],
            "relation_revision": item["relation_revision"],
            "sequence": item["sequence"],
        }
        existing = by_digest.get(normalized["key_sha256"])
        if existing is not None:
            dropped = True
            if (
                normalized["sequence"],
                normalized["relation_revision"],
            ) <= (
                existing["sequence"],
                existing["relation_revision"],
            ):
                continue
        by_digest[normalized["key_sha256"]] = normalized

    by_sequence: dict[int, dict[str, Any]] = {}
    for item in sorted(by_digest.values(), key=lambda value: value["key_sha256"]):
        existing = by_sequence.get(item["sequence"])
        if existing is not None:
            dropped = True
            continue
        by_sequence[item["sequence"]] = item

    newest = sorted(
        by_sequence.values(),
        key=lambda item: (-item["sequence"], item["key_sha256"]),
    )
    if len(newest) > MAX_RELATION_TOMBSTONES:
        dropped = True
        newest = newest[:MAX_RELATION_TOMBSTONES]
    newest.sort(key=lambda item: (item["sequence"], item["key_sha256"]))
    return newest, dropped


def _projection_envelope(
    projection: dict[str, Any],
    *,
    relations: list[dict[str, Any]],
    tombstones: list[dict[str, Any]],
    history_complete: bool,
    overflow: bool,
    tombstone_overflow: bool,
) -> dict[str, Any]:
    result = dict(projection)
    result.update({
        "version": SCHEMA_VERSION,
        "session_id": projection["session_id"],
        "relations": sorted(relations, key=_relation_sort_key),
        "relation_tombstones": tombstones,
        "tombstone_sequence": max(
            projection.get("tombstone_sequence", 0)
            if type(projection.get("tombstone_sequence")) is int
            else 0,
            max((item["sequence"] for item in tombstones), default=0),
        ),
        "history_complete": history_complete,
        "overflow": overflow,
        "omitted_relations": None if overflow else 0,
        "tombstone_overflow": tombstone_overflow,
    })
    return result


def _encode_unchecked(projection: dict[str, Any]) -> bytes:
    try:
        text = json.dumps(
            projection,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ProjectionError(f"projection is not JSON encodable: {exc}") from exc
    return (text + "\n").encode("utf-8")


def _select_relations(
    projection: dict[str, Any],
    relations: list[dict[str, Any]],
    tombstones: list[dict[str, Any]],
) -> dict[str, Any]:
    compact_tombstones, tombstones_dropped = _compact_tombstones(tombstones)
    tombstone_overflow = bool(
        projection.get("tombstone_overflow") or tombstones_dropped
    )
    tombstone_by_digest = {
        item["key_sha256"]: item for item in compact_tombstones
    }
    candidates = []
    cleared_digests: set[str] = set()
    for relation in _dedupe_relations(relations):
        digest = _relation_key_sha256(_relation_key(relation))
        tombstone = tombstone_by_digest.get(digest)
        if tombstone is None:
            candidates.append(relation)
        elif _relation_revision(relation) > tombstone["relation_revision"]:
            candidates.append(relation)
            cleared_digests.add(digest)
    if cleared_digests:
        compact_tombstones = [
            item
            for item in compact_tombstones
            if item["key_sha256"] not in cleared_digests
        ]

    prioritized = sorted(candidates, key=_relation_priority_key)
    count_excluded = len(prioritized) > MAX_RELATIONS
    prior_overflow = projection.get("overflow") is True
    history_complete = projection.get("history_complete") is True

    if not prior_overflow and not count_excluded:
        complete = _projection_envelope(
            projection,
            relations=prioritized,
            tombstones=compact_tombstones,
            history_complete=history_complete,
            overflow=False,
            tombstone_overflow=tombstone_overflow,
        )
        if len(_encode_unchecked(complete)) <= MAX_BYTES:
            return complete

    base = _projection_envelope(
        projection,
        relations=[],
        tombstones=compact_tombstones,
        history_complete=False,
        overflow=True,
        tombstone_overflow=tombstone_overflow,
    )
    if len(_encode_unchecked(base)) > MAX_BYTES:
        raise ProjectionError(
            "projection fixed envelope and tombstones exceed the byte budget"
        )

    retained: list[dict[str, Any]] = []
    for candidate in prioritized[:MAX_RELATIONS]:
        trial = _projection_envelope(
            projection,
            relations=[*retained, candidate],
            tombstones=compact_tombstones,
            history_complete=False,
            overflow=True,
            tombstone_overflow=tombstone_overflow,
        )
        if len(_encode_unchecked(trial)) <= MAX_BYTES:
            retained.append(candidate)
            continue
        if candidate.get("role") == "bound":
            raise ProjectionError("bound relation exceeds the projection byte budget")
        break
    if any(
        candidate.get("role") == "bound"
        for candidate in prioritized[len(retained):]
    ):
        raise ProjectionError("bound relation exceeds the projection byte budget")
    return _projection_envelope(
        projection,
        relations=retained,
        tombstones=compact_tombstones,
        history_complete=False,
        overflow=True,
        tombstone_overflow=tombstone_overflow,
    )


def _valid_v1_tombstone(item: dict[str, Any]) -> bool:
    return bool(
        isinstance(item.get("project"), str)
        and item["project"]
        and isinstance(item.get("worktree_id"), str)
        and item["worktree_id"]
        and item.get("role") in {"bound", "controller"}
        and type(item.get("relation_revision")) is int
        and item["relation_revision"] >= 0
    )


def _migrate_v1_projection(
    projection: dict[str, Any],
    *,
    conservative_reconstruction: bool = False,
) -> dict[str, Any]:
    if projection.get("version") != 1:
        raise ProjectionError("v1 migration requires a version 1 projection")
    if conservative_reconstruction:
        overflow = projection.get("overflow") is True
    else:
        overflow = bool(projection_metadata(projection)["overflow"])

    sequence = 0
    malformed_tombstone = False
    migrated_tombstones = []
    for item in projection.get("relation_tombstones", []):
        if not isinstance(item, dict) or not _valid_v1_tombstone(item):
            malformed_tombstone = True
            continue
        sequence += 1
        migrated_tombstones.append({
            "key_sha256": _relation_key_sha256(_relation_key(item)),
            "relation_revision": item["relation_revision"],
            "sequence": sequence,
        })

    migrated = dict(projection)
    migrated.update({
        "version": SCHEMA_VERSION,
        "relations": [
            item
            for item in projection.get("relations", [])
            if isinstance(item, dict)
        ],
        "relation_tombstones": migrated_tombstones,
        "tombstone_sequence": sequence,
        "history_complete": False,
        "overflow": overflow,
        "omitted_relations": None if overflow else 0,
        "tombstone_overflow": bool(
            conservative_reconstruction or malformed_tombstone
        ),
    })
    return _select_relations(
        migrated,
        migrated["relations"],
        migrated_tombstones,
    )


def _recover_v2_projection(projection: dict[str, Any]) -> dict[str, Any]:
    overflow = projection.get("overflow") is True
    tombstones = [
        item
        for item in projection.get("relation_tombstones", [])
        if isinstance(item, dict) and _valid_v2_tombstone(item)
    ]
    sequence = max(
        projection.get("tombstone_sequence", 0)
        if type(projection.get("tombstone_sequence")) is int
        and projection.get("tombstone_sequence", 0) >= 0
        else 0,
        max((item["sequence"] for item in tombstones), default=0),
    )
    recovered = dict(projection)
    recovered.update({
        "version": SCHEMA_VERSION,
        "relations": [
            item
            for item in projection.get("relations", [])
            if isinstance(item, dict)
        ],
        "relation_tombstones": tombstones,
        "tombstone_sequence": sequence,
        "history_complete": False,
        "overflow": overflow,
        "omitted_relations": None if overflow else 0,
        "tombstone_overflow": True,
    })
    return _select_relations(
        recovered,
        recovered["relations"],
        tombstones,
    )


def _merge_relation(
    projection: dict[str, Any],
    relation: dict[str, Any],
) -> dict[str, Any]:
    original = projection
    if projection.get("version") == 1:
        projection = _migrate_v1_projection(projection)
    elif projection.get("version") != SCHEMA_VERSION:
        raise ProjectionError(
            f"unsupported projection version {projection.get('version')}"
        )
    relation_key = _relation_key(relation)
    digest = _relation_key_sha256(relation_key)
    tombstones = [
        item
        for item in projection.get("relation_tombstones", [])
        if isinstance(item, dict) and _valid_v2_tombstone(item)
    ]
    tombstone = next(
        (
            item for item in tombstones if item.get("key_sha256") == digest
        ),
        None,
    )
    incoming_revision = _relation_revision(relation)
    if (
        tombstone is not None
        and _relation_revision(tombstone) >= incoming_revision
    ):
        return projection if projection is not original else original
    tombstones = [
        item for item in tombstones
        if item.get("key_sha256") != digest
    ]
    existing = next(
        (
            item for item in projection.get("relations", [])
            if isinstance(item, dict) and _relation_key(item) == relation_key
        ),
        None,
    )
    if existing is not None:
        existing_vector = _revision_vector(existing)
        incoming_vector = _revision_vector(relation)
        if existing_vector is not None and incoming_vector is not None:
            ordering = _compare_revision_vectors(
                existing_vector,
                incoming_vector,
            )
            if ordering in {"newer", "collision"}:
                return projection if projection is not original else original
        existing_revision = existing.get("relation_revision", 0)
        if (
            isinstance(existing_revision, int)
            and existing_revision > incoming_revision
        ):
            return projection if projection is not original else original
        relation = _merge_additive_relation_fields(existing, relation)
    relations = [
        item for item in projection.get("relations", [])
        if isinstance(item, dict) and _relation_key(item) != relation_key
    ]
    relations.append(relation)
    return _select_relations(projection, relations, tombstones)


def _remove_relation(
    projection: dict[str, Any],
    relation_key: tuple[str, str, str],
    relation_revision: int,
) -> dict[str, Any]:
    original = projection
    if projection.get("version") == 1:
        projection = _migrate_v1_projection(projection)
    elif projection.get("version") != SCHEMA_VERSION:
        raise ProjectionError(
            f"unsupported projection version {projection.get('version')}"
        )
    digest = _relation_key_sha256(relation_key)
    existing = next(
        (
            item for item in projection.get("relations", [])
            if isinstance(item, dict) and _relation_key(item) == relation_key
        ),
        None,
    )
    if (
        existing is not None
        and _relation_revision(existing) > relation_revision
    ):
        return projection if projection is not original else original
    relations = [
        item for item in projection.get("relations", [])
        if isinstance(item, dict) and _relation_key(item) != relation_key
    ]
    tombstones = [
        item
        for item in projection.get("relation_tombstones", [])
        if isinstance(item, dict)
        and _valid_v2_tombstone(item)
        and item.get("key_sha256") != digest
    ]
    current_tombstone = next(
        (
            item
            for item in projection.get("relation_tombstones", [])
            if isinstance(item, dict)
            and _valid_v2_tombstone(item)
            and item.get("key_sha256") == digest
        ),
        None,
    )
    if (
        current_tombstone is not None
        and _relation_revision(current_tombstone) >= relation_revision
        and len(relations) == len(projection.get("relations", []))
    ):
        return projection if projection is not original else original
    if (
        current_tombstone is not None
        and current_tombstone["relation_revision"] >= relation_revision
    ):
        tombstones.append(current_tombstone)
        return _select_relations(projection, relations, tombstones)

    sequence = projection.get("tombstone_sequence", 0)
    if type(sequence) is not int or sequence < 0:
        raise ProjectionError("invalid v2 projection tombstone_sequence")
    sequence += 1
    tombstones.append({
        "key_sha256": digest,
        "relation_revision": relation_revision,
        "sequence": sequence,
    })
    updated = dict(projection)
    updated["tombstone_sequence"] = sequence
    return _select_relations(updated, relations, tombstones)


def _encode(projection: dict[str, Any]) -> bytes:
    encoded = _encode_unchecked(projection)
    if len(encoded) > MAX_BYTES:
        raise ProjectionError(f"projection exceeds {MAX_BYTES} bytes")
    return encoded


def _atomic_replace(target: Path, temp_dir: Path, content: bytes) -> None:
    from . import tracking

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.stem}.",
        suffix=".tmp",
        dir=temp_dir,
    )
    fd_open = True
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd_open = False
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists() and (_is_reparse(target) or not target.is_file()):
            raise ProjectionError(f"unsafe projection target: {target}")
        tracking._replace_with_retry(temp_name, str(target))
        if os.name != "nt":
            try:
                target.chmod(0o600)
            except OSError:
                pass
    except BaseException:
        if fd_open:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _load_writable_projection(
    session_id: str,
    target: Path,
    *,
    initial_registration: bool = False,
    reconstruct_corrupt: bool = False,
) -> tuple[dict[str, Any], bool, bool]:
    target_was_missing = not target.exists()
    try:
        current = read(session_id)
    except ProjectionError:
        try:
            return _read_recoverable(session_id), True, False
        except (UnsupportedProjectionVersion, ProjectionUnavailable):
            raise
        except ProjectionError:
            if not reconstruct_corrupt:
                raise
            return _empty_projection(session_id), True, False
    if current is None:
        return (
            _empty_projection(
                session_id,
                history_complete=initial_registration and target_was_missing,
                tombstone_overflow=not (
                    initial_registration and target_was_missing
                ),
            ),
            False,
            target_was_missing,
        )
    if current.get("version") == 1:
        try:
            migrated = _migrate_v1_projection(current)
        except ProjectionError:
            if not reconstruct_corrupt:
                raise
            migrated = _migrate_v1_projection(
                current,
                conservative_reconstruction=True,
            )
        return migrated, True, False
    if current.get("version") != SCHEMA_VERSION:
        raise UnsupportedProjectionVersion(
            "projection version "
            f"{current.get('version')} is newer than supported {SCHEMA_VERSION}"
        )
    return current, False, False


def _sync_relation(
    record: Any,
    session_id: str,
    *,
    relation: dict[str, Any] | None,
    role: str,
    remove_missing: bool,
    blocking: bool,
    initial_registration: bool = False,
) -> SyncOutcome:
    try:
        target, lock_base, temp_dir = _session_paths(session_id, writing=True)
        from . import tracking

        with tracking._RecordLock(
            lock_base,
            blocking=blocking,
            require_sidecar=True,
        ) as lock:
            if not lock.acquired:
                return "deferred"
            if target.exists() and target.stat().st_size > MAX_BYTES:
                return "blocked"
            try:
                current, rewrite_required, _missing_projection = (
                    _load_writable_projection(
                        session_id,
                        target,
                        initial_registration=initial_registration,
                        reconstruct_corrupt=relation is not None,
                    )
                )
            except UnsupportedProjectionVersion:
                return "blocked"
            except ProjectionUnavailable:
                return "deferred"
            except ProjectionError:
                return "deferred" if relation is None else "blocked"
            if relation is None:
                if not remove_missing:
                    return "current"
                updated = _remove_relation(
                    current,
                    (record.repo, record.worktree_id, role),
                    record.controller_revision,
                )
            else:
                updated = _merge_relation(current, relation)
            if updated == current and not rewrite_required:
                return "current"
            encoded = _encode(updated)
            _atomic_replace(target, temp_dir, encoded)
        return "written"
    except ProjectionUnavailable:
        return "deferred"
    except ProjectionError:
        return "blocked"
    except Exception:
        return "deferred"


def sync_bound(
    record: Any,
    session_id: str,
    *,
    blocking: bool = True,
    initial_registration: bool = False,
) -> SyncOutcome:
    """Best-effort projection of one bound session after record persistence."""
    return _sync_relation(
        record,
        session_id,
        relation=bound_relation(record, session_id),
        role="bound",
        remove_missing=False,
        blocking=blocking,
        initial_registration=initial_registration,
    )


def sync_controller(
    record: Any,
    session_id: str,
    *,
    blocking: bool = True,
) -> SyncOutcome:
    """Upsert or retract one exact controller-session projection relation."""
    return _sync_relation(
        record,
        session_id,
        relation=controller_relation(record, session_id),
        role="controller",
        remove_missing=True,
        blocking=blocking,
    )


def _revision_vector(
    relation: dict[str, Any],
) -> tuple[int, int] | None:
    role = relation.get("role")
    secondary_key = (
        "head_revision" if role == "bound" else "controller_revision"
    )
    primary = relation.get("relation_revision")
    secondary = relation.get(secondary_key)
    if (
        not isinstance(primary, int)
        or primary < 0
        or not isinstance(secondary, int)
        or secondary < 0
    ):
        return None
    return primary, secondary


def _compare_revision_vectors(
    current: tuple[int, int],
    expected: tuple[int, int],
) -> Literal["current", "stale", "newer", "collision"]:
    if current == expected:
        return "current"
    if all(left <= right for left, right in zip(current, expected)):
        return "stale"
    if all(left >= right for left, right in zip(current, expected)):
        return "newer"
    return "collision"


def _audit_item(
    record: Any,
    session_id: str,
    role: Literal["bound", "controller"],
) -> dict[str, Any]:
    return {
        "project": record.repo,
        "worktree_id": record.worktree_id,
        "session_id": session_id,
        "role": role,
        "status": "current",
        "repairable": False,
        "repaired": False,
        "restored": False,
        "relation_set_incomplete": None,
        "tombstone_overflow": None,
    }


def _classify_relation(
    record: Any,
    session_id: str,
    *,
    role: Literal["bound", "controller"],
    projection: dict[str, Any] | None,
    restored: bool,
    record_loader: RecordLoader,
) -> dict[str, Any]:
    item = _audit_item(record, session_id, role)
    item["restored"] = restored
    expected = (
        bound_relation(record, session_id)
        if role == "bound"
        else controller_relation(record, session_id)
    )
    if expected is None:
        item["status"] = "missing-authority"
        return item
    if restored:
        validation = validate_restored_hint(
            session_id,
            projection,
            record_loader=record_loader,
        )
        if validation["status"] not in RESTORED_VALIDATED_STATUSES:
            item["status"] = validation["status"]
            return item
    if projection is None:
        item["status"] = (
            "restored-missing-projection" if restored else "missing"
        )
        item["repairable"] = not restored
        return item
    try:
        metadata = projection_metadata(projection)
    except ProjectionError:
        item["status"] = "restored-invalid" if restored else "invalid"
        return item
    relation_set_incomplete = bool(metadata["relation_set_incomplete"])
    writable_projection = metadata["version"] in {1, SCHEMA_VERSION}
    item["relation_set_incomplete"] = relation_set_incomplete
    item["tombstone_overflow"] = metadata["tombstone_overflow"]
    if restored and metadata["version"] == 1 and relation_set_incomplete:
        item["status"] = "restored-incomplete"
        return item

    relation_key = _relation_key(expected)
    relations = [
        relation
        for relation in projection.get("relations", [])
        if isinstance(relation, dict)
        and _relation_key(relation) == relation_key
    ]
    if len(relations) > 1:
        item["status"] = "restored-ambiguous" if restored else "ambiguous"
        return item
    if not relations:
        tombstones = _matching_tombstones(projection, relation_key)
        if len(tombstones) > 1:
            item["status"] = "restored-ambiguous" if restored else "ambiguous"
            return item
        if tombstones:
            tombstone_revision = _relation_revision(tombstones[0])
            expected_revision = _relation_revision(expected)
            if tombstone_revision > expected_revision:
                item["status"] = (
                    "restored-newer" if restored else "newer-state"
                )
                return item
            if tombstone_revision == expected_revision:
                item["status"] = (
                    "restored-collision" if restored else "collision"
                )
                return item
            item["status"] = "restored-stale" if restored else "stale"
            item["repairable"] = not restored and writable_projection
            return item
        if role == "bound":
            other_bound = [
                relation
                for relation in projection.get("relations", [])
                if isinstance(relation, dict)
                and relation.get("role") == "bound"
            ]
            if other_bound:
                item["status"] = (
                    "restored-collision" if restored else "collision"
                )
                return item
        if relation_set_incomplete:
            item["status"] = (
                "restored-incomplete" if restored else "incomplete"
            )
            return item
        item["status"] = "restored-missing-relation" if restored else "missing"
        item["repairable"] = not restored and writable_projection
        return item

    current = relations[0]
    current_vector = _revision_vector(current)
    expected_vector = _revision_vector(expected)
    if current_vector is None or expected_vector is None:
        item["status"] = "restored-invalid" if restored else "invalid"
        item["repairable"] = not restored and writable_projection
        return item
    revision_status = _compare_revision_vectors(
        current_vector,
        expected_vector,
    )
    if revision_status == "newer":
        item["status"] = "restored-newer" if restored else "newer-state"
        return item
    if revision_status == "collision":
        item["status"] = "restored-collision" if restored else "collision"
        return item
    if revision_status == "stale":
        item["status"] = "restored-stale" if restored else "stale"
        item["repairable"] = not restored and writable_projection
        return item
    if _known_relation(current) != _known_relation(expected):
        item["status"] = "restored-collision" if restored else "collision"
        return item
    item["status"] = "restored-current" if restored else "current"
    return item


def _repair_relation(
    record: Any,
    session_id: str,
    *,
    role: Literal["bound", "controller"],
    record_loader: RecordLoader,
) -> dict[str, Any]:
    try:
        from . import tracking

        record_path = record.yaml_path
        with tracking._RecordLock(record_path, require_sidecar=True):
            if not record_path.is_file():
                item = _audit_item(record, session_id, role)
                item["status"] = "missing-authority"
                return item
            authoritative = tracking.load_record(record_path)
            target, lock_base, temp_dir = _session_paths(
                session_id,
                writing=True,
            )
            with tracking._RecordLock(
                lock_base,
                blocking=True,
                require_sidecar=True,
            ):
                if target.exists() and target.stat().st_size > MAX_BYTES:
                    item = _audit_item(record, session_id, role)
                    item["status"] = "repair-blocked"
                    return item
                try:
                    current, rewrite_required, missing_projection = (
                        _load_writable_projection(
                            session_id,
                            target,
                            reconstruct_corrupt=True,
                        )
                    )
                except UnsupportedProjectionVersion:
                    item = _audit_item(record, session_id, role)
                    item["status"] = "newer-schema"
                    return item
                except ProjectionUnavailable:
                    item = _audit_item(record, session_id, role)
                    item["status"] = "repair-deferred"
                    return item
                except ProjectionError:
                    item = _audit_item(record, session_id, role)
                    item["status"] = "repair-blocked"
                    return item

                if not missing_projection:
                    locked = _classify_relation(
                        authoritative,
                        session_id,
                        role=role,
                        projection=current,
                        restored=False,
                        record_loader=record_loader,
                    )
                    rewrite_with_relation = (
                        rewrite_required
                        and locked["status"] in {
                            "current",
                            "incomplete",
                            "missing",
                        }
                    )
                    if not locked["repairable"] and not rewrite_with_relation:
                        return locked
                else:
                    locked = _audit_item(authoritative, session_id, role)
                expected = (
                    bound_relation(authoritative, session_id)
                    if role == "bound"
                    else controller_relation(authoritative, session_id)
                )
                if expected is None:
                    locked["status"] = "missing-authority"
                    locked["repairable"] = False
                    return locked
                updated = _merge_relation(current, expected)
                final = _classify_relation(
                    authoritative,
                    session_id,
                    role=role,
                    projection=updated,
                    restored=False,
                    record_loader=record_loader,
                )
                if final["status"] != "current":
                    return final
                if updated != current or rewrite_required:
                    _atomic_replace(target, temp_dir, _encode(updated))
                final["status"] = "repaired"
                final["repairable"] = True
                final["repaired"] = True
                return final
    except ProjectionUnavailable:
        item = _audit_item(record, session_id, role)
        item["status"] = "repair-deferred"
        return item
    except ProjectionError:
        item = _audit_item(record, session_id, role)
        item["status"] = "repair-blocked"
        return item
    except Exception:
        item = _audit_item(record, session_id, role)
        item["status"] = "repair-deferred"
        return item


def audit_relation(
    record: Any,
    session_id: str,
    *,
    role: Literal["bound", "controller"],
    apply: bool = False,
    record_loader: RecordLoader = _default_record_loader,
) -> dict[str, Any]:
    """Inspect and optionally repair one exact authoritative projection relation."""
    item = _audit_item(record, session_id, role)
    restored = False
    try:
        restored = is_restored(session_id)
        projection = read(session_id)
    except MissingSessionTree:
        item["status"] = "missing-session-tree"
        return item
    except UnsupportedProjectionVersion:
        item["status"] = "newer-schema"
        return item
    except ProjectionUnavailable:
        item["status"] = "unavailable"
        return item
    except ProjectionError:
        if restored:
            item["status"] = "restored-invalid"
            return item
        projection = None
    item = _classify_relation(
        record,
        session_id,
        role=role,
        projection=projection,
        restored=restored,
        record_loader=record_loader,
    )
    migrate_v1 = bool(
        apply
        and not restored
        and projection is not None
        and projection.get("version") == 1
    )
    if apply and (item["repairable"] or migrate_v1):
        return _repair_relation(
            record,
            session_id,
            role=role,
            record_loader=record_loader,
        )
    return item


def backfill_relations(
    records: list[Any],
    *,
    apply: bool = False,
    budget: int = 256,
    record_loader: RecordLoader = _default_record_loader,
) -> dict[str, Any]:
    """Bounded explicit audit/backfill for known session projection relations."""
    candidates: list[tuple[Any, str, Literal["bound", "controller"]]] = []
    seen: set[tuple[str, str, str, str]] = set()
    bound_owners: dict[str, set[tuple[str, str]]] = {}
    for record in sorted(records, key=lambda item: item.worktree_id):
        for entry in record.sessions or ():
            key = (record.repo, record.worktree_id, entry.session_id, "bound")
            bound_owners.setdefault(entry.session_id, set()).add(
                (record.repo, record.worktree_id)
            )
            if key not in seen:
                seen.add(key)
                candidates.append((record, entry.session_id, "bound"))
        for relation in record.controllers:
            if not relation.controller_session_id:
                continue
            key = (
                record.repo,
                record.worktree_id,
                relation.controller_session_id,
                "controller",
            )
            if key not in seen:
                seen.add(key)
                candidates.append(
                    (record, relation.controller_session_id, "controller")
                )
    limit = max(0, budget)
    items = []
    for record, session_id, role in candidates[:limit]:
        if role == "bound" and len(bound_owners.get(session_id, set())) > 1:
            item = _audit_item(record, session_id, role)
            item["status"] = "ambiguous-authority"
            items.append(item)
            continue
        items.append(
            audit_relation(
                record,
                session_id,
                role=role,
                apply=apply,
                record_loader=record_loader,
            )
        )
    status_counts: dict[str, int] = {}
    for item in items:
        status = str(item["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "budget": limit,
        "candidates": len(candidates),
        "checked": len(items),
        "remaining": max(0, len(candidates) - len(items)),
        "repairable": sum(bool(item["repairable"]) for item in items),
        "repaired": sum(bool(item["repaired"]) for item in items),
        "report_only": sum(
            item["status"] not in {"current", "repaired"}
            and not item["repairable"]
            for item in items
        ),
        "status_counts": status_counts,
        "items": items,
    }


def _cwd_is_within(cwd: str | None, root: str) -> bool:
    if not cwd or not root:
        return False
    try:
        candidate = os.path.normcase(os.path.realpath(cwd))
        expected = os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([candidate, expected]) == expected
    except (OSError, ValueError):
        return False


def _configured_machine(project: str) -> str:
    from . import config as cfg

    try:
        return cfg.load_config(
            path=cfg.project_dir(project) / "config.yaml",
            project=project,
        ).machine
    except Exception:
        try:
            return cfg.detect_machine()
        except Exception:
            return ""


def recovery_report(
    session_id: str,
    *,
    cwd: str | None = None,
    record_loader: RecordLoader = _default_record_loader,
    local_machine: str | None = None,
) -> dict[str, Any]:
    """Return bounded, validated recovery guidance for one exact session."""
    report: dict[str, Any] = {
        "session_id": session_id,
        "status": "missing-projection",
        "restored": False,
        "schema_version": None,
        "history_complete": None,
        "overflow": None,
        "omitted_relations": None,
        "tombstone_overflow": None,
        "relations": [],
        "recommended_action": "none",
    }
    try:
        restored = is_restored(session_id)
        projection = read(session_id)
    except MissingSessionTree:
        report["status"] = "missing-session-tree"
        return report
    except UnsupportedProjectionVersion:
        report["status"] = "unsupported"
        report["recommended_action"] = "inspect"
        return report
    except ProjectionError:
        report["status"] = "invalid"
        report["recommended_action"] = "inspect"
        return report
    report["restored"] = restored
    if projection is None:
        return report
    try:
        metadata = projection_metadata(projection)
    except ProjectionError:
        report["status"] = "invalid"
        report["recommended_action"] = "inspect"
        return report
    report.update({
        "schema_version": metadata["version"],
        "history_complete": metadata["history_complete"],
        "overflow": metadata["overflow"],
        "omitted_relations": metadata["omitted_relations"],
        "tombstone_overflow": metadata["tombstone_overflow"],
    })
    relation_set_incomplete = bool(metadata["relation_set_incomplete"])
    if metadata["version"] == 1 and relation_set_incomplete:
        report["status"] = "incomplete"
        report["recommended_action"] = "inspect"
        return report
    if restored:
        validation = validate_restored_hint(
            session_id,
            projection,
            record_loader=record_loader,
        )
        if validation["status"] not in RESTORED_VALIDATED_STATUSES:
            report["status"] = validation["status"]
            report["recommended_action"] = "inspect"
            return report

    bound_relations = [
        relation
        for relation in projection.get("relations", [])
        if isinstance(relation, dict) and relation.get("role") == "bound"
    ]
    if len(bound_relations) > 1:
        report["status"] = "ambiguous"
        report["recommended_action"] = "inspect"
        return report
    validated = []
    for relation in projection.get("relations", []):
        if not isinstance(relation, dict):
            continue
        role = relation.get("role")
        project = relation.get("project")
        worktree_id = relation.get("worktree_id")
        if (
            role not in {"bound", "controller"}
            or not _safe_identity_token(project)
            or not _safe_identity_token(worktree_id)
        ):
            validated.append({
                "status": "invalid",
                "role": role,
                "project": project,
                "worktree_id": worktree_id,
            })
            continue
        try:
            record = record_loader(str(project), str(worktree_id))
        except Exception:
            validated.append({
                "status": "unreadable",
                "role": role,
                "project": project,
                "worktree_id": worktree_id,
            })
            continue
        if record is None:
            validated.append({
                "status": "foreign",
                "role": role,
                "project": project,
                "worktree_id": worktree_id,
            })
            continue
        item = _classify_relation(
            record,
            session_id,
            role=role,
            projection=projection,
            restored=restored,
            record_loader=record_loader,
        )
        relation_status = item["status"]
        if relation_status in {"current", "restored-current"}:
            if role == "bound":
                entry = record.session_entry(session_id)
                if entry is not None and entry.state == "handed-off":
                    item["successor_session_id"] = entry.successor
                    terminal = {"status": "error", "terminal_session_id": None}
                    try:
                        from . import controller_lineage

                        terminal = controller_lineage.resolve_terminal_session(
                            record,
                            session_id,
                        )
                    except Exception:
                        pass
                    item["terminal_status"] = terminal["status"]
                    item["terminal_session_id"] = terminal[
                        "terminal_session_id"
                    ]
                    relation_status = (
                        "handed-off"
                        if terminal["status"] == "resolved"
                        and terminal["terminal_session_id"]
                        else "handoff-unresolved"
                    )
                elif entry is not None and entry.state == "concluded":
                    relation_status = "concluded"
                elif _cwd_is_within(cwd, record.worktree_path):
                    relation_status = "bound-here"
                else:
                    relation_status = "bound-elsewhere"
                item["worktree_path"] = record.worktree_path
            else:
                controller = record.controller_for_session(session_id)
                if controller is not None and controller.state == "ended":
                    relation_status = "controller-terminal"
                effective_local_machine = (
                    local_machine
                    if local_machine is not None
                    else _configured_machine(str(project))
                )
                if (
                    relation_status != "controller-terminal"
                    and effective_local_machine
                    and record.machine != effective_local_machine
                ):
                    relation_status = "controlled-remote"
                elif relation_status != "controller-terminal":
                    relation_status = "controlled-elsewhere"
                item["machine"] = record.machine
                item["worktree_path"] = record.worktree_path
        item["status"] = relation_status
        validated.append(item)
    report["relations"] = validated
    if relation_set_incomplete:
        report["status"] = "incomplete"
        report["recommended_action"] = "inspect"
        return report

    bound = [
        item
        for item in validated
        if item.get("role") == "bound"
        and item.get("status") in {
            "bound-here",
            "bound-elsewhere",
            "handed-off",
            "handoff-unresolved",
            "concluded",
        }
    ]
    if len(bound) == 1:
        report["status"] = bound[0]["status"]
        report["recommended_action"] = {
            "bound-here": "none",
            "bound-elsewhere": "verify-and-bind",
            "handed-off": "continue-successor",
            "handoff-unresolved": "inspect",
            "concluded": "none",
        }[report["status"]]
        report["primary"] = bound[0]
        return report
    if len(bound) > 1:
        report["status"] = "ambiguous"
        report["recommended_action"] = "inspect"
        return report

    controllers = [
        item
        for item in validated
        if item.get("role") == "controller"
        and item.get("status") in {
            "controlled-elsewhere",
            "controlled-remote",
            "controller-terminal",
        }
    ]
    if controllers:
        live = [
            item
            for item in controllers
            if item["status"] != "controller-terminal"
        ]
        if live:
            report["status"] = (
                "controlled-remote"
                if all(item["status"] == "controlled-remote" for item in live)
                else "controlled-elsewhere"
            )
            report["recommended_action"] = "inspect-controllers"
        else:
            report["status"] = "controller-terminal"
            report["recommended_action"] = "none"
        return report

    problematic = [
        str(item.get("status"))
        for item in validated
        if item.get("status") not in {"current", "restored-current"}
    ]
    if problematic:
        report["status"] = (
            "ambiguous"
            if any(
                status in {
                    "ambiguous",
                    "ambiguous-authority",
                    "collision",
                    "restored-ambiguous",
                    "restored-collision",
                }
                for status in problematic
            )
            else problematic[0]
        )
        report["recommended_action"] = "inspect"
    elif validated:
        report["status"] = "current"
    else:
        report["status"] = "unbound-projection"
        report["recommended_action"] = "inspect"
    return report


def render_recovery_context(report: dict[str, Any]) -> str:
    """Render one concise sessionStart recovery pointer."""
    status = report.get("status")
    primary = report.get("primary") or {}
    restored = " restored" if report.get("restored") else ""
    if status == "bound-elsewhere":
        return (
            f"[agent-worktrees] Recovery hint: this exact session has a validated"
            f"{restored} projection for "
            f"{primary.get('project')}/{primary.get('worktree_id')}, but the "
            f"startup cwd is elsewhere. Verify with `agent-worktrees "
            f"session-recovery --session-id {report.get('session_id')}`; if "
            f"continuing this work, change to {primary.get('worktree_path')} "
            "and bind explicitly. The projection did not change the binding."
        )
    if status == "handed-off":
        successor = primary.get("terminal_session_id")
        return (
            f"[agent-worktrees] Recovery hint: this exact session handed off "
            f"{primary.get('project')}/{primary.get('worktree_id')} to session "
            f"{successor}. Continue in that successor unless intentionally "
            "reclaiming the work; the projection did not change the binding."
        )
    if status == "handoff-unresolved":
        return (
            f"[agent-worktrees] Recovery hint: this exact session handed off "
            f"{primary.get('project')}/{primary.get('worktree_id')}, but its "
            f"terminal lineage is {primary.get('terminal_status')}. Inspect "
            f"`agent-worktrees session-recovery --session-id "
            f"{report.get('session_id')}`; no terminal successor was selected "
            "and no binding was changed."
        )
    if status == "concluded":
        return (
            f"[agent-worktrees] Recovery hint: this exact session previously "
            f"concluded its work on "
            f"{primary.get('project')}/{primary.get('worktree_id')}. It has no "
            "successor recovery action; the projection did not change the "
            "binding."
        )
    if status in {"controlled-elsewhere", "controlled-remote"}:
        relations = [
            item
            for item in report.get("relations", [])
            if item.get("status") in {
                "controlled-elsewhere",
                "controlled-remote",
            }
        ][:3]
        targets = ", ".join(
            f"{item.get('machine')}/{item.get('project')}/"
            f"{item.get('worktree_id')}"
            for item in relations
        )
        return (
            f"[agent-worktrees] Recovery hint: this exact session has validated"
            f"{restored} controller relations for {targets}. Inspect them with "
            f"`agent-worktrees session-recovery --session-id "
            f"{report.get('session_id')}` before acting; controller metadata "
            "does not bind this session to those worktrees."
        )
    if report.get("recommended_action") == "inspect":
        return (
            f"[agent-worktrees] Recovery hint: this exact session's projection "
            f"is {status} and cannot be used automatically. Inspect it with "
            f"`agent-worktrees session-recovery --session-id "
            f"{report.get('session_id')}`; no binding was changed."
        )
    return ""
