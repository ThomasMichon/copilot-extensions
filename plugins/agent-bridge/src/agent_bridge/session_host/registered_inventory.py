"""Observe standalone Core Hosts through their existing daemon registrations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent_procutil.daemon_instances import REGISTRY_ENV
from agent_procutil.daemon_observation import observe_instances

from agent_remote_driver.session_host.osutil import process_start_time

_SERVICE = "agent-bridge-session-host"
_MAX_STATE_BYTES = 64 * 1024
_LOCATOR = "observation_session_host_state_path"


def _target(
    pid: int, instance: str, sid: str | None, birth: str | None, cwd: str | None,
) -> dict[str, Any]:
    return {
        "kind": "session_host", "handle": sid or "host-instance:" + instance,
        "sources": ["daemon-instance"],
        "registration": {
            "session_id": sid, "instance_id": instance, "host_pid": pid,
            "host_process_start_time": birth, "cwd": cwd,
            "boundary": "local", "liveness": "observed", "controller": "original_owner",
        },
    }


def registered_hosts() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read birth-matched Host metadata without attachment, claims or cleanup."""
    root = os.environ.get(REGISTRY_ENV)
    try:
        records = observe_instances(Path(root) if root else None, service=_SERVICE)
    except (OSError, ValueError, TypeError):
        return [], {"availability": "unavailable", "reason": "instance_observation_unavailable"}
    by_root: dict[Path, dict[int, str]] = {}
    by_file: dict[Path, dict[int, str]] = {}
    admitted: dict[int, str] = {}
    incomplete = False
    for record in records:
        if record.get("reason") == "invalid-record":
            incomplete = True
            continue
        if record.get("purpose") != "production":
            continue
        if record.get("state") == "finished":
            continue
        if record.get("state") != "running":
            incomplete = True
            continue
        identity = record.get("process_identity_state")
        if identity in {"dead", "reused"}:
            continue
        if identity != "matching":
            incomplete = True
            continue
        try:
            pid = int(record["pid"])
            admitted[pid] = record["instance_id"]
            locator = record.get(_LOCATOR)
            path = Path(locator) if locator is not None else Path(record["runtime_root"])
            if not path.is_absolute():
                raise ValueError("relative Host location")
            locations = by_file if locator is not None else by_root
            locations.setdefault(path, {})[pid] = record["instance_id"]
        except (KeyError, ValueError):
            incomplete = True
    for directory, owners in by_root.items():
        try:
            paths = [entry for entry in directory.iterdir() if entry.name.startswith("host-")
                     and entry.suffix == ".json"]
        except OSError:
            incomplete = True
            continue
        for path in paths:
            by_file.setdefault(path, {}).update(owners)
    targets: list[dict[str, Any]] = []
    seen: set[int] = set()
    for path, owners in by_file.items():
        try:
            if path.is_symlink():
                raise ValueError("unavailable Host state")
            with path.open("rb") as stream:
                raw = stream.read(_MAX_STATE_BYTES + 1)
            if len(raw) > _MAX_STATE_BYTES:
                raise ValueError("oversized Host state")
            state = json.loads(raw.decode("utf-8"))
            if not isinstance(state, dict):
                raise ValueError("malformed Host state")
            state_pid = state.get("host_pid")
            birth = state.get("host_process_start_time")
            if type(state_pid) is not int or state_pid not in owners or state_pid in seen:
                continue
            declared_instance = state.get("host_instance_id")
            if declared_instance is not None:
                matched = declared_instance == owners[state_pid]
            else:
                matched = isinstance(birth, str) and process_start_time(state_pid) == birth
            if not matched:
                incomplete = True
                continue
            if state.get("state") in {"child_exited", "closed", "stopped", "failed"}:
                seen.add(state_pid)
                continue
            if state.get("state") != "running":
                incomplete = True
                continue
            if not isinstance(birth, str):
                birth = process_start_time(state_pid)
            sid = state.get("session_id")
            sid = sid if isinstance(sid, str) and sid else None
            cwd = state.get("cwd")
            cwd = cwd if isinstance(cwd, str) else None
            seen.add(state_pid)
            targets.append(_target(state_pid, owners[state_pid], sid, birth, cwd))
        except (OSError, ValueError, UnicodeError):
            incomplete = True
    for pid, instance in admitted.items():
        if pid not in seen:
            targets.append(_target(pid, instance, None, process_start_time(pid), None))
            incomplete = True
    return targets, {
        "availability": "unavailable" if incomplete else "available",
        "reason": "host_metadata_unconfirmed" if incomplete else None,
    }
