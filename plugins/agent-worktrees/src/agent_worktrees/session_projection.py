"""Reciprocal, rebuildable worktree metadata beside one Copilot session."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Literal

from . import sessions

SCHEMA_VERSION = 1
SIDECAR_NAME = "agent-worktrees.json"
MAX_BYTES = 128 * 1024
MAX_RELATIONS = 128
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
SyncOutcome = Literal["written", "current", "blocked", "deferred"]


class ProjectionError(ValueError):
    """The session projection cannot be read or updated safely."""


class UnsupportedProjectionVersion(ProjectionError):
    """The projection was written by a newer incompatible implementation."""


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


def _session_paths(
    session_id: str,
    *,
    writing: bool = False,
) -> tuple[Path, Path, Path]:
    if not _valid_session_id(session_id):
        raise ProjectionError(f"invalid session id {session_id!r}")
    root = sessions._session_state_dir()
    if not root.is_dir() or _is_reparse(root):
        raise ProjectionError(f"unsafe or missing session-state root: {root}")
    session_dir = root / session_id
    if not session_dir.is_dir() or _is_reparse(session_dir):
        raise ProjectionError(f"unsafe or missing session directory: {session_dir}")
    if writing and (session_dir / "rescued-origin.json").exists():
        raise ProjectionError(f"restored session is read-only: {session_id}")
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


def _empty_projection(session_id: str) -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "session_id": session_id,
        "relations": [],
        "overflow": False,
        "omitted_relations": 0,
    }


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
    if version > SCHEMA_VERSION:
        raise UnsupportedProjectionVersion(
            f"projection version {version} is newer than supported {SCHEMA_VERSION}"
        )
    if version != SCHEMA_VERSION:
        raise ProjectionError(f"unsupported projection version {version}")
    if loaded.get("session_id") != session_id:
        raise ProjectionError("projection session_id does not match its directory")
    relations = loaded.get("relations")
    if not isinstance(relations, list):
        raise ProjectionError("projection relations must be an array")
    if any(not isinstance(relation, dict) for relation in relations):
        raise ProjectionError("projection relations must contain JSON objects")
    return loaded


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


def _relation_key(relation: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(relation.get("project") or ""),
        str(relation.get("worktree_id") or ""),
        str(relation.get("role") or ""),
    )


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


def _merge_relation(
    projection: dict[str, Any],
    relation: dict[str, Any],
) -> dict[str, Any]:
    relation_key = _relation_key(relation)
    existing = next(
        (
            item for item in projection.get("relations", [])
            if isinstance(item, dict) and _relation_key(item) == relation_key
        ),
        None,
    )
    if existing is not None:
        existing_revision = existing.get("relation_revision", 0)
        incoming_revision = relation.get("relation_revision", 0)
        if (
            isinstance(existing_revision, int)
            and isinstance(incoming_revision, int)
            and existing_revision > incoming_revision
        ):
            return projection
    relations = [
        item for item in projection.get("relations", [])
        if isinstance(item, dict) and _relation_key(item) != relation_key
    ]
    relations.append(relation)
    relations.sort(key=_relation_sort_key)
    prior_omitted = projection.get("omitted_relations", 0)
    if not isinstance(prior_omitted, int) or prior_omitted < 0:
        prior_omitted = 0
    newly_omitted = max(0, len(relations) - MAX_RELATIONS)
    omitted = (
        prior_omitted + newly_omitted
        if existing is None
        else max(prior_omitted, newly_omitted)
    )
    if newly_omitted:
        bound = [item for item in relations if item.get("role") == "bound"]
        nonterminal = [
            item for item in relations
            if item.get("role") != "bound" and _protected_relation(item)
        ]
        terminal = [item for item in relations if not _protected_relation(item)]
        for group in (bound, nonterminal, terminal):
            group.sort(key=_relation_sort_key)
        retained = bound[-MAX_RELATIONS:]
        remaining = MAX_RELATIONS - len(retained)
        if remaining:
            retained = retained + nonterminal[-remaining:]
            remaining = MAX_RELATIONS - len(retained)
        if remaining:
            retained = retained + terminal[-remaining:]
        relations = retained
        relations.sort(key=_relation_sort_key)
    result = dict(projection)
    result.update({
        "version": SCHEMA_VERSION,
        "session_id": projection["session_id"],
        "relations": relations,
        "overflow": bool(omitted or projection.get("overflow")),
        "omitted_relations": omitted,
    })
    return result


def _encode(projection: dict[str, Any]) -> bytes:
    encoded = (
        json.dumps(projection, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")
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


def sync_bound(record: Any, session_id: str) -> SyncOutcome:
    """Best-effort projection of one bound session after record persistence."""
    relation = bound_relation(record, session_id)
    if relation is None:
        return "current"
    try:
        target, lock_base, temp_dir = _session_paths(session_id, writing=True)
        from . import tracking

        with tracking._RecordLock(lock_base, require_sidecar=True):
            try:
                current = read(session_id) or _empty_projection(session_id)
            except UnsupportedProjectionVersion:
                return "blocked"
            except ProjectionError:
                current = _empty_projection(session_id)
            updated = _merge_relation(current, relation)
            if updated == current:
                return "current"
            encoded = _encode(updated)
            _atomic_replace(target, temp_dir, encoded)
        return "written"
    except Exception:
        return "deferred"
