"""Resident client for Worktree Manager's ``mux-status-v1`` sink.

`agent-worktrees` remains the sole status-data authority, but once a live mux
session is Manager-owned the resident monitor stops writing ``set-option``
itself and instead hands the already-rendered option payload to Worktree
Manager's mux companion daemon. This module owns only that small loopback JSON
client seam; it deliberately does not import ``worktree_manager`` in-process.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from work_coalescing_singleton import client as wcs_client

KIND = "mux-status-v1"
REQUEST_DEADLINE_S = 5.0


def _default_root() -> Path:
    override = os.environ.get("WORKTREE_MANAGER_ROOT")
    if override:
        return Path(override)
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".worktree-manager"


def lock_path(root: Path | None = None) -> Path:
    return (root if root is not None else _default_root()) / "mux-daemon.lock"


def read_lock_data(path: Path | None = None) -> dict | None:
    target = path if path is not None else lock_path()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def endpoint_from_rendezvous(data: dict | None) -> tuple[str, int, str] | None:
    if not isinstance(data, dict):
        return None
    endpoint = data.get("manager_mux_endpoint")
    token = data.get("manager_mux_token")
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


def status_push_key(payload: dict) -> str:
    project = payload.get("project")
    worktree_id = payload.get("worktree_id")
    rendered_at = payload.get("rendered_at")
    values = payload.get("values")
    if not isinstance(project, str) or not project:
        raise ValueError("mux-status-v1 payload missing 'project'")
    if not isinstance(worktree_id, str) or not worktree_id:
        raise ValueError("mux-status-v1 payload missing 'worktree_id'")
    if not isinstance(rendered_at, str) or not rendered_at:
        raise ValueError("mux-status-v1 payload missing 'rendered_at'")
    if not isinstance(values, dict):
        raise ValueError("mux-status-v1 payload missing 'values'")
    values_digest = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return (
        f"{len(project)}:{project}:{len(worktree_id)}:{worktree_id}:"
        f"{rendered_at}:{values_digest}"
    )


def push_status_via_daemon(
    payload: dict,
    *,
    lock_data: dict | None = None,
    request_deadline_s: float = REQUEST_DEADLINE_S,
) -> dict:
    endpoint = endpoint_from_rendezvous(lock_data if lock_data is not None else read_lock_data())
    if endpoint is None:
        return {"applied": False, "reason": "daemon-unavailable"}
    key = status_push_key(payload)
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
        return {"applied": False, "reason": "daemon-unavailable"}
    finally:
        wcs_client.release(host, port, token, client_id, timeout=request_deadline_s)


def publish_managed_session_status(
    *,
    managed_mux_cache,
    project: str | None,
    path: str,
    session_name: str,
    values: dict[str, str],
    published: dict[tuple[str, str], str] | None,
    prefix: str,
    token: str,
    resolve_worktree_id,
    before_publish=None,
    request_deadline_s: float = REQUEST_DEADLINE_S,
) -> dict | None:
    if managed_mux_cache is None or not project:
        return None
    try:
        worktree_id = resolve_worktree_id(path, project=project)
    except Exception:
        return None
    if not worktree_id:
        return None
    entry = managed_mux_cache.get(project, worktree_id)
    if not (
        entry
        and entry.get("live")
        and entry.get("mux_session") == session_name
    ):
        return None
    changed_values = {
        option: value
        for option, value in values.items()
        if published is None or published.get((session_name, option)) != value
    }
    if not changed_values:
        return {"handled": True, "applied": True, "context_published": False}
    if before_publish is not None:
        before_publish()
    result = push_status_via_daemon(
        {
            "project": project,
            "worktree_id": worktree_id,
            "values": changed_values,
            "rendered_at": datetime.now(timezone.utc).isoformat(),
            "monitor_generation": f"{prefix}:{token}",
        },
        request_deadline_s=request_deadline_s,
    )
    if result.get("applied") and published is not None:
        for option, value in changed_values.items():
            published[(session_name, option)] = value
    return {
        "handled": True,
        "applied": bool(result.get("applied")),
        "context_published": bool(result.get("applied")) and "@aw_ctx" in changed_values,
    }
