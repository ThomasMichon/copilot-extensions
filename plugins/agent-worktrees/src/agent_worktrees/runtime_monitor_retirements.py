"""Resident-owned demand and durable code retention for displaced monitors."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import sys
import tempfile
from contextlib import nullcontext
from functools import lru_cache
from pathlib import Path
from types import ModuleType

from zdd import diagnostics, routing

from . import locks, tracking

CAPABILITY = "status-monitor-async-retirement-v1"


@lru_cache(maxsize=1)
def _runtime() -> ModuleType:
    helper = Path(sys.prefix) / ".runtime-bootstrap" / "versioned_runtime.py"
    if helper.parent.is_symlink() or helper.is_symlink() or not helper.is_file():
        raise RuntimeError("Monitor retirement requires its retained runtime helpers")
    spec = importlib.util.spec_from_file_location("_monitor_retirement_runtime", helper)
    if spec is None or spec.loader is None:
        raise ImportError("Monitor retirement runtime helpers are unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _owner(pid: int, start: str) -> str:
    digest = hashlib.sha256(f"{pid}:{start}".encode()).hexdigest()
    return f"worktrees-monitor-retirement:{digest}"


def _validate(record: dict, path: Path) -> None:
    if (
        not isinstance(record, dict)
        or set(record)
        not in (
            {"version", "pid", "start", "slot", "release_claim"},
            {"version", "pid", "start", "slot", "release_claim", "state"},
        )
        or record.get("state", "prepared") not in ("prepared", "retiring", "cancelled")
        or record["version"] != 1
        or type(record["version"]) is not int
        or type(record["pid"]) is not int
        or record["pid"] <= 0
        or not isinstance(record["start"], str)
        or not record["start"]
        or len(record["start"]) > 128
        or not re.fullmatch(r"[0-9]+(?::[0-9]+)*", record["start"])
        or (
            record["slot"] is not None
            and (
                not isinstance(record["slot"], str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", record["slot"])
                or record["slot"].endswith(".")
            )
        )
        or (
            record["release_claim"] is not None
            and (
                record["slot"] != "dev"
                or not isinstance(record["release_claim"], dict)
                or not isinstance(record["release_claim"].get("owner"), str)
            )
        )
        or path.stem != _owner(record["pid"], record["start"]).rsplit(":", 1)[1]
    ):
        raise ValueError("Invalid monitor retirement record")


def _records(root: Path) -> list[tuple[Path, dict]]:
    directory = root / "status-monitor-retirements"
    if directory.is_symlink():
        raise ValueError("Monitor retirement directory must not be linked")
    if not directory.exists():
        return []
    paths = sorted(directory.glob("*.json"))
    if len(paths) > 256:
        raise ValueError("Monitor retirement inventory exceeds its bound")
    records = []
    for path in paths:
        if path.is_symlink() or path.stat().st_size > 32768:
            raise ValueError("Monitor retirement record is not a bounded ordinary file")
        record = json.loads(path.read_text(encoding="utf-8"))
        _validate(record, path)
        record.setdefault("state", "prepared")
        records.append((path, record))
    return records


def capture(root: Path, *, identity: tuple[int, str, str | None] | None = None) -> dict | None:
    """Use authenticated routed runtime identity or a caller-qualified snapshot."""
    from .status_monitor_cutover import routed_monitor_identity

    record = locks.read_lock(root / "status-monitor.lock") or {}
    endpoint = routing.read_active_endpoint(root / "status-monitor-routing", verify_listener=False)
    if identity is None and endpoint is not None:
        identity = routed_monitor_identity(root)
        if identity is None:
            return None
        if identity[2] is None:
            prefix = record.get("prefix")
            if (
                record.get("pid") != identity[0]
                or record.get("start_time") != identity[1]
                or not isinstance(prefix, str)
                or not prefix
                or Path(prefix).resolve().parent == (root / "versions").resolve()
            ):
                raise RuntimeError("Routed monitor runtime retention is unconfirmed")
    pid = identity[0] if identity is not None else record.get("pid")
    if type(pid) is not int or pid <= 0 or not diagnostics._pid_alive(pid):
        return None
    start = locks.process_start_time(pid)
    if start is None:
        raise RuntimeError("Live monitor predecessor identity is unavailable")
    if identity is not None:
        if start != identity[1]:
            raise RuntimeError("Monitor predecessor identity changed during capture")
        slot = identity[2]
    else:
        prefix = record.get("prefix")
        if record.get("start_time") != start or not isinstance(prefix, str) or not prefix:
            raise RuntimeError("Monitor predecessor runtime retention is unconfirmed")
        path = Path(prefix).resolve()
        slot = path.name if path.parent == (root / "versions").resolve() else None
    return {"pid": pid, "start": start, "slot": slot, "release_claim": None}


def _write_record(path: Path, record: dict) -> None:
    """Flush recoverable intent before publishing the canonical deletion barrier."""
    content = json.dumps(record)
    if len(content.encode("utf-8")) > 32768:
        raise ValueError("Monitor retirement record exceeds its bound")
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        tracking._replace_with_retry(temporary, str(path))
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _retain(root: Path, record: dict, helper: ModuleType) -> None:
    owner = _owner(record["pid"], record["start"])
    previous = helper._leases.read_runtime_references(root).get(owner, [])
    if previous not in ([], [record["slot"]]):
        raise RuntimeError("Monitor retirement references changed before reservation")
    if not previous:
        helper._leases.replace_runtime_references(root, owner, [record["slot"]], expected=[])


def reserve(
    root: Path,
    predecessor: dict | None,
    *,
    runtime: ModuleType | None = None,
    committed: bool = False,
) -> bool:
    """Publish recoverable intent and barriers before caller custody is released.

    An exception does not transfer custody: the caller must retain its existing
    installation/retention ownership until retry or proven rollback succeeds.
    Only explicit committed publication grants deferred native-exit cleanup.
    A namespace-serialized retry may replace cancelled custody with prepared
    intent for the same native identity and slot, never reuse its old grant.
    """
    if predecessor is None:
        return False
    if type(committed) is not bool:
        raise ValueError("Monitor retirement commitment must be explicit")
    directory = root / "status-monitor-retirements"
    directory.mkdir(exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Monitor retirement directory must not be linked")
    record = {"version": 1, **predecessor, "state": "retiring" if committed else "prepared"}
    owner = _owner(record["pid"], record["start"])
    path = directory / (owner.rsplit(":", 1)[1] + ".json")
    _validate(record, path)
    with tracking._RecordLock(directory / "inventory", require_sidecar=True):
        records = dict(_records(root))
        existing = records.get(path)
        if existing is None and len(records) >= 256:
            raise ValueError("Monitor retirement inventory exceeds its bound")
        if existing is not None:
            if existing["state"] == "cancelled":
                if committed:
                    raise RuntimeError("Cancelled monitor retirement requires a prepared retry")
            proposed = {**record, "state": existing["state"]}
            if {**existing, "release_claim": record["release_claim"]} != proposed:
                raise RuntimeError("Monitor predecessor custody changed before reservation")
            if (
                existing["state"] != "cancelled"
                and (existing["release_claim"] is not None or existing["state"] == "retiring")
            ) and existing != proposed:
                raise RuntimeError("Monitor dev release custody changed before reservation")
            if existing["state"] == "retiring":
                record["state"] = "retiring"
        helper = (
            (runtime if runtime is not None else _runtime())
            if record["slot"] is not None
            else None
        )
        custody = (
            helper._kernel_lease(root, record["slot"], kind="retention", shared=True)
            if helper is not None
            else nullcontext()
        )
        with custody:
            if helper is not None:
                previous = helper._leases.read_runtime_references(root).get(owner, [])
                if previous not in ([], [record["slot"]]):
                    raise RuntimeError("Monitor retirement references changed before reservation")
            _write_record(path, record)
            if helper is not None:
                _retain(root, record, helper)
    return True


def _completed_commit(root: Path, record: dict, runtime: ModuleType | None) -> bool:
    if _exit_state(record) is not True:
        raise RuntimeError("Monitor retirement reservation is missing before confirmed exit")
    helper = runtime if runtime is not None else _runtime()
    with helper._publication_lease(root):
        owner = _owner(record["pid"], record["start"])
        if helper._leases.read_runtime_references(root).get(owner, []):
            raise RuntimeError("Missing monitor retirement still owns runtime references")
        if record["release_claim"] is not None and (
            helper.current_version(root) == "dev"
            or helper.read_dev_claim(root, strict=True) is not None
        ):
            raise RuntimeError("Missing monitor retirement dev grant is not settled")
        if _exit_state(record) is not True:
            raise RuntimeError("Monitor retirement native exit changed during completion check")
    return False


def commit(root: Path, predecessor: dict, *, runtime: ModuleType | None = None) -> bool:
    """After parent-proven publication, report durable retiring or completed custody.

    True requires an exact retiring record; False requires positive native exit
    and settled original references and deferred claim, never inferred grants.
    """
    record = {"version": 1, **predecessor, "state": "prepared"}
    directory = root / "status-monitor-retirements"
    path = directory / (_owner(record["pid"], record["start"]).rsplit(":", 1)[1] + ".json")
    _validate(record, path)
    if not directory.exists():
        return _completed_commit(root, record, runtime)
    with tracking._RecordLock(directory / "inventory", require_sidecar=True):
        existing = dict(_records(root)).get(path)
        if existing is None:
            return _completed_commit(root, record, runtime)
        if {**existing, "state": "prepared"} != record:
            raise RuntimeError("Monitor retirement custody changed before commitment")
        if existing["state"] == "cancelled":
            raise RuntimeError("Cancelled monitor retirement cannot be committed")
        if record["slot"] is not None and (
            existing["state"] != "retiring" or _exit_state(record) is not True
        ):
            _retain(root, record, runtime if runtime is not None else _runtime())
        if dict(_records(root)).get(path) != existing:
            raise RuntimeError("Monitor retirement custody changed during commitment")
        if existing["state"] != "retiring":
            _write_record(path, {**existing, "state": "retiring"})
    return True


def _exit_state(record: dict) -> bool | None:
    observed = locks.process_start_time(record["pid"])
    if observed is not None and observed != record["start"]:
        return True
    alive = diagnostics._pid_alive(record["pid"])
    if observed is None and alive:
        return None
    return not alive


def _exited(record: dict) -> bool:
    exited = _exit_state(record)
    if exited is None:
        raise RuntimeError("Monitor predecessor native identity is unavailable")
    return exited


def _self(record: dict) -> bool:
    return (
        record["pid"] == os.getpid()
        and locks.process_start_time(record["pid"]) == record["start"]
        and diagnostics._pid_alive(record["pid"])
    )


def _demand(record: dict, exited: bool | None) -> bool:
    if exited is None:
        raise RuntimeError("Monitor predecessor native identity is unavailable")
    return record["state"] == "prepared" or (not exited and record["pid"] != os.getpid())


def _remove_reference(root: Path, record: dict, helper: ModuleType) -> None:
    owner = _owner(record["pid"], record["start"])
    previous = helper._leases.read_runtime_references(root).get(owner, [])
    if previous not in ([], [record["slot"]]):
        raise RuntimeError("Monitor retirement references changed before release")
    if previous:
        helper._leases.replace_runtime_references(root, owner, [], expected=previous)


def reconcile(root: Path, *, runtime: ModuleType | None = None, cleanup: bool = True) -> bool:
    """Keep the elected successor needed; settle only positively exited identities."""
    records = _records(root)
    if not records:
        return False
    if not cleanup:
        pending = False
        for _path, record in records:
            if _demand(record, _exit_state(record)):
                pending = True
        return pending
    pending = False
    directory = root / "status-monitor-retirements"
    with tracking._RecordLock(directory / "inventory", require_sidecar=True):
        for path, record in _records(root):
            exited = _exit_state(record)
            if record["state"] == "prepared" or exited is not True:
                if record["slot"] is not None:
                    _retain(root, record, runtime if runtime is not None else _runtime())
                pending = _demand(record, exited) or pending
                continue
            if record["slot"] is not None:
                helper = runtime if runtime is not None else _runtime()
                claim = record["release_claim"] if record["state"] == "retiring" else None
                if claim is not None:
                    with helper._publication_lease(root):
                        if helper.current_version(root) == "dev":
                            raise RuntimeError(
                                "Dev runtime is selected before resident-owned release"
                            )
                        current = helper.read_dev_claim(root, strict=True)
                        if current is not None:
                            if current != claim:
                                raise RuntimeError(
                                    "Dev claim changed before resident-owned release"
                                )
                            helper._release_dev(root, claim["owner"])
                _remove_reference(root, record, helper)
            path.unlink()
    return pending


def pending_slot(root: Path, slot: str, *, include_cancelled: bool = True) -> bool:
    """Protect retained code by default; new releases may exclude revoked grants."""
    if type(include_cancelled) is not bool:
        raise ValueError("Cancelled retirement visibility must be explicit")
    return any(
        record["slot"] == slot and (include_cancelled or record["state"] != "cancelled")
        for _path, record in _records(root)
    )


def pending_claim(root: Path, claim: dict) -> bool:
    """Observe exact deferred custody without releasing or repairing anything."""
    return any(
        record["state"] != "cancelled" and record["release_claim"] == claim
        for _path, record in _records(root)
    )


def cancel(root: Path, predecessor: dict, *, runtime: ModuleType | None = None) -> bool:
    """Cancel exact custody after the parent has proven precommit rollback."""
    record = {"version": 1, **predecessor, "state": "prepared"}
    directory = root / "status-monitor-retirements"
    owner = _owner(record["pid"], record["start"])
    path = directory / (owner.rsplit(":", 1)[1] + ".json")
    _validate(record, path)
    if not directory.exists():
        return False
    with tracking._RecordLock(directory / "inventory", require_sidecar=True):
        existing = dict(_records(root)).get(path)
        if existing is None:
            return False
        if {**existing, "state": "prepared"} != record:
            raise RuntimeError("Monitor retirement custody changed before cancellation")
        if existing["state"] == "retiring":
            raise RuntimeError("Committed monitor retirement cannot be cancelled")
        record["state"] = "cancelled"
        if existing["state"] != "cancelled":
            _write_record(path, record)
        if record["slot"] is not None:
            helper = runtime if runtime is not None else _runtime()
            previous = helper._leases.read_runtime_references(root).get(owner, [])
            if previous not in ([], [record["slot"]]):
                raise RuntimeError("Monitor retirement references changed before cancellation")
        exited = _exit_state(record)
        if exited is not True:
            if record["slot"] is not None:
                _retain(root, record, helper)
            if dict(_records(root)).get(path) != record:
                raise RuntimeError("Monitor retirement custody changed during cancellation")
            _demand(record, exited)
            return True
        if record["slot"] is not None:
            _remove_reference(root, record, helper)
        if dict(_records(root)).get(path) != record:
            raise RuntimeError("Monitor retirement custody changed during cancellation")
        path.unlink()
    return True


class ResidentRetirements:
    def __init__(self, root: Path):
        self.root = root
        self.pending = False
        self.error: str | None = None

    def refresh(self, *, cleanup: bool = True) -> None:
        try:
            self.pending = reconcile(self.root, cleanup=cleanup)
        except (OSError, ValueError, RuntimeError, ImportError) as error:
            message = str(error)
            if message != self.error:
                logging.getLogger(__name__).error(
                    "Monitor retirement remains pending: %s", message
                )
            self.pending, self.error = True, message
        else:
            self.error = None

    def health(self) -> dict:
        return {"pending": self.pending, "error": self.error}
