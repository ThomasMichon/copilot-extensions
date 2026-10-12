"""Let an identity-confirmed native unit own its supervisor's version restart."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .supervisor_lifecycle import daemon_audit
if TYPE_CHECKING:
    from .supervisor_daemon import SupervisorDaemon


class NativeOwnershipUnconfirmed(RuntimeError):
    pass


def native_restart_unit() -> str | None:
    if sys.platform != "linux":
        return None
    unit = os.environ.get("AGENT_DISPATCH_SUPERVISOR_SYSTEMD_UNIT", "")
    if not re.fullmatch(r"agent-dispatch-supervisor(?:-[A-Za-z0-9_-]+)?\.service", unit):
        return None
    if os.environ.get("AGENT_DISPATCH_SUPERVISOR_MAIN_PID") != str(os.getpid()):
        raise NativeOwnershipUnconfirmed("native main-PID metadata does not match")
    try:
        lines = Path("/proc/self/cgroup").read_text().splitlines()
    except OSError as exc:
        raise NativeOwnershipUnconfirmed("native cgroup identity unavailable") from exc
    if not any(line.rsplit("/", 1)[-1] == unit for line in lines):
        raise NativeOwnershipUnconfirmed("native cgroup identity does not match")
    return unit


def request_native_restart(daemon: SupervisorDaemon, unit: str) -> None:
    daemon._self_update_native_unit = unit
    daemon_audit(daemon, "handoff", "native-restart-requested")
