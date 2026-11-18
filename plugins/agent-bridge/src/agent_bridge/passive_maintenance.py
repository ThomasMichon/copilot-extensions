"""Host admission for refreshing a passive frontend's inherited metadata."""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
from typing import Any

from single_instance_lease.supersession import pid_alive
from zdd import process_exit
from zdd.claims import ClaimConflict

from .db import Database
from .session_host.host_index import HostIndex, HostRecord
from .session_status import SessionStatus


def may_refresh_inherited(
    db: Database, index: HostIndex, generation: str, row: dict[str, Any], on_acquired: Callable[[HostRecord], None],
) -> bool:
    """Admit metadata, not a launch. Return newly acquired claims via the callback."""
    status = row["status"]
    if status in {SessionStatus.ENDED.value, SessionStatus.FAILED.value}:
        return True
    sid = row["id"]
    dormant = status == SessionStatus.STOPPED.value and (
        row["runtime_custody_json"] is not None or not row["background_recovery_enabled"]
    )
    if row["cold_recovery_stop_token"] is not None or row["runtime_custody_json"] is not None:
        return dormant
    record = index.get(sid)
    if dormant and (record is None or not db.has_stopped_host_receipt(row, record)):
        return True
    if record is None or db.retained_recovery_held(sid):
        return dormant
    try:
        if record.boundary == "local":
            exited = []
            for name in ("host", "child"):
                birth = record.extra.get(f"{name}_process_start")
                if not isinstance(birth, str) or not birth:
                    return False
                exited.append(process_exit.observe_original_exit(getattr(record, f"{name}_pid"), birth))
            if any(exited):
                if not all(exited):
                    return False
                removed = index.release_exited_local(
                    record, inherited_by=generation, settle=lambda locked: db.settle_exited_inherited_host(row, locked),
                )
                # First settlement changes RUNNING/IDLE to STOPPED: reread before publishing.
                return dormant and removed
        if dormant:
            return False
        index.claim(sid, generation=generation, owner_pid=os.getpid(), pid_alive=pid_alive,
                    expected=record, on_acquired=on_acquired)
    except (ClaimConflict, KeyError):
        return False
    except (OSError, ValueError, NotImplementedError) as exc:
        logging.getLogger("agent-bridge").warning("Deferring inherited session %s: %s", sid, exc)
        return False
    return True
