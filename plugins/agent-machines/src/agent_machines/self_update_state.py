"""Shared state helpers for agent-machines self-update."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATE_VERSION = TASKS_VERSION = 1


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
    return base / ".agent-machines" / "self-update"


def status_path(home: Path | None = None) -> Path:
    return state_root(home) / "status.json"


def task_config_path(home: Path | None = None) -> Path:
    return state_root(home) / "tasks.json"


def lock_path(tier: str, home: Path | None = None) -> Path:
    return state_root(home) / f"{tier}.lock.json"


def mutex_name(tier: str) -> str:
    return f"Global\\AgentMachinesSelfUpdate_{tier}"


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


def selected_tiers_from_resolved(resolved_resources: list[Any]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for resource in resolved_resources:
        if getattr(resource, "type", "") != "self-update":
            continue
        selected[str(getattr(resource, "id", ""))] = resource
    return selected


def tier_enabled(resource: Any | None) -> bool:
    if resource is None:
        return False
    desired = getattr(resource, "desired", {}) or {}
    return str(desired.get("state", "present")) != "absent"
