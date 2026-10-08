"""Manager-owned managed-mux live-observation publishing.

Split out of :mod:`worktree_manager.mux_daemon` so the resident-daemon module
can grow a graceful-cutover control surface without crossing the module-size
guard. This file owns only the Status Monitor-facing ``mux-live-v1`` link and
the small registry helpers that immediately publish live/tombstone
observations after register/remove.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from work_coalescing_singleton import client as wcs_client

from .mux_attached_clients import AttachedClientObserver
from .mux_mapping_registry import (
    MuxMappingRegistry,
    get_mapping,
    register_next_mapping,
    remove_mapping,
)

LIVE_KIND = "mux-live-v1"
LIVE_REQUEST_DEADLINE_S = 2.0
LIVE_BOOT_WAIT_S = 6.0
_SESSION_CREDENTIAL_ENV_KEYS = {"GH_TOKEN", "GITHUB_TOKEN", "AGENT_WORKTREES_AHP_AUTH_TOKEN"}


def read_lock_data(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _agent_worktrees_root() -> Path | None:
    from . import agent_plugin_runtime

    slot = agent_plugin_runtime.resolve_installed_plugin_slot("agent-worktrees")
    if slot is None:
        return None
    return slot.parent.parent


def status_monitor_lock_path() -> Path | None:
    root = _agent_worktrees_root()
    return None if root is None else root / "status-monitor.lock"


def status_monitor_generation(data: dict | None) -> str | None:
    if not isinstance(data, dict):
        return None
    generation = data.get("managed_mux_generation")
    return generation if isinstance(generation, str) and generation else None


def _status_monitor_endpoint_from_rendezvous(data: dict | None) -> tuple[str, int, str] | None:
    if not isinstance(data, dict):
        return None
    endpoint = data.get("managed_mux_endpoint")
    token = data.get("managed_mux_token")
    if not isinstance(endpoint, str) or not isinstance(token, str) or not token:
        return None
    host, _, port_s = endpoint.partition(":")
    if not host or not port_s:
        return None
    try:
        port = int(port_s)
    except ValueError:
        return None
    if not 0 < port < 65536:
        return None
    return host, port, token


def _scrub_session_credentials(env: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in env.items()
        if key.upper() not in _SESSION_CREDENTIAL_ENV_KEYS
    }


def ensure_status_monitor_running() -> bool:
    from . import engine_client

    base = engine_client.engine_base_command()
    if not base:
        return False
    env = _scrub_session_credentials(engine_client._engine_environment())
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "timeout": 30,
        "check": False,
        "env": env,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run([*base, "status-monitor-restart"], **kwargs)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def live_push_key(payload: dict) -> str:
    project = payload.get("project")
    worktree_id = payload.get("worktree_id")
    revision = payload.get("mapping_revision")
    if not isinstance(project, str) or not project:
        raise ValueError("mux-live-v1 payload missing 'project'")
    if not isinstance(worktree_id, str) or not worktree_id:
        raise ValueError("mux-live-v1 payload missing 'worktree_id'")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValueError("mux-live-v1 payload missing 'mapping_revision'")
    return f"{len(project)}:{project}:{len(worktree_id)}:{worktree_id}:{revision}"


def mux_live_via_daemon(
    lock_data: dict | None,
    *,
    payload: dict,
    fallback: Callable[[], dict],
    request_deadline_s: float = LIVE_REQUEST_DEADLINE_S,
) -> dict:
    endpoint = _status_monitor_endpoint_from_rendezvous(lock_data)
    if endpoint is None:
        return fallback()
    key = live_push_key(payload)
    host, port, token = endpoint
    client_id = wcs_client.new_client_id()
    try:
        return wcs_client.request(
            host,
            port,
            token,
            kind=LIVE_KIND,
            key=key,
            payload=payload,
            request_deadline_s=request_deadline_s,
            client_id=client_id,
        )
    except wcs_client.DaemonUnavailable:
        return fallback()
    finally:
        wcs_client.release(host, port, token, client_id, timeout=request_deadline_s)


def mux_live_with_boot(
    *,
    read_lock_data: Callable[[], dict | None],
    ensure_monitor: Callable[[], bool] | None,
    payload: dict,
    fallback: Callable[[], dict],
    request_deadline_s: float = LIVE_REQUEST_DEADLINE_S,
    boot_wait_s: float = LIVE_BOOT_WAIT_S,
    poll_interval_s: float = 0.1,
) -> dict:
    started = time.time()

    def _dial() -> tuple[str, int, str] | None:
        return _status_monitor_endpoint_from_rendezvous(read_lock_data())

    endpoint = _dial()
    if endpoint is None and ensure_monitor is not None:
        ensure_monitor()
        while endpoint is None and time.time() - started < boot_wait_s:
            time.sleep(poll_interval_s)
            endpoint = _dial()
    if endpoint is None:
        return fallback()

    host, port, token = endpoint
    client_id = wcs_client.new_client_id()
    try:
        return wcs_client.request(
            host,
            port,
            token,
            kind=LIVE_KIND,
            key=live_push_key(payload),
            payload=payload,
            request_deadline_s=request_deadline_s,
            client_id=client_id,
        )
    except wcs_client.DaemonUnavailable:
        return fallback()
    finally:
        wcs_client.release(host, port, token, client_id, timeout=request_deadline_s)


def _mapping_to_observation(entry: dict) -> dict:
    return {
        "project": entry["project"],
        "worktree_id": entry["worktree_id"],
        "worktree_path": entry.get("worktree_path"),
        "mux_session": entry["mux_session"],
        "session_incarnation": entry.get("session_incarnation") or "",
        "panes": list(entry.get("panes") or []),
        "attached_clients": int(entry.get("attached_clients") or 0),
        "live": bool(entry.get("live", True)),
        "mapping_revision": int(entry["mapping_revision"]),
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _monitor_unavailable() -> dict:
    return {"applied": False, "reason": "monitor-unavailable"}


def publish_live_observation(
    entry: dict,
    *,
    ensure_monitor: bool = True,
    request_deadline_s: float = LIVE_REQUEST_DEADLINE_S,
    boot_wait_s: float = LIVE_BOOT_WAIT_S,
) -> dict:
    lock = status_monitor_lock_path()
    if lock is None:
        return _monitor_unavailable()
    return mux_live_with_boot(
        read_lock_data=lambda: read_lock_data(lock),
        ensure_monitor=ensure_status_monitor_running if ensure_monitor else None,
        payload=_mapping_to_observation(entry),
        fallback=_monitor_unavailable,
        request_deadline_s=request_deadline_s,
        boot_wait_s=boot_wait_s,
    )


def register_managed_mapping(
    payload: dict,
    *,
    root: Path | None,
    ensure_daemon_running: Callable[[Path | None], bool],
) -> dict:
    ensure_daemon_running(root)
    result = register_next_mapping(payload, root=root)
    project = payload.get("project")
    worktree_id = payload.get("worktree_id")
    if result.get("applied") and isinstance(project, str) and isinstance(worktree_id, str):
        entry = get_mapping(project, worktree_id, root=root)
        if entry is not None:
            publish_live_observation(entry, ensure_monitor=True)
    return result


def remove_managed_mapping(
    project: str,
    worktree_id: str,
    *,
    mapping_revision: int | None = None,
    mux_session: str | None = None,
    session_incarnation: str | None = None,
    root: Path | None = None,
) -> dict:
    result = remove_mapping(
        project,
        worktree_id,
        mapping_revision=mapping_revision,
        mux_session=mux_session,
        session_incarnation=session_incarnation,
        root=root,
    )
    if result.get("applied"):
        entry = get_mapping(project, worktree_id, root=root)
        if entry is not None:
            publish_live_observation(entry, ensure_monitor=True)
    return result


def republish_live_mappings(
    registry: MuxMappingRegistry,
    *,
    ensure_monitor: bool,
    publish: Callable | None = None,
    observer: AttachedClientObserver | None = None,
) -> bool:
    publish = publish if publish is not None else publish_live_observation
    observer = observer if observer is not None else AttachedClientObserver()
    observer.observe(registry)
    published_any = False
    for entry in registry.snapshot().values():
        if not entry.get("live"):
            continue
        current = registry.get(entry["project"], entry["worktree_id"])
        if current is None or not current["live"]:
            continue
        result = publish(current, ensure_monitor=ensure_monitor)
        if result.get("applied"):
            published_any = True
        else:
            return False
    return published_any or not registry.has_any_live()
