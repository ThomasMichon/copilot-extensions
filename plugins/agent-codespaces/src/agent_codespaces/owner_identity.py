"""OS-derived birth identity for the Connection Owner's liveness beacon.

A fresh beacon alone does not prove its writer is still running: on Windows
the Owner never probes ``pid`` (``os.kill`` would terminate it), and on any OS
the pid can be reused. The beacon records the writer's birth identity (process
creation time from the OS), and readers trust it only while the process now at
``pid`` has that same identity.
"""

from __future__ import annotations

from typing import Any


def beacon_identity(raw: dict) -> str | None:
    """The writer identity recorded in a raw beacon payload, if any."""
    value = raw.get("process_identity")
    return value if isinstance(value, str) and value else None


def owner_process_identity(pid: int) -> str | None:
    """The OS birth identity of ``pid`` (None when it can't be determined)."""
    try:
        from ssh_manager.locks import process_identity

        return process_identity(pid)
    except Exception:
        return None


def _pid_gone(pid: int) -> bool:
    try:
        from ssh_manager.locks import pid_alive

        return not pid_alive(pid)
    except Exception:
        return False


def owner_identity_matches(live: Any, this_host: str) -> bool:
    """Whether the process at ``live.pid`` is still the beacon's writer.

    A beacon without an identity (written by an older Owner) or from another
    host can't be checked and keeps the previous freshness-only behavior. A
    dead pid, or a live one with a different birth identity (pid reuse), is not
    the writer. A live pid whose identity can't be read is not disproven.
    """
    recorded = getattr(live, "process_identity", None)
    if not recorded or getattr(live, "host", "") != this_host:
        return True
    current = owner_process_identity(live.pid)
    if current is None:
        return not _pid_gone(live.pid)
    return current == recorded
