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


def _table(root: Path) -> dict | None:
    table = routing.read_table(root / "status-monitor-routing", strict=True)
    routing._validate_endpoint_rows(table)
    return table


def active(root: Path, *, fallback: bool = False) -> routing.Endpoint | None:
    """Validate publication storage without probing any outgoing native process."""
    table = _table(root)
    raw = (table or {}).get("active")
    if raw is None and fallback:
        raw = (table or {}).get("previous")
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
    except (ValueError, UnicodeError, TypeError):
        return None
    return record if isinstance(record, dict) else None


def _content(record: dict) -> dict:
    keys = ("table", "breadcrumb", "lock", "predecessor")
    if record.get("version") == 2:
        keys += ("release_claim",)
    return {key: record[key] for key in keys}


def _digest(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def _groups(record: dict, digest: str) -> dict[str, list[str]]:
    slots = record["slots"]
    if record["version"] == 1:
        return {_REFERENCE_PREFIX + digest: slots}
    return {
        f"{_REFERENCE_PREFIX}/v2/{digest}/{offset // 64}": slots[offset:offset + 64]
        for offset in range(0, max(1, len(slots)), 64)
    }


def _archives(directory: Path) -> list[tuple[Path, dict]]:
    if directory.is_symlink():
        raise ValueError("Monitor observation directory must not be linked")
    records = []
    for path in sorted([*directory.glob("*.json"), *directory.glob("*.pending")]):
        if path.is_symlink() or path.stat().st_size > 1048576:
            raise ValueError("Monitor observation must be a bounded ordinary file")
        record = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(record, dict)
            or type(record.get("version")) is not int or record["version"] not in (1, 2)
        ):
            raise ValueError("Monitor observation identity changed")
        required = {"version", "table", "breadcrumb", "lock", "predecessor", "slots"}
        if record["version"] == 2:
            required |= {"release_claim", "release_state"}
        if "cleanup_complete" in record:
            required.add("cleanup_complete")
        if (
            set(record) != required
            or type(record.get("cleanup_complete", False)) is not bool
            or any(record[key] is not None and not isinstance(record[key], dict)
                   for key in ("table", "lock", "predecessor"))
            or record["breadcrumb"] is not None and not isinstance(record["breadcrumb"], str)
            or not isinstance(record["slots"], list)
            or not all(isinstance(slot, str) for slot in record["slots"])
            or record.get("release_state", "retiring") not in ("prepared", "retiring", "cancelled")
            or path.stem != _digest(_content(record))
        ):
            raise ValueError("Monitor observation shape or identity changed")
        routing._validate_endpoint_rows(record["table"])
        claim = record.get("release_claim")
        if claim is not None and (
            not isinstance(claim, dict) or not isinstance(claim.get("owner"), str)
        ):
            raise ValueError("Monitor observation dev release changed")
        records.append((path, record))
    return records


def _reference_updates(record: dict, digest: str, references: dict, *, retain: bool) -> tuple[dict, dict]:
    groups = _groups(record, digest)
    expected = {}
    for owner, slots in groups.items():
        previous = references.get(owner, [])
        if previous and previous != slots:
            raise ValueError("Monitor observation code custody changed")
        expected[owner] = previous
    return {owner: slots if retain else [] for owner, slots in groups.items()}, expected


def _inventory(root: Path, path: Path, record: dict, helper) -> dict:
    if path.suffix == ".pending" and not record["slots"] and not record.get("cleanup_complete"):
        record = {**record, "slots": sorted(helper.list_versions(root))}
        routing_persistence.write(path, record)
    return record


def _references(
    root: Path, record: dict, digest: str, helper, *, retain: bool,
    inventory_path: Path | None = None,
) -> dict:
    with helper._leases.reference_transaction_custody(root):
        if record["version"] == 1:
            references = helper._leases.read_runtime_references(root)
        else:
            transaction = helper._leases.begin_runtime_reference_transaction(root, _REFERENCE_PREFIX)
            references = transaction["owners"]
            if inventory_path is not None:
                record = _inventory(root, inventory_path, record, helper)
        desired, expected = _reference_updates(record, digest, references, retain=retain)
        if record["version"] == 1:
            for owner, slots in desired.items():
                helper._leases.replace_runtime_references(root, owner, slots, expected=expected[owner])
        else:
            helper._leases.commit_runtime_reference_transaction(
                root, _REFERENCE_PREFIX, transaction["token"], desired, expected=expected,
            )
    return record


