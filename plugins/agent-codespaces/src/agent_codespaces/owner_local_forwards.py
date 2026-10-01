"""State helpers for Connection Owner local forwards."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .config import RUNTIME_DIR, ensure_runtime_dir

ACTIVE_LOCAL_FORWARDS_FILE = RUNTIME_DIR / "connection-owner.local-forwards.json"
_ACTIVE_TTL = 60.0


def _port_map(value: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if isinstance(value, dict):
        for host, venue in value.items():
            try:
                h, v = int(host), int(venue)
            except (TypeError, ValueError):
                continue
            if 0 < h < 65536 and 0 < v < 65536:
                out[str(h)] = v
    return out


def reuse_assigned_local_forwards(hold: Any, requested: dict[int, int] | None) -> dict[int, int] | None:
    """Under the Owner lock, turn repeated ``0:venue`` requests into live assigned ports."""
    requested_clean: dict[int, int] = {}
    for host, venue in (requested or {}).items():
        try:
            h, v = int(host), int(venue)
        except (TypeError, ValueError):
            continue
        if 0 <= h < 65536 and 0 < v < 65536:
            requested_clean[h] = v
    if not requested_clean or 0 not in requested_clean:
        return requested
    requested_hosts = {host for host in requested_clean if host != 0}
    assigned = _port_map(getattr(hold, "assigned_local_forwards", {}))
    reused = dict(requested_clean)
    for host, venue in assigned.items():
        h = int(host)
        if h not in requested_hosts and venue == requested_clean[0]:
            reused.pop(0, None)
            reused[h] = venue
            break
    return reused


def record_assigned_local_forward(
    codespace: str,
    *,
    requested_host_port: int,
    assigned_host_port: int,
    venue_port: int,
    ttl: float | None = None,
) -> bool:
    """Replace a pending ``0:venue`` local forward with its assigned host port.

    The Owner process owns the actual local bind. Once its local-forward channel
    is established, it records the concrete host port back into the durable hold
    so launchers and later rejoins observe the same stable endpoint.
    """
    if int(requested_host_port) != 0:
        return False
    assigned = int(assigned_host_port)
    venue = int(venue_port)
    if not (0 < assigned < 65536 and 0 < venue < 65536):
        return False
    from . import connection_owner as owner

    with owner._owner_lock():
        holds = owner._prune(owner._read_holds(), owner.DEFAULT_TTL if ttl is None else ttl)
        hold = holds.get(codespace)
        if hold is None:
            return False
        forwards = dict(getattr(hold, "local_forwards", None) or {})
        if forwards.get("0") != venue:
            return False
        current = forwards.get(str(assigned))
        if current is not None and current != venue:
            return False
        forwards.pop("0", None)
        forwards[str(assigned)] = venue
        hold.local_forwards = forwards
        hold.assigned_local_forwards[str(assigned)] = venue
        owner._write_holds(holds)
        return True


def reassign_dynamic_local_forward(
    codespace: str,
    *,
    old_host_port: int,
    assigned_host_port: int,
    venue_port: int,
    ttl: float | None = None,
) -> bool:
    old_host = int(old_host_port)
    assigned = int(assigned_host_port)
    venue = int(venue_port)
    if not (0 < old_host < 65536 and 0 < assigned < 65536 and 0 < venue < 65536):
        return False
    from . import connection_owner as owner

    with owner._owner_lock():
        holds = owner._prune(owner._read_holds(), owner.DEFAULT_TTL if ttl is None else ttl)
        hold = holds.get(codespace)
        if hold is None or hold.assigned_local_forwards.get(str(old_host)) != venue:
            return False
        hold.local_forwards.pop(str(old_host), None)
        hold.assigned_local_forwards.pop(str(old_host), None)
        hold.local_forwards[str(assigned)] = venue
        hold.assigned_local_forwards[str(assigned)] = venue
        owner._write_holds(holds)
        return True


def write_active_local_forwards(active: dict[str, dict[int, int]]) -> None:
    try:
        ensure_runtime_dir()
        payload = {
            "pid": os.getpid(),
            "heartbeat_at": time.time(),
            "local_forwards": {
                cs: {str(int(host)): int(venue) for host, venue in forwards.items()}
                for cs, forwards in active.items()
            },
        }
        tmp = ACTIVE_LOCAL_FORWARDS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, ACTIVE_LOCAL_FORWARDS_FILE)
    except Exception:
        pass


def read_active_local_forwards(*, now: float | None = None) -> dict[str, dict[int, int]]:
    try:
        raw = json.loads(ACTIVE_LOCAL_FORWARDS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    try:
        heartbeat = float(raw.get("heartbeat_at"))
    except (TypeError, ValueError):
        return {}
    if (time.time() if now is None else now) - heartbeat > _ACTIVE_TTL:
        return {}
    out: dict[str, dict[int, int]] = {}
    if isinstance(raw.get("local_forwards"), dict):
        for codespace, forwards in raw["local_forwards"].items():
            mapped = {int(host): venue for host, venue in _port_map(forwards).items()}
            if mapped:
                out[str(codespace)] = mapped
    return out


def clear_active_local_forwards() -> None:
    try:
        ACTIVE_LOCAL_FORWARDS_FILE.unlink(missing_ok=True)
    except Exception:
        pass
