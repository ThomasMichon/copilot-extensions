"""Durable native ownership under the existing container admission lock."""

from __future__ import annotations

import hashlib
import json

from . import lease
from .private_state import atomic_write_json


def _path():
    return lease.LEASE_FILE.with_name("native-executions.json")


def _read():
    path = _path()
    if not path.exists():
        if path.with_suffix(".initialized").exists():
            raise lease.ProviderAdmissionError("native container ownership store was lost")
        return {"version": 1, "active": {}, "retired": {}}
    try:
        if path.is_symlink() or path.stat().st_size > 1048576:
            raise ValueError("unsafe native state")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value["version"] != 1 or not isinstance(value["active"], dict) or not isinstance(value["retired"], dict):
            raise ValueError("invalid native state")
        return value
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise lease.ProviderAdmissionError("native container ownership cannot be verified") from exc


def _write(value):
    atomic_write_json(_path(), value)
    _path().with_suffix(".initialized").touch(exist_ok=True)


def _key(name, identity):
    return hashlib.sha256(json.dumps([name, *identity]).encode()).hexdigest()


def assert_access(name, identity=None):
    """Caller holds the provider admission lock."""
    row = _read()["active"].get(name)
    if row and identity != (row["executionId"], row["generation"], row["owner"]):
        raise lease.ProviderAdmissionError("native execution still owns this container")
    return row


def reserve(name, identity, container_id):
    with lease._lease_lock():
        value = _read()
        old = assert_access(name, identity)
        if _key(name, identity) in value["retired"]:
            raise lease.ProviderAdmissionError("native execution identity has already retired")
        if old:
            if old["containerId"] != container_id:
                raise lease.ProviderAdmissionError("native container incarnation changed")
            return
        holds = lease._read_live_records(lease._DEPLOY_HOLDS_FILE, lease.DeployHold, lease.DEPLOY_HOLD_TTL)
        sessions = lease._read_live_records(
            lease._SESSION_ADMISSIONS_FILE, lease.SessionAdmission, lease.SESSION_ADMISSION_TTL,
        )
        if name in holds or any(item.container == name for item in sessions.values()):
            raise lease.ProviderAdmissionError("container lifecycle/session admission is busy")
        held = lease._prune(lease._read_leases(), lease.DEFAULT_TTL).get(name)
        if held and held.effort != identity[2]:
            raise lease.ProviderAdmissionError("container lease belongs to another owner")
        value["active"][name] = {
            "executionId": identity[0], "generation": identity[1], "owner": identity[2],
            "containerId": container_id, "launchRequested": False, "infrastructureStopped": True,
        }
        _write(value)


def require_owner(name, identity):
    with lease._lease_lock():
        row = assert_access(name, identity)
        if not row:
            raise lease.ProviderAdmissionError("native container reservation is unavailable")
        return dict(row)


def mark_launch(name, identity):
    with lease._lease_lock():
        value = _read()
        row = assert_access(name, identity)
        if not row:
            raise lease.ProviderAdmissionError("native container reservation is unavailable")
        value["active"][name]["launchRequested"] = True
        _write(value)


def infrastructure(name, identity, *, stopped):
    with lease._lease_lock():
        value = _read()
        row = assert_access(name, identity)
        if row:
            value["active"][name]["infrastructureStopped"] = stopped
            _write(value)


def retire(name, identity, proof=None):
    with lease._lease_lock():
        value = _read()
        row = assert_access(name, identity)
        if not row:
            return
        if proof is None:
            if row["launchRequested"] or row.get("infrastructureStopped") is not True:
                raise lease.ProviderAdmissionError("remote retirement or infrastructure cleanup proof is required")
            proof = {"executionId": identity[0], "generation": identity[1], "retired": True, "noLaunch": True}
        if proof.get("retired") is not True:
            raise lease.ProviderAdmissionError("remote retirement is not confirmed")
        value["retired"][_key(name, identity)] = {
            **proof, "owner": identity[2], "container": name, "containerId": row["containerId"],
        }
        del value["active"][name]
        _write(value)


def retirement(name, identity):
    with lease._lease_lock():
        return _read()["retired"].get(_key(name, identity))