def _recover_archives(root: Path, directory: Path, helper) -> bool:
    records = dict(_archives(directory))
    selected = {}
    for path, record in records.items():
        target = path.with_suffix(".json")
        if path.suffix == ".pending" and target in records:
            if record["slots"] != records[target]["slots"]:
                raise ValueError("Monitor observation inventory changed")
            continue
        selected[path] = record
    if any(record["version"] == 2 for record in selected.values()):
        with helper._leases.reference_transaction_custody(root):
            transaction = helper._leases.begin_runtime_reference_transaction(root, _REFERENCE_PREFIX)
            desired, expected = {}, {}
            for path, record in selected.items():
                if record["version"] != 2:
                    continue
                record = selected[path] = _inventory(root, path, record, helper)
                updates, previous = _reference_updates(
                    record, path.stem, transaction["owners"],
                    retain=not record.get("cleanup_complete", False),
                )
                desired.update(updates)
                expected.update(previous)
            helper._leases.commit_runtime_reference_transaction(
                root, _REFERENCE_PREFIX, transaction["token"], desired, expected=expected,
            )
    for path, record in selected.items():
        if record["version"] == 1:
            _references(root, record, path.stem, helper, retain=not record.get("cleanup_complete", False))
        target = path.with_suffix(".json")
        if record.get("cleanup_complete"):
            path.unlink()
            continue
        if path.suffix == ".pending":
            routing_persistence.write(target, record)
            path.unlink()
    for path in records:
        if path.suffix == ".pending" and path.with_suffix(".json") in records:
            path.unlink()
    return any(not record.get("cleanup_complete") for record in selected.values())


def recover(root: Path, *, runtime=None) -> bool:
    """Restore archived deletion barriers before unrelated lifecycle work."""
    from . import runtime_monitor_retirements as retirements

    directory = root / "status-monitor-observations"
    if directory.is_symlink():
        raise ValueError("Monitor observation directory must not be linked")
    if not directory.exists():
        return False
    helper = runtime if runtime is not None else retirements._runtime()
    with tracking._RecordLock(directory / "inventory", require_sidecar=True):
        return _recover_archives(root, directory, helper)


def retain(
    root: Path, *, runtime=None, detach_recovery: bool = False, predecessor: dict | None = None,
    cutover_guard=None, release_claim: dict | None = None,
) -> bool:
    """Archive exact recovery bytes and protect code, never granting retirement.

    The installation owner holds its cutover guard. References precede the
    final archive, with a durable write-ahead mapping first so partial canonical
    references remain recoverable. Only the resident's independent exit
    reconciliation releases barriers; this never recovers an older attempt.
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
        _recover_archives(root, directory, helper)
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
                "release_claim": release_claim,
            }
            if not table and raw is None and not content["lock"] and predecessor is None and release_claim is None:
                return False
            digest = _digest(content)
            target = directory / (digest + ".json")
            pending = target.with_suffix(".pending")
            records = dict(_archives(directory))
            record = records.get(target) or records.get(pending)
            if record is None:
                record = {
                    "version": 2, **content, "slots": [],
                    "release_state": "prepared" if release_claim is not None else "retiring",
                }
                routing_persistence.write(pending, record)
            elif release_claim is not None and record["release_state"] == "cancelled":
                record = {**record, "release_state": "prepared"}
                routing_persistence.write(target if target in records else pending, record)
            record = _references(
                root, record, digest, helper, retain=True,
                inventory_path=pending if target not in records else None,
            )
            if target not in records:
                routing_persistence.write(target, record)
            pending.unlink(missing_ok=True)
            if detach_recovery and raw is not None:
                path.unlink()
    return True


def settle_release(root: Path, claim: dict, *, committed: bool) -> None:
    """Only the owning installer grants or revokes its exact deferred release."""
    directory = root / "status-monitor-observations"
    with tracking._RecordLock(directory / "inventory", require_sidecar=True):
        found = False
        for path, record in _archives(directory):
            if record.get("release_claim") != claim or record["release_state"] == "cancelled":
                continue
            found = True
            if record["release_state"] == "retiring" and not committed:
                raise ValueError("Committed monitor dev release cannot be cancelled")
            routing_persistence.write(path, {
                **record, "release_state": "retiring" if committed else "cancelled",
            })
        if committed and not found:
            raise ValueError("Deferred monitor dev release custody is missing")


def pending_claim(root: Path, claim: dict) -> bool:
    from .runtime_monitor_retirements import _restored_claim

    return any(
        record.get("release_claim") is not None
        and record.get("release_state") != "cancelled"
        and claim in (record["release_claim"], _restored_claim(record["release_claim"]))
        for _path, record in _archives(root / "status-monitor-observations")
    )


def cancel_prepared_release(root: Path, claim: dict, *, runtime) -> bool:
    """Revoke only unchanged, prepublication claim custody under the caller's guard."""
    directory = root / "status-monitor-observations"
    if not directory.exists():
        return False
    with tracking._RecordLock(directory / "inventory", require_sidecar=True):
        records = [
            (path, record) for path, record in _archives(directory)
            if record.get("release_claim") == claim and record["release_state"] != "cancelled"
        ]
        if not records or any(record["release_state"] != "prepared" for _path, record in records):
            return False
        with runtime._publication_lease(root):
            route_root = root / "status-monitor-routing"
            if (
                runtime.current_version(root) != (claim.get("selected_generation") or "dev")
                or runtime.read_dev_claim(root, strict=True) != claim
                or breadcrumb.breadcrumb_path(route_root).exists()
            ):
                return False
            table = routing.read_table(route_root, strict=True)
            routing._validate_endpoint_rows(table)
            if any(record["table"] != table for _path, record in records):
                return False
            for path, record in records:
                routing_persistence.write(path, {**record, "release_state": "cancelled"})
    return True


