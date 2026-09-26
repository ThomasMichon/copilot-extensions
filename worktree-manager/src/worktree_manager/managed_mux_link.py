"""Worktree Manager -> agent-worktrees managed-mux client helpers.

Phase 3b Slice 2 Sub-slice 3 Step 3 wires Worktree Manager's real mux
launch/join/restore path into the resident ``agent-worktrees`` status-monitor's
new ``mux-live-v1`` observation endpoint. The server-side contract already
landed in ``plugins/agent-worktrees/src/agent_worktrees/mux_link.py`` during
Step 1, but Worktree Manager must not import ``agent_worktrees`` in-process
(``mux_mapping_registry.py``'s process-boundary rule). This module therefore
duplicates the **client-side only** pieces of that contract inside
Worktree Manager, following the same precedent this package already uses for
vendored cross-boundary helpers such as ``work_coalescing_singleton`` and the
installed-runtime resolver.

The duplicate is intentionally narrow:

* resolve the exact installed ``agent-worktrees`` runtime root/command via
  :mod:`agent_plugin_runtime` (never PATH, never a same-repo source import);
* parse the Step-1-added rendezvous fields out of ``status-monitor.lock``;
* derive the same injective coalescing key ``mux_link._push_key`` does; and
* issue one loopback JSON request through ``work_coalescing_singleton``.

The "boot the monitor if needed" leg also stays process-boundary-only: the
helper shells to the exact installed ``agent-worktrees`` runtime's public
``status-monitor-restart`` verb, which already has the desired "spawn when
absent / stand down when current / reap stale" semantics. No Worktree Manager
code imports ``agent_worktrees.status_monitor_runtime`` directly.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from work_coalescing_singleton import client as wcs_client

from . import agent_plugin_runtime, engine_client

#: Wire kind pinned by Step 1's server-side contract in agent-worktrees.
KIND = "mux-live-v1"
REQUEST_DEADLINE_S = 2.0
BOOT_WAIT_S = 6.0


def agent_worktrees_runtime_root() -> Path | None:
    """The exact installed ``agent-worktrees`` runtime root, or ``None``.

    ``resolve_installed_plugin_slot("agent-worktrees")`` returns the immutable
    slot ``<root>/versions/<version>`` selected by the plugin's own install
    receipts/policy. ``status-monitor.lock`` lives one level above that
    version tree, at ``<root>/status-monitor.lock``.
    """
    slot = agent_plugin_runtime.resolve_installed_plugin_slot("agent-worktrees")
    if slot is None:
        return None
    return slot.parent.parent


def lock_path(root: Path | None = None) -> Path:
    """The resident monitor's lock/rendezvous file path."""
    resolved_root = root if root is not None else agent_worktrees_runtime_root()
    if resolved_root is None:
        agent_home = os.environ.get("AGENT_HOME")
        if agent_home:
            resolved_root = Path(agent_home) / ".agent-worktrees"
        else:
            home = Path(os.environ.get("USERPROFILE") or Path.home())
            resolved_root = home / ".agent-worktrees"
    return resolved_root / "status-monitor.lock"


def read_lock_data(path: Path) -> dict | None:
    """Best-effort parse of ``status-monitor.lock``'s JSON payload."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def endpoint_from_rendezvous(data: dict | None) -> tuple[str, int, str] | None:
    """Parse Step 1's ``managed_mux_*`` rendezvous fields."""
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


def _push_key(payload: dict) -> str:
    """Derive the exact coalescing key ``agent_worktrees.mux_link`` expects."""
    project = payload.get("project")
    worktree_id = payload.get("worktree_id")
    revision = payload.get("mapping_revision")
    if not isinstance(project, str) or not project:
        raise ValueError("mux-live-v1 push payload missing 'project'")
    if not isinstance(worktree_id, str) or not worktree_id:
        raise ValueError("mux-live-v1 push payload missing 'worktree_id'")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValueError("mux-live-v1 push payload requires an integer mapping_revision")
    if revision < 0:
        raise ValueError("mux-live-v1 push payload mapping_revision must be non-negative")
    return f"{len(project)}:{project}:{len(worktree_id)}:{worktree_id}:{revision}"


def mux_live_via_daemon(
    lock_data: dict | None,
    *,
    payload: dict,
    fallback: Callable[[], dict],
    request_deadline_s: float = REQUEST_DEADLINE_S,
) -> dict:
    """Push one ``mux-live-v1`` payload to an already-live monitor if possible."""
    endpoint = endpoint_from_rendezvous(lock_data)
    if endpoint is None:
        return fallback()
    key = _push_key(payload)
    host, port, token = endpoint
    client_id = wcs_client.new_client_id()
    try:
        return wcs_client.request(
            host,
            port,
            token,
            kind=KIND,
            key=key,
            payload=payload,
            request_deadline_s=request_deadline_s,
            client_id=client_id,
        )
    except wcs_client.DaemonUnavailable:
        return fallback()
    finally:
        wcs_client.release(host, port, token, client_id, timeout=request_deadline_s)


def ensure_status_monitor_running() -> bool:
    """Best-effort process-boundary ensure of the resident status-monitor."""
    command = agent_plugin_runtime.resolve_installed_plugin_command("agent-worktrees")
    if not command:
        return False
    try:
        result = subprocess.run(
            [*command, "status-monitor-restart"],
            capture_output=True,
            text=True,
            timeout=30,
            env=engine_client._engine_environment(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def mux_live_with_boot(
    *,
    payload: dict,
    fallback: Callable[[], dict],
    request_deadline_s: float = REQUEST_DEADLINE_S,
    boot_wait_s: float = BOOT_WAIT_S,
    poll_interval_s: float = 0.1,
) -> dict:
    """Mirror Step 1's boot-wait flow without importing ``agent_worktrees``."""
    started = time.time()
    _push_key(payload)  # validate early; the daemon path would reject this too.

    def _dial() -> tuple[str, int, str] | None:
        return endpoint_from_rendezvous(read_lock_data(lock_path()))

    endpoint = _dial()
    if endpoint is None:
        ensure_status_monitor_running()
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
            kind=KIND,
            key=_push_key(payload),
            payload=payload,
            request_deadline_s=request_deadline_s,
            client_id=client_id,
        )
    except wcs_client.DaemonUnavailable:
        return fallback()
    finally:
        wcs_client.release(host, port, token, client_id, timeout=request_deadline_s)
