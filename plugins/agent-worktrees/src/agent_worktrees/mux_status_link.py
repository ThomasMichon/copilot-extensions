"""Resident status-monitor -> Worktree Manager mux-status client helpers.

Phase 3b Slice 2 Sub-slice 3 Step 3 keeps ``agent-worktrees`` as the sole
status-data renderer while moving the actual `set-option` writes for
Manager-owned mux sessions out to Worktree Manager's own resident companion
daemon. This module is the process-boundary client for that reverse direction:
read ``mux-daemon.lock``, parse the published loopback endpoint, derive the same
``mux-status-v1`` coalescing key the daemon expects, and issue one bounded
request.

Deliberately duplicated rather than importing ``worktree_manager.mux_daemon``:
the plugin must not grow an in-process dependency on Worktree Manager just to
talk to its daemon. The wire contract is small, stable, and already pinned in
the Phase 3b plan.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from work_coalescing_singleton import client as wcs_client

KIND = "mux-status-v1"
REQUEST_DEADLINE_S = 5.0


def manager_root() -> Path:
    override = os.environ.get("WORKTREE_MANAGER_ROOT")
    if override:
        return Path(override)
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return Path(home) / ".worktree-manager"


def lock_path(root: Path | None = None) -> Path:
    return (root if root is not None else manager_root()) / "mux-daemon.lock"


def read_lock_data(path: Path) -> dict | None:
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
    """Mirror Worktree Manager's ``mux_daemon.status_push_key`` exactly."""
    project = payload.get("project")
    worktree_id = payload.get("worktree_id")
    rendered_at = payload.get("rendered_at")
    values = payload.get("values")
    if not isinstance(project, str) or not project:
        raise ValueError("mux-status-v1 push payload missing 'project'")
    if not isinstance(worktree_id, str) or not worktree_id:
        raise ValueError("mux-status-v1 push payload missing 'worktree_id'")
    if not isinstance(rendered_at, str) or not rendered_at:
        raise ValueError("mux-status-v1 push payload missing 'rendered_at'")
    if not isinstance(values, dict):
        raise ValueError("mux-status-v1 push payload missing 'values'")
    values_digest = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return (
        f"{len(project)}:{project}:{len(worktree_id)}:{worktree_id}:"
        f"{rendered_at}:{values_digest}"
    )


def push_status_update(
    payload: dict,
    *,
    request_deadline_s: float = REQUEST_DEADLINE_S,
    root: Path | None = None,
) -> dict | None:
    """Try one ``mux-status-v1`` request; return ``None`` when unreachable."""
    endpoint = endpoint_from_rendezvous(read_lock_data(lock_path(root)))
    if endpoint is None:
        return None
    host, port, token = endpoint
    client_id = wcs_client.new_client_id()
    try:
        return wcs_client.request(
            host,
            port,
            token,
            kind=KIND,
            key=status_push_key(payload),
            payload=payload,
            request_deadline_s=request_deadline_s,
            client_id=client_id,
        )
    except wcs_client.DaemonUnavailable:
        return None
    finally:
        wcs_client.release(host, port, token, client_id, timeout=request_deadline_s)


def live_entries_by_session(cache) -> tuple[set[str], dict[str, dict]]:
    """Return the live session-name set plus freshest entry per session."""
    live_names = cache.live_session_names() if cache is not None else set()
    entries_by_session: dict[str, dict] = {}
    if cache is None or not live_names:
        return live_names, entries_by_session
    for entry in cache.snapshot().values():
        session_name = entry.get("mux_session")
        if (
            not entry.get("live")
            or not isinstance(session_name, str)
            or session_name not in live_names
        ):
            continue
        current = entries_by_session.get(session_name)
        if current is None or entry.get("mapping_revision", -1) >= current.get(
            "mapping_revision", -1
        ):
            entries_by_session[session_name] = entry
    return live_names, entries_by_session


def publish_managed_status(
    session: str,
    entry: dict,
    *,
    token: str,
    prefix: str,
    context_value: str | None,
    segment_value: str,
    published: dict[tuple[str, str], str] | None,
) -> tuple[bool, str | None]:
    """Publish one Manager-owned session's rendered ``@aw_*`` payload.

    Returns ``(True, None)`` on success (or when there was nothing new to send),
    else ``(False, <reason>)`` where ``reason`` is a small loggable token.
    """
    values = {
        "@aw_updater": token,
        "@aw_updater_prefix": prefix,
        "@aw_seg": segment_value,
    }
    if context_value is not None:
        values["@aw_ctx"] = context_value
    changed_values = {
        option: value
        for option, value in values.items()
        if published is None or published.get((session, option)) != value
    }
    if not changed_values:
        return True, None
    result = push_status_update(
        {
            "project": entry["project"],
            "worktree_id": entry["worktree_id"],
            "values": changed_values,
            "rendered_at": datetime.now(timezone.utc).isoformat(),
            "monitor_generation": prefix,
        }
    )
    if result is None:
        return False, "manager-daemon-unavailable"
    if not result.get("applied"):
        return False, str(result.get("reason") or "manager-daemon-declined")
    if published is not None:
        for option, value in changed_values.items():
            published[(session, option)] = value
    return True, None


def dispatch_managed_status(
    session: str,
    *,
    live_names: set[str],
    entries_by_session: dict[str, dict],
    token: str,
    prefix: str,
    context_value: str | None,
    segment_value: str,
    published: dict[tuple[str, str], str] | None,
) -> tuple[bool, bool, str | None]:
    """Route one session through the Manager-owned status path when applicable.

    Returns ``(handled, ok, reason)``:

    * ``handled=False`` -> session is unmanaged; caller should use its direct path.
    * ``handled=True, ok=True`` -> Manager path succeeded (or nothing changed).
    * ``handled=True, ok=False`` -> Manager-owned but could not be written;
      caller must NOT fall back to direct mux writes this sweep.
    """
    if session not in live_names:
        return False, True, None
    entry = entries_by_session.get(session)
    if entry is None:
        return True, False, "missing-manager-mapping"
    ok, reason = publish_managed_status(
        session,
        entry,
        token=token,
        prefix=prefix,
        context_value=context_value,
        segment_value=segment_value,
        published=published,
    )
    return True, ok, reason
