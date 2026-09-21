"""Shared state helpers for agent-machines fleet-update scheduling.

Mirrors ``self_update_state.py``'s shape exactly, but for a distinct
resource concern: periodically invoking ``worktree-manager update`` (the
fleet-wide plugin install/update orchestrator) on a schedule, rather than
agent-machines' own self-update/mesh-health duties. Kept as an independent
module (own state directory, own mutex names, own single tier) rather than
extending ``self_update_state``'s ``TIER_SPECS`` so that a bug in one
subsystem's scheduling cannot affect the other's already-deployed, live
mechanism.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATE_VERSION = TASKS_VERSION = 1

#: The only fleet-update tier today: a daily sweep that runs
#: ``worktree-manager update``. Modeled as a dict (like self-update's
#: ``TIER_SPECS``) so a second tier (e.g. a faster "watchdog" cadence) can be
#: added later without reshaping callers.
SWEEP_TIER = "sweep"
SWEEP_STALE_SECONDS = 26 * 60 * 60  # a day, plus slack


@dataclass(frozen=True)
class TierSpec:
    tier: str
    stale_seconds: int
    task_name: str
    schedule_kind: str
    schedule_value: int


TIER_SPECS: dict[str, TierSpec] = {
    SWEEP_TIER: TierSpec(
        tier=SWEEP_TIER,
        stale_seconds=SWEEP_STALE_SECONDS,
        task_name="agent-machines-fleet-update-sweep",
        schedule_kind="daily",
        schedule_value=1,
    ),
}


@dataclass
class TierStatus:
    last_attempt: str | None = None
    last_success: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_attempt": self.last_attempt,
            "last_success": self.last_success,
        }


def state_root(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return base / ".agent-machines" / "fleet-update"


def status_path(home: Path | None = None) -> Path:
    return state_root(home) / "status.json"


def task_config_path(home: Path | None = None) -> Path:
    return state_root(home) / "tasks.json"


def lock_path(tier: str, home: Path | None = None) -> Path:
    return state_root(home) / f"{tier}.lock.json"


def mutex_name(tier: str) -> str:
    return f"Global\\AgentMachinesFleetUpdate_{tier}"


def load_status(home: Path | None = None) -> dict[str, Any]:
    path = status_path(home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": STATE_VERSION, "tiers": {}}
    if not isinstance(raw, dict):
        return {"schema_version": STATE_VERSION, "tiers": {}}
    tiers = raw.get("tiers")
    if not isinstance(tiers, dict):
        tiers = {}
    return {
        "schema_version": STATE_VERSION,
        "tiers": tiers,
    }


def tier_status(home: Path | None, tier: str) -> TierStatus:
    tiers = load_status(home).get("tiers", {})
    current = tiers.get(tier, {})
    if not isinstance(current, dict):
        current = {}
    return TierStatus(
        last_attempt=current.get("last_attempt"),
        last_success=current.get("last_success"),
    )


def observed_plan_fields(tier: str, home: Path | None = None) -> dict[str, Any]:
    status = tier_status(home, tier)
    return {
        "last_attempt": status.last_attempt,
        "last_success": status.last_success,
    }


def task_config(home: Path | None = None) -> dict[str, Any]:
    path = task_config_path(home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": TASKS_VERSION, "tiers": {}}
    if not isinstance(raw, dict):
        return {"schema_version": TASKS_VERSION, "tiers": {}}
    tiers = raw.get("tiers")
    if not isinstance(tiers, dict):
        tiers = {}
    return {
        "schema_version": TASKS_VERSION,
        "tiers": tiers,
    }


class _WindowsMutex:
    WAIT_OBJECT_0 = 0x00000000
    WAIT_ABANDONED = 0x00000080

    def __init__(self, name: str):
        self.handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        if not self.handle:
            raise OSError(f"CreateMutexW failed for {name}")
        self.acquired = False

    def try_acquire(self) -> str:
        result = ctypes.windll.kernel32.WaitForSingleObject(self.handle, 0)
        if result == self.WAIT_OBJECT_0:
            self.acquired = True
            return "acquired"
        if result == self.WAIT_ABANDONED:
            self.acquired = True
            return "abandoned"
        raise RuntimeError("could not acquire fleet-update state mutex")

    def close(self) -> None:
        if self.acquired:
            ctypes.windll.kernel32.ReleaseMutex(self.handle)
            self.acquired = False
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def _with_mutex(name: str):
    if sys.platform != "win32":
        return None
    mutex = _WindowsMutex(name)
    state = mutex.try_acquire()
    if state not in {"acquired", "abandoned"}:
        raise RuntimeError(f"could not acquire {name}")
    return mutex


def write_status(
    home: Path | None, tier: str, *, attempt: str | None = None, success: str | None = None
) -> TierStatus:
    mutex = _with_mutex("Global\\AgentMachinesFleetUpdateStatus")
    try:
        root = state_root(home)
        root.mkdir(parents=True, exist_ok=True)
        path = status_path(home)
        payload = load_status(home)
        tiers = payload.setdefault("tiers", {})
        current = tiers.setdefault(tier, {})
        if attempt is not None:
            current["last_attempt"] = attempt
        if success is not None:
            current["last_success"] = success
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
        return tier_status(home, tier)
    finally:
        if mutex is not None:
            mutex.close()


def record_task_config(
    home: Path | None,
    tier: str,
    *,
    installed: bool,
    opted_in: bool,
    attempted_elevation: bool,
) -> None:
    mutex = _with_mutex("Global\\AgentMachinesFleetUpdateTaskConfig")
    try:
        root = state_root(home)
        root.mkdir(parents=True, exist_ok=True)
        path = task_config_path(home)
        payload = task_config(home)
        tiers = payload.setdefault("tiers", {})
        tiers[tier] = {
            "installed": installed,
            "opted_in": opted_in,
            "attempted_elevation": attempted_elevation,
        }
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        if mutex is not None:
            mutex.close()


def selected_tiers_from_resolved(resolved_resources: list[Any]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for resource in resolved_resources:
        if getattr(resource, "type", "") != "fleet-update":
            continue
        selected[str(getattr(resource, "id", ""))] = resource
    return selected


def tier_enabled(resource: Any | None) -> bool:
    if resource is None:
        return False
    desired = getattr(resource, "desired", {}) or {}
    return str(desired.get("state", "present")) != "absent"
