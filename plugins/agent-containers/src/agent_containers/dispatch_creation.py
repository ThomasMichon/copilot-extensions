"""Original creation association on the provider's existing dispatch binding.

Internal storage only. No transport, guest/native admission or slot retention
is granted by these observations.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from .private_state import atomic_write_json


def _text(value: object) -> None:
    if (
        not isinstance(value, str) or not value or len(value) > 1024
        or any(ord(char) < 33 or ord(char) > 126 for char in value)
    ):
        raise RuntimeError("Invalid retained provider creation identity")


def _digest(value: object, length: int = 64) -> None:
    if not isinstance(value, str) or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
        raise RuntimeError("Invalid retained provider creation digest")


def _association(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "admission", "handoff_id", "key_digest", "facts_digest",
        "session_id", "consumer_incarnation", "runtime",
    }:
        raise RuntimeError("Unsupported retained provider creation association")
    admission, runtime = value["admission"], value["runtime"]
    if (
        not isinstance(admission, dict) or set(admission) != {"installation", "epoch", "producer"}
        or type(admission["epoch"]) is not int or admission["epoch"] < 1
        or not isinstance(runtime, dict)
        or set(runtime) != {"root", "version", "payload_hash", "incarnation"}
    ):
        raise RuntimeError("Invalid retained provider creation owners")
    for item in (
        admission["installation"], admission["producer"], value["session_id"],
        value["consumer_incarnation"], runtime["incarnation"],
    ):
        _text(item)
    root, version = runtime["root"], runtime["version"]
    if (
        not isinstance(root, str) or not Path(root).is_absolute() or ".." in Path(root).parts
        or not isinstance(version, str) or version.casefold() == "dev"
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version) is None
    ):
        raise RuntimeError("Retained provider creation requires an immutable runtime identity")
    _digest(value["handoff_id"], 32)
    for digest in (value["key_digest"], value["facts_digest"], runtime["payload_hash"]):
        _digest(digest)
    return value


def validate(value: object, binding: dict) -> None:
    """Validate version-2 binding storage without upgrading a legacy birth."""
    if binding["worker_admission_generation"] != 0 or any(
        binding[key] is not None
        for key in ("owner_session_id", "expires_at", "attempted_expires_at")
    ):
        raise RuntimeError("Retained provider creation cannot inherit an ordinary launch")
    if value is None:
        return
    if (
        not isinstance(value, dict) or set(value) != {"association", "closed", "started_at"}
        or type(value["closed"]) is not bool or binding["task_id"] is None
    ):
        raise RuntimeError("Invalid retained provider creation observations")
    _association(value["association"])
    if value["started_at"] is not None:
        _time(value["started_at"])


def _time(value: object) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise RuntimeError("Invalid retained provider creation observation time")


def _binding(container_id: str, binding_id: str) -> dict:
    from . import dispatch_projection as projection

    binding = projection._read(container_id)
    if (
        binding is None or binding["binding_id"] != binding_id or binding["task_id"] is None
        or binding["version"] != 2
    ):
        raise RuntimeError("Retained creation requires its explicit provider birth and binding")
    return binding


def _original(binding: dict, association: dict) -> dict:
    value = binding["creation"]
    if value is None or value["association"] != association:
        raise RuntimeError("Retained provider creation cannot replace its original association")
    return value


def retain(name: str, container_id: str, binding_id: str, association: dict) -> dict:
    """Retain an original association before effects; replay is observational."""
    from . import dispatch_projection as projection, lease, lifecycle
    from .config import DISPATCH_BINDING_LABEL, RESTRICTED_PROFILE
    from .resolver import resolve_live_exec_target

    _association(association)
    with lease._lease_lock():
        binding = _binding(container_id, binding_id)
        if binding["creation"] is not None:
            return _original(binding, association)
        target = resolve_live_exec_target(name, dispatch_management=True)
        inspected = lifecycle.inspect_container(container_id)
        if (
            target.container_id != container_id or inspected.get("Id") != container_id
            or target.actual_profile != RESTRICTED_PROFILE or not target.fleet.dispatch_only
            or ((inspected.get("Config") or {}).get("Labels") or {}).get(
                DISPATCH_BINDING_LABEL
            ) != binding_id
        ):
            raise RuntimeError("Provider creation physical birth changed before retention")
        holds = lease._read_live_records(
            lease._DEPLOY_HOLDS_FILE, lease.DeployHold, lease.DEPLOY_HOLD_TTL,
        )
        admissions = lease._read_live_records(
            lease._SESSION_ADMISSIONS_FILE, lease.SessionAdmission, lease.SESSION_ADMISSION_TTL,
        )
        if name in holds or any(item.container == name for item in admissions.values()):
            raise RuntimeError("Provider creation is busy before retention")
        binding["creation"] = {"association": association, "closed": False, "started_at": None}
        atomic_write_json(projection._path(container_id), binding)
        return binding["creation"]


def read(container_id: str, binding_id: str, association: dict) -> dict:
    """Recover the same observations; process or TTL loss cannot settle them."""
    from . import lease

    _association(association)
    with lease._lease_lock():
        return _original(_binding(container_id, binding_id), association)


def begin(container_id: str, binding_id: str, association: dict, *, now: float) -> bool:
    """Persist one outstanding attempt, not permission to launch a provider."""
    from . import dispatch_projection as projection, lease

    _association(association)
    _time(now)
    with lease._lease_lock():
        binding = _binding(container_id, binding_id)
        value = _original(binding, association)
        if value["closed"]:
            raise RuntimeError("Retained provider creation admission is closed")
        if value["started_at"] is not None:
            return False
        value["started_at"] = now
        atomic_write_json(projection._path(container_id), binding)
        return True


def close(container_id: str, binding_id: str, association: dict) -> dict:
    """Close future attempts without clearing an admitted or unknown operation."""
    from . import dispatch_projection as projection, lease

    _association(association)
    with lease._lease_lock():
        binding = _binding(container_id, binding_id)
        value = _original(binding, association)
        if not value["closed"]:
            value["closed"] = True
            atomic_write_json(projection._path(container_id), binding)
        return value
