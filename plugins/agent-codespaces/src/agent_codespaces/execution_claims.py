"""Durable interaction-mode reservations sharing the provider's claim lock."""

from __future__ import annotations

import json
import hashlib
import os
import re
import time
import uuid


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError("execution identity must be a bounded portable identifier")
    return value


def _path():
    from . import lease

    return lease.LEASE_FILE.with_name("execution-claims.json")


def _read() -> dict:
    path = _path()
    if not path.exists():
        if path.with_suffix(".initialized").exists():
            from .lease import CoordinationRejected
            raise CoordinationRejected("execution ownership store was lost; refusing an empty-venue assumption")
        return {}
    from .lease import CoordinationRejected

    try:
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise ValueError("unsafe execution claim store")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("claims"), dict):
            raise ValueError("invalid execution claim store")
        for name, row in data["claims"].items():
            if not isinstance(name, str) or not isinstance(row, dict):
                raise ValueError("invalid execution claim")
            if row.get("mode") not in {"native", "acp"} or not row.get("owner"):
                raise ValueError("invalid execution claim owner/mode")
            identifier(row["executionId"])
            identifier(row["generation"])
        return data["claims"]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise CoordinationRejected("execution ownership is unreadable; refusing to assume an empty venue") from exc


def _retirement_key(name: str, identity: tuple[str, str]) -> str:
    return hashlib.sha256(json.dumps([name, *identity]).encode()).hexdigest()


def _retired() -> set[str]:
    path = _path()
    if not path.exists():
        return set()
    try:
        values = json.loads(path.read_text(encoding="utf-8")).get("retired", [])
        if not isinstance(values, list) or any(not isinstance(item, str) or len(item) != 64 for item in values):
            raise ValueError("invalid retired execution ledger")
        return set(values)
    except (OSError, ValueError, TypeError) as exc:
        from .lease import CoordinationRejected
        raise CoordinationRejected("retired execution identity cannot be verified") from exc


def _receipts() -> dict:
    path = _path()
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8")).get("receipts", {})
    if not isinstance(value, dict):
        from .lease import CoordinationRejected
        raise CoordinationRejected("retirement receipts are unreadable")
    return value


def _write(
    rows: dict, *, retire: tuple[str, tuple[str, str]] | None = None,
    receipt_update: tuple[str, dict] | None = None,
) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".execution-claims-{uuid.uuid4().hex}.new")
    retired = _retired()
    receipts = _receipts()
    if retire:
        retired.add(_retirement_key(*retire))
    if receipt_update:
        receipts[receipt_update[0]] = receipt_update[1]
    try:
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "claims": rows, "retired": sorted(retired), "receipts": receipts}, stream)
        os.replace(staging, path)
        path.with_suffix(".initialized").touch(exist_ok=True)
    finally:
        staging.unlink(missing_ok=True)


def _conflict(name: str, row: dict):
    from .lease import ClaimConflict

    return ClaimConflict(name, f"{row['mode']} execution {row['executionId']}", "", 0)


def assert_access(name: str, identity: tuple[str, str] | None = None, owner: str | None = None) -> None:
    """Caller holds the lease lock; absence is allowed, ambiguity never is."""
    row = _read().get(name)
    if row is None:
        return
    if identity == (row["executionId"], row["generation"]) and owner == row["owner"]:
        return
    raise _conflict(name, row)


def get(name: str) -> dict | None:
    from .lease import _lease_lock

    with _lease_lock():
        return _read().get(name)


