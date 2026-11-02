"""Resident-owned demand and durable code retention for displaced monitors."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

from zdd import diagnostics, routing

from . import locks, tracking

CAPABILITY = "status-monitor-async-retirement-v1"


@lru_cache(maxsize=1)
def _runtime():
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


def _validate(record, path: Path) -> None:
    if (
        not isinstance(record, dict)
        or set(record) != {"version", "pid", "start", "slot", "release_claim"}
        or record["version"] != 1 or type(record["version"]) is not int
        or type(record["pid"]) is not int or record["pid"] <= 0
        or not isinstance(record["start"], str) or not record["start"]
        or len(record["start"]) > 128
        or not re.fullmatch(r"[0-9]+(?::[0-9]+)*", record["start"])
        or record["slot"] is not None and (
            not isinstance(record["slot"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", record["slot"])
            or record["slot"].endswith(".")
        )
        or record["release_claim"] is not None and (
            record["slot"] != "dev" or not isinstance(record["release_claim"], dict)
            or not isinstance(record["release_claim"].get("owner"), str)
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
        records.append((path, record))
    return records


def capture(root: Path) -> dict | None:
    """Capture native identity without requiring predecessor control IPC."""
    record = locks.read_lock(root / "status-monitor.lock") or {}
    endpoint = routing.read_active_endpoint(root / "status-monitor-routing", verify_listener=False)
    pid = endpoint.pid if endpoint is not None else record.get("pid")
    if type(pid) is not int or pid <= 0 or not diagnostics._pid_alive(pid):
        return None
    start = locks.process_start_time(pid)
    if start is None:
        raise RuntimeError("Live monitor predecessor identity is unavailable")
    prefix = record.get("prefix") if record.get("pid") == pid else None
    slot = None
    if prefix:
        path = Path(prefix).resolve()
        if path.parent == (root / "versions").resolve():
            slot = path.name
    return {"pid": pid, "start": start, "slot": slot, "release_claim": None}


def reserve(root: Path, predecessor: dict | None, *, runtime=None) -> bool:
    """Publish deletion barriers before the installer releases its kernel lease."""
    if predecessor is None:
        return False
    directory = root / "status-monitor-retirements"
    directory.mkdir(exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Monitor retirement directory must not be linked")
    record = {"version": 1, **predecessor}
    owner = _owner(record["pid"], record["start"])
    path = directory / (owner.rsplit(":", 1)[1] + ".json")
    _validate(record, path)
    with tracking._RecordLock(directory / "inventory", require_sidecar=True):
        existing = dict(_records(root)).get(path)
        if existing is not None:
            if {**existing, "release_claim": record["release_claim"]} != record:
                raise RuntimeError("Monitor predecessor custody changed before reservation")
            if existing["release_claim"] is not None and existing != record:
                raise RuntimeError("Monitor dev release custody changed before reservation")
        if record["slot"] is not None:
            helper = runtime if runtime is not None else _runtime()
            references = helper._leases.read_runtime_references(root)
            previous = references.get(owner, [])
            if previous not in ([], [record["slot"]]):
                raise RuntimeError("Monitor retirement references changed before reservation")
            helper._leases.replace_runtime_references(
                root, owner, [record["slot"]], expected=previous,
            )
        tracking._atomic_write(path, json.dumps(record))
        _records(root)
    return True


def _exited(record: dict) -> bool:
    observed = locks.process_start_time(record["pid"])
    return observed != record["start"] if observed is not None else not diagnostics._pid_alive(record["pid"])


def reconcile(root: Path, *, runtime=None, cleanup: bool = True) -> bool:
    """Keep the elected successor needed; settle only positively exited identities."""
    records = _records(root)
    if not records:
        return False
    if not cleanup:
        return any(record["pid"] != os.getpid() and not _exited(record) for _path, record in records)
    pending = False
    directory = root / "status-monitor-retirements"
    with tracking._RecordLock(directory / "inventory", require_sidecar=True):
        for path, record in _records(root):
            pid, start = record["pid"], record["start"]
            if pid == os.getpid():
                continue
            if not _exited(record):
                pending = True
                continue
            if record["slot"] is not None:
                helper = runtime if runtime is not None else _runtime()
                claim = record["release_claim"]
                if claim is not None:
                    with helper._publication_lease(root):
                        current = helper.read_dev_claim(root, strict=True)
                        if current is not None:
                            if current != claim:
                                raise RuntimeError("Dev claim changed before resident-owned release")
                            helper._release_dev(root, claim["owner"])
                owner = _owner(pid, start)
                previous = helper._leases.read_runtime_references(root).get(owner, [])
                if previous:
                    if previous != [record["slot"]]:
                        raise RuntimeError("Monitor retirement references changed before release")
                    helper._leases.replace_runtime_references(root, owner, [], expected=previous)
            path.unlink()
    return pending


def pending_slot(root: Path, slot: str) -> bool:
    return any(record["slot"] == slot for _path, record in _records(root))


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
                logging.getLogger(__name__).error("Monitor retirement remains pending: %s", message)
            self.pending, self.error = True, message
        else:
            self.error = None

    def health(self) -> dict:
        return {"pending": self.pending, "error": self.error}
