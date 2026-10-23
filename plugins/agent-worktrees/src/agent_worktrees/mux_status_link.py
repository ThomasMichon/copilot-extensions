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
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from agent_procutil import no_window_kwargs, owned_process, spawn_sync_in_kill_on_close_job
from work_coalescing_singleton import client as wcs_client
from zdd import routing

KIND = "mux-status-v1"
REQUEST_DEADLINE_S = 5.0
_ROUTING_DIRNAME = "mux-daemon-routing"
_TOKEN_FILENAME = "mux-daemon.token"
_startup_after: dict[tuple[tuple[str, ...], str], float] = {}
_startup_lock = threading.Lock()
_now = time.monotonic


def _selected_manager():
    from . import control_plane_providers, manager_launch_cli

    manifest, diagnostic = manager_launch_cli._control_plane_provider_selection(
        directory=control_plane_providers._machine_control_plane_providers_dir(),
    )
    if manifest is not None:
        if manifest.provider != "worktree-manager":
            return None, "selected-provider-does-not-own-manager-mux"
        root = Path(manifest.provider_root)
        if (
            not root.is_absolute() or root == Path(root.anchor)
            or not Path(manifest.command[0]).is_absolute()
        ):
            return None, "selected-manager-identity-unavailable"
    return manifest, diagnostic


def _default_root() -> Path:
    override = os.environ.get("WORKTREE_MANAGER_ROOT")
    if override:
        return Path(override)
    manifest, _ = _selected_manager()
    if manifest is not None:
        return Path(manifest.provider_root)
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".worktree-manager"


def lock_path(root: Path | None = None) -> Path:
    return (root if root is not None else _default_root()) / "mux-daemon.lock"


def routing_dir(root: Path | None = None) -> Path:
    return (root if root is not None else _default_root()) / _ROUTING_DIRNAME


def control_token_path(root: Path | None = None) -> Path:
    return (root if root is not None else _default_root()) / _TOKEN_FILENAME


def load_control_token(root: Path | None = None) -> str | None:
    path = control_token_path(root)
    try:
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    except OSError:
        pass
    return None


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
    fence = json.dumps(
        {field: payload[field] for field in (
            "mapping_revision", "mux_session", "session_incarnation",
        ) if field in payload},
        sort_keys=True, separators=(",", ":"),
    )
    return (
        f"{len(project)}:{project}:{len(worktree_id)}:{worktree_id}:"
        f"{rendered_at}:{values_digest}:{fence}"
    )


def push_status_via_daemon(
    payload: dict,
    *,
    lock_data: dict | None = None,
    request_deadline_s: float = REQUEST_DEADLINE_S,
) -> dict:
    return _push_via_daemon(
        KIND, status_push_key(payload), payload,
        lock_data=lock_data, request_deadline_s=request_deadline_s,
    )


def push_discovery_via_daemon(payload: dict) -> dict | None:
    """Start only an already-selected Manager, on positive attributable demand."""
    manifest, diagnostic = _selected_manager()
    if diagnostic:
        return {"applied": False, "reason": diagnostic}
    root = Path(manifest.provider_root) if manifest is not None else _default_root()
    override = os.environ.get("WORKTREE_MANAGER_ROOT")
    if manifest is not None and override and Path(override).expanduser().resolve() != root.resolve():
        return {"applied": False, "reason": "selected-manager-root-conflict"}
    if (
        manifest is None and _manager_endpoint(root) is None
        and not lock_path(root).exists() and not control_token_path(root).exists()
    ):
        return None
    if manifest is not None and not _endpoint_available(root):
        if not (
            isinstance(payload.get("attached_clients"), int)
            and not isinstance(payload["attached_clients"], bool)
            and payload["attached_clients"] > 0
            and all(isinstance(payload.get(field), str) and payload[field] for field in (
                "project", "worktree_id", "worktree_path", "mux_session", "session_incarnation",
            ))
            and payload["mux_session"] == "wt-" + payload["worktree_id"].replace(".", "_")
        ):
            return {"applied": False, "reason": "no-attributable-connected-demand"}
        startup = _ensure_selected_manager(manifest, root)
        if not startup["applied"]:
            return startup
    key = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    result = _push_via_daemon("mux-discover-v1", key, payload, root=root, request_deadline_s=2.0)
    if not result.get("applied") and not result.get("reason"):
        return {"applied": False, "reason": "manager-discovery-rejected"}
    return result