def options(root: Path, predecessor: dict | None, *, incumbent_expected: bool) -> tuple[dict | None, dict, bool]:
    """Prefer qualified cleanup, but elect exclusively through structural CAS."""
    from . import status_monitor_cutover as cutover
    from .runtime_monitor_identity import async_predecessor

    raw_expected = (_table(root) or {}).get("active")
    expected = routing.Endpoint.from_dict(raw_expected) if raw_expected is not None else None
    try:
        observed = cutover.routed_monitor_identity(root, require_async_retirement=True)
        admitted, result = async_predecessor(
            root, predecessor, observed=observed, incumbent_expected=incumbent_expected,
        )
    except (OSError, ValueError, ConnectionError):
        admitted, result = None, {}
    else:
        if expected is None and observed is None and active(root, fallback=True) is None:
            return admitted, result, False
        if observed is not None and expected is not None and (
            expected.pid, expected.process_start_time,
        ) == observed[:2]:
            # Native identity is needed for cleanup, not for route authority.
            result["preserve_raw_active"] = True
            result["refuse_old"] = lambda raw: (
                None if raw == raw_expected else "predecessor route changed"
            )
            return admitted, result, False
    retain(root, predecessor=predecessor)
    result = {
        "expected_old": expected,
        "preserve_raw_active": True,
        "refuse_old": lambda raw: (
            None if raw == raw_expected
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
    recover(root)
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
        evidence = root / "runtime-references.json"
        if not evidence.exists() and not evidence.is_symlink():
            return False
        helper = runtime if runtime is not None else retirements._runtime()
        if any(
            owner.startswith(_REFERENCE_PREFIX)
            for owner in helper._leases._read_runtime_reference_state(root, allow_transaction=True)[0]
        ):
            raise ValueError("Unidentifiable monitor observation reference remains pending")
        return False
    helper = runtime if runtime is not None else retirements._runtime()
    pending = False
    with tracking._RecordLock(directory / "inventory", require_sidecar=True):
        if cleanup:
            _recover_archives(root, directory, helper)
        archives = _archives(directory)
        known = {owner for path, record in archives for owner in _groups(record, path.stem)}
        if any(
            owner.startswith(_REFERENCE_PREFIX) and owner not in known
            for owner in helper._leases._read_runtime_reference_state(root, allow_transaction=True)[0]
        ):
            raise ValueError("Unidentifiable monitor observation reference remains pending")
        for path, record in archives:
            content = _content(record)
            digest = _digest(content)
            recovery = _record(content)
            identities = _identities(
                content["table"], recovery, content["lock"], content["predecessor"],
            )
            unreadable = content["breadcrumb"] is not None and recovery is None
            exited = bool(identities) and not unreadable
            pending = pending or unreadable
            for identity in identities:
                observed = breadcrumb.owner_exited({
                    "pid": identity["pid"], "owner_process_start_time": identity["start"],
                })
                exited = exited and observed is True
                pending = pending or (observed is not True and identity["pid"] != os.getpid())
            if not identities:
                pending = True
            claim = record.get("release_claim")
            if claim is not None and record["release_state"] == "prepared":
                pending = True
                continue
            if cleanup and exited:
                if claim is not None and record["release_state"] == "retiring":
                    retirements.release_exited_claim(root, claim, helper)
                record = {**record, "cleanup_complete": True}
                routing_persistence.write(path, record)
                _references(root, record, digest, helper, retain=False)
                path.unlink()
            elif cleanup:
                _references(root, record, digest, helper, retain=True)
    return pending