def reserve(name: str, owner: str, execution_id: str, generation: str, mode: str) -> bool:
    """Reserve before launch; no TTL can reinterpret a lost frontend as retirement."""
    from ssh_manager import TargetLock
    from . import lease

    identifier(execution_id)
    identifier(generation)
    if mode not in {"native", "acp"} or not isinstance(owner, str) or not owner.strip():
        raise ValueError("execution mode and owner are required")
    lock = TargetLock(name, op=f"{mode}-reservation")
    lock.acquire()
    try:
        with lease._lease_lock():
            rows = _read()
            if _retirement_key(name, (execution_id, generation)) in _retired():
                raise lease.CoordinationRejected("execution identity was retired; use a new launch request")
            prior = rows.get(name)
            if prior:
                assert_access(name, (execution_id, generation), owner)
                if prior["mode"] != mode:
                    raise _conflict(name, prior)
                return False
            held = lease._prune(lease._read_leases(), lease.DEFAULT_TTL).get(name)
            if held and lease._claim_owner(held) != owner:
                raise lease.ClaimConflict(name, lease._claim_owner(held), held.host, held.pid)
            rows[name] = {
                "mode": mode, "owner": owner, "executionId": execution_id,
                "generation": generation, "createdAt": time.time(),
            }
            if mode == "native":
                rows[name]["infrastructureStopped"] = True
            _write(rows)
            return True
    finally:
        lock.release()


def release(name: str, owner: str, identity: tuple[str, str], *, proof: dict | None = None) -> bool:
    """Retirement caller supplies the verified execution identity."""
    from .lease import _lease_lock

    with _lease_lock():
        rows = _read()
        if name not in rows:
            return False
        assert_access(name, identity, owner)
        receipt = None
        if rows[name]["mode"] == "native":
            if not proof or proof.get("retired") is not True:
                from .lease import CoordinationRejected
                raise CoordinationRejected("native release requires verified retirement")
            receipt = {
                "codespace": name, "owner": owner, "executionId": identity[0],
                "generation": identity[1], "retired": True,
                **{key: proof.get(key) for key in ("sessionId", "exitCode", "recovery", "noLaunch")},
            }
        del rows[name]
        _write(rows, retire=(name, identity),
               receipt_update=(_retirement_key(name, identity), receipt) if receipt else None)
        return True


def mark(name: str, owner: str, identity: tuple[str, str], **changes) -> None:
    from .lease import _lease_lock, CoordinationRejected

    with _lease_lock():
        rows = _read()
        if name not in rows:
            raise CoordinationRejected("native execution ownership disappeared")
        assert_access(name, identity, owner)
        rows[name].update(changes)
        _write(rows)


def abort_unlaunched(name: str, owner: str, identity: tuple[str, str]) -> bool:
    from ssh_manager import TargetLock
    from .lease import _lease_lock, CoordinationRejected

    lock = TargetLock(name, op="native-abort")
    lock.acquire()
    try:
        with _lease_lock():
            rows = _read()
            row = rows.get(name)
            if row is None:
                return False
            assert_access(name, identity, owner)
            if row.get("launchRequested") or row.get("infrastructureStopped") is not True:
                raise CoordinationRejected("native launch/cleanup is uncertain; retirement proof is required")
            del rows[name]
            _write(rows, retire=(name, identity), receipt_update=(
                _retirement_key(name, identity),
                {"codespace": name, "owner": owner, "executionId": identity[0],
                 "generation": identity[1], "retired": True, "noLaunch": True},
            ))
            return True
    finally:
        lock.release()


def retirement(name: str, owner: str, identity: tuple[str, str]) -> dict | None:
    from .lease import _lease_lock, CoordinationRejected

    with _lease_lock():
        _read()
        result = _receipts().get(_retirement_key(name, identity))
        if result is None:
            return None
        if (
            result.get("owner") != owner or result.get("codespace") != name
            or (result.get("executionId"), result.get("generation")) != identity
            or result.get("retired") is not True
        ):
            raise CoordinationRejected("retirement receipt identity does not match")
        return result


def update_recovery(name: str, owner: str, identity: tuple[str, str], recovery: dict) -> None:
    from .lease import _lease_lock, CoordinationRejected

    with _lease_lock():
        rows = _read()
        key = _retirement_key(name, identity)
        prior = _receipts().get(key)
        if not prior or prior.get("owner") != owner:
            raise CoordinationRejected("no owned retirement receipt for recovery")
        _write(rows, receipt_update=(key, {**prior, "recovery": recovery}))