def _manager_endpoint(root: Path, lock_data: dict | None = None):
    route = routing.read_active_endpoint(routing_dir(root))
    token = load_control_token(root)
    if route is not None and token is not None:
        return route.client_host, route.port, token
    return endpoint_from_rendezvous(
        lock_data if lock_data is not None else read_lock_data(lock_path(root))
    )


def _endpoint_available(root: Path) -> bool:
    endpoint = _manager_endpoint(root)
    if endpoint is None:
        return False
    host, port, token = endpoint
    client_id = wcs_client.new_client_id()
    try:
        wcs_client.subscribe(host, port, token, client_id, timeout=1.0)
        return True
    except wcs_client.DaemonUnavailable:
        return False
    finally:
        wcs_client.release(host, port, token, client_id, timeout=1.0)


def _invoke_manager_ensure(manifest, root: Path) -> bool:
    from . import manager_launch_cli

    env = manager_launch_cli._launch_probe_env()
    env["WORKTREE_MANAGER_ROOT"] = str(root)
    env["WORKTREE_MANAGER_ENGINE_ARGV"] = json.dumps([sys.executable, "-m", "agent_worktrees"])
    posix = sys.platform != "win32"
    if posix:
        owned_process._require_posix_execution_support()
    argv = [*manifest.command, "mux-daemon", "ensure"]
    proc, job = spawn_sync_in_kill_on_close_job(
        argv, require_job=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(root), env=env, **no_window_kwargs(),
        **({"start_new_session": True} if posix else {}),
    )
    try:
        if posix:
            owned_process._communicate_unreaped(
                proc, input_text=None, deadline=_now() + 10.0, argv=argv, timeout=10.0,
            )
        else:
            proc.wait(timeout=10.0)
    finally:
        if posix:
            owned_process._retire_posix_group(proc)
        elif job is not None:
            job.close()
        elif proc.poll() is None:
            proc.kill()
        exit_code = proc.wait(timeout=5.0)
    return exit_code == 0


def _ensure_selected_manager(manifest, root: Path) -> dict:
    key = (manifest.command, str(root))
    with _startup_lock:
        if _now() < _startup_after.get(key, 0.0):
            return {"applied": False, "reason": "manager-startup-deferred"}
        _startup_after[key] = float("inf")
    try:
        try:
            started = _invoke_manager_ensure(manifest, root)
        except (OSError, subprocess.TimeoutExpired, RuntimeError):
            started = False
        return {"applied": True} if started else {"applied": False, "reason": "manager-startup-failed"}
    finally:
        with _startup_lock:
            _startup_after[key] = _now() + 20.0


def _push_via_daemon(
    kind: str,
    key: str,
    payload: dict,
    *,
    root: Path | None = None,
    lock_data: dict | None = None,
    request_deadline_s: float = REQUEST_DEADLINE_S,
) -> dict:
    endpoint = _manager_endpoint(root if root is not None else _default_root(), lock_data)
    if endpoint is None:
        return {"applied": False, "reason": "daemon-unavailable"}
    host, port, token = endpoint
    client_id = wcs_client.new_client_id()
    try:
        return wcs_client.request(
            host,
            port,
            token,
            kind=kind,
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
    worktree_id: str | None = None,
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
    resolved_worktree_id = worktree_id
    if not resolved_worktree_id:
        try:
            resolved_worktree_id = resolve_worktree_id(path, project=project)
        except Exception:
            return None
    if not resolved_worktree_id:
        return None
    entry = managed_mux_cache.get(project, resolved_worktree_id)
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
            "worktree_id": resolved_worktree_id,
            "values": changed_values,
            "rendered_at": datetime.now(timezone.utc).isoformat(),
            "monitor_generation": f"{prefix}:{token}",
            "mapping_revision": entry["mapping_revision"],
            "mux_session": entry["mux_session"],
            "session_incarnation": entry.get("session_incarnation") or "",
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
