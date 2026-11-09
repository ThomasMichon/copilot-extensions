"""Outgoing-only custody that must not elect the incoming monitor."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path

from zdd import breadcrumb, routing, routing_persistence

from . import locks, tracking

_REFERENCE_PREFIX = "worktrees-monitor-observation:"


def active(root: Path) -> routing.Endpoint | None:
    """Validate publication storage without probing any outgoing native process."""
    table = routing.read_table(root / "status-monitor-routing", strict=True)
    routing._validate_endpoint_rows(table)
    raw = (table or {}).get("active")
    return routing.Endpoint.from_dict(raw) if raw is not None else None


def _identities(
    table: dict | None, record: dict | None, lock: dict | None, predecessor: dict | None,
) -> list[dict]:
    identities = []
    for row in [
        *((table or {}).get(key) for key in ("active", "previous")),
        (record or {}).get("old"),
        {
            "pid": (record or {}).get("new_pid"),
            "process_start_time": (record or {}).get("new_process_start_time"),
        },
        {
            "pid": (lock or {}).get("pid"),
            "process_start_time": (lock or {}).get("start_time"),
        },
        predecessor,
    ]:
        if isinstance(row, dict) and type(row.get("pid")) is int and row["pid"] > 0:
            identity = {"pid": row["pid"], "start": row.get("process_start_time")}
            if identity not in identities:
                identities.append(identity)
    return identities


def _record(content: dict) -> dict | None:
    try:
        raw = content["breadcrumb"]
        record = json.loads(base64.b64decode(raw, validate=True)) if raw is not None else None
    except (ValueError, UnicodeError):
        return None
    return record if isinstance(record, dict) else None


def retain(
    root: Path, *, runtime=None, detach_recovery: bool = False, predecessor: dict | None = None,
    cutover_guard=None,
) -> bool:
    """Archive exact recovery bytes and protect code, never granting retirement.

    The installation owner holds its cutover guard. References precede the
    durable archive so an interrupted archive write still cannot expose code
    to collection. Only the resident's independent exit reconciliation releases
    these barriers; this operation never cancels or recovers an older attempt.
    """
    from . import runtime_monitor_retirements as retirements

    if detach_recovery:
        from zdd.cutover_lock import CutoverLock, lock_path

        if (
            not isinstance(cutover_guard, CutoverLock) or not cutover_guard.held
            or cutover_guard.path.resolve() != lock_path(root / "status-monitor-routing").resolve()
        ):
            raise ValueError("Recovery archival requires the held publication cutover guard")
    helper = runtime if runtime is not None else retirements._runtime()
    route_root = root / "status-monitor-routing"
    directory = root / "status-monitor-observations"
    directory.mkdir(exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Monitor observation directory must not be linked")
    with tracking._RecordLock(directory / "inventory", require_sidecar=True):
        with routing.routing_lock(route_root, deadline=time.monotonic() + 5.0):
            table = routing.read_table(route_root, strict=True)
            routing._validate_endpoint_rows(table)
            path = breadcrumb.breadcrumb_path(route_root)
            try:
                info = path.lstat()
            except FileNotFoundError:
                raw = None
            else:
                if path.is_symlink() or not path.is_file() or info.st_size > 65536:
                    raise ValueError("Monitor recovery evidence must be a bounded ordinary file")
                raw = path.read_bytes()
            content = {
                "table": table,
                "breadcrumb": base64.b64encode(raw).decode("ascii") if raw is not None else None,
                "lock": locks.read_lock(root / "status-monitor.lock"),
                "predecessor": {
                    "pid": predecessor["pid"], "process_start_time": predecessor["start"],
                } if predecessor is not None else None,
            }
            if not table and raw is None and not content["lock"] and predecessor is None:
                return False
            digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
            owner = _REFERENCE_PREFIX + digest
            target = directory / (digest + ".json")
            if target.exists():
                if target.is_symlink() or target.stat().st_size > 1048576:
                    raise ValueError("Monitor observation must be a bounded ordinary file")
                retained = json.loads(target.read_text(encoding="utf-8"))
                if (
                    any(retained.get(key) != value for key, value in content.items())
                    or helper._leases.read_runtime_references(root).get(owner, []) != retained["slots"]
                ):
                    raise ValueError("Monitor observation code custody changed")
                if detach_recovery and raw is not None:
                    path.unlink()
                return True
            versions = helper.list_versions(root)
            previous = helper._leases.read_runtime_references(root).get(owner, [])
            versions = sorted(set(versions).union(previous))
            helper._leases.replace_runtime_references(root, owner, versions, expected=previous)
            routing_persistence.write(target, {
                "version": 1, **content, "slots": versions,
            })
            if detach_recovery and raw is not None:
                path.unlink()
    return True


def options(root: Path, predecessor: dict | None, *, incumbent_expected: bool) -> tuple[dict | None, dict, bool]:
    """Prefer qualified cleanup, but elect exclusively through structural CAS."""
    from . import status_monitor_cutover as cutover
    from .runtime_monitor_identity import async_predecessor

    expected = active(root)
    try:
        observed = cutover.routed_monitor_identity(root, require_async_retirement=True)
        admitted, result = async_predecessor(
            root, predecessor, observed=observed, incumbent_expected=incumbent_expected,
        )
    except (OSError, ValueError, ConnectionError):
        admitted, result = None, {}
    else:
        if expected is None and observed is None:
            return admitted, result, False
        if observed is not None and expected is not None and (
            expected.pid, expected.process_start_time,
        ) == observed[:2]:
            # Native identity is needed for cleanup, not for route authority.
            result["refuse_old"] = lambda raw: (
                None if raw == expected.to_dict() else "predecessor route changed"
            )
            return admitted, result, False
    retain(root, predecessor=predecessor)
    result = {
        "expected_old": expected,
        "refuse_old": lambda raw: (
            None if raw == (expected.to_dict() if expected is not None else None)
            else "predecessor route changed"
        ),
    }
    if expected is not None:
        result["retire_failed_predecessor"] = lambda _old: {
            "exit_confirmed": False, "reason": "outgoing-observation-deferred",
        }
    # An already-qualified caller reservation keeps its exact existing custody;
    # an unverified route row never manufactures a native retirement grant.
    return predecessor, result, True


def prepare(root: Path, predecessor: dict | None, *, incumbent_expected: bool, custody, timeout: float):
    """Keep publication serialization across archived custody and the new attempt."""
    from zdd.cutover_lock import CutoverLock

    guard = CutoverLock(root / "status-monitor-routing")
    guard.acquire(timeout=timeout)
    custody.callback(guard.release)
    predecessor, publication_options, deferred = options(
        root, predecessor, incumbent_expected=incumbent_expected,
    )
    if breadcrumb.breadcrumb_path(root / "status-monitor-routing").exists():
        deferred = retain(root, detach_recovery=True, cutover_guard=guard) or deferred
    return guard, predecessor, publication_options, deferred


def reconcile(root: Path, *, runtime=None, cleanup: bool = True) -> bool:
    """Release archived code barriers only after every named native exit."""
    from . import runtime_monitor_retirements as retirements

    directory = root / "status-monitor-observations"
    if directory.is_symlink():
        raise ValueError("Monitor observation directory must not be linked")
    if not directory.exists():
        return False
    helper = runtime if runtime is not None else retirements._runtime()
    pending = False
    with tracking._RecordLock(directory / "inventory", require_sidecar=True):
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink() or path.stat().st_size > 1048576:
                raise ValueError("Monitor observation must be a bounded ordinary file")
            record = json.loads(path.read_text(encoding="utf-8"))
            content = {key: record[key] for key in ("table", "breadcrumb", "lock", "predecessor")}
            digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
            if type(record.get("version")) is not int or record["version"] != 1 or path.stem != digest:
                raise ValueError("Monitor observation identity changed")
            identities = _identities(
                content["table"], _record(content), content["lock"], content["predecessor"],
            )
            exited = bool(identities)
            for identity in identities:
                observed = breadcrumb.owner_exited({
                    "pid": identity["pid"], "owner_process_start_time": identity["start"],
                })
                exited = exited and observed is True
                pending = pending or (observed is not True and identity["pid"] != os.getpid())
            if not identities:
                pending = True
            if cleanup and exited:
                owner = _REFERENCE_PREFIX + digest
                previous = helper._leases.read_runtime_references(root).get(owner, [])
                if previous:
                    if previous != record["slots"]:
                        raise ValueError("Monitor observation code custody changed")
                    helper._leases.replace_runtime_references(root, owner, [], expected=previous)
                path.unlink()
    return pending
