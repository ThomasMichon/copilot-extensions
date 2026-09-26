"""Atomic Step-3 managed-mux activation/deactivation helpers.

This module is the real launch-path bundle Phase 3b Slice 2 Sub-slice 3 Step 3
asked for: one Worktree Manager action should not have to remember "register
the mapping, ensure the Manager daemon, then push a mux-live-v1 observation to
agent-worktrees". The launcher scripts and Picker-facing flows call one helper
per edge instead:

* :func:`activate_managed_session` -- create/join/restore/remux succeeded, so
  this worktree now has a live Manager-owned mux embodiment;
* :func:`deactivate_managed_session` -- that embodiment is gone.

The functions stay entirely inside Worktree Manager's ownership boundary:

* they update the Manager-owned mapping registry via
  :mod:`mux_mapping_registry`;
* they ensure the Manager-owned companion daemon via :mod:`mux_daemon`;
* they publish the Step 1/3 ``mux-live-v1`` observation through the vendored,
  process-boundary-only :mod:`managed_mux_link` client.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import managed_mux_link, mux_daemon


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_metadata(mux_bin: str, mux_session: str) -> dict:
    """Probe the live mux server for metadata the wire contract carries.

    Best-effort by design: failure to read one field should not block the whole
    activation path. The launcher already proved the session exists well enough
    to attach/create it; the metadata here merely enriches the observation and
    preserves the same ``session_id:created`` incarnation token the resident
    monitor's own direct scan uses.
    """

    def _run(*argv: str, timeout: int = 5) -> str | None:
        try:
            result = subprocess.run(
                [mux_bin, *argv],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return (result.stdout or "").strip()

    attached_clients = 0
    session_incarnation = ""
    sessions_out = _run(
        "list-sessions",
        "-F",
        "#{session_name}:#{session_attached}:#{session_id}:#{session_created}",
    )
    if sessions_out:
        for line in sessions_out.splitlines():
            if line.count(":") < 3:
                continue
            name, _, created = line.rpartition(":")
            name, _, session_id = name.rpartition(":")
            name, _, attached = name.rpartition(":")
            if name != mux_session:
                continue
            try:
                attached_clients = int(attached)
            except ValueError:
                attached_clients = 0
            session_incarnation = f"{session_id}:{created}"
            break

    panes: list[dict[str, object]] = []
    panes_out = _run("list-panes", "-t", mux_session, "-F", "#{pane_id}:#{pane_active}")
    if panes_out:
        for line in panes_out.splitlines():
            pane_id, _, active = line.partition(":")
            if not pane_id:
                continue
            panes.append(
                {
                    "pane_id": pane_id,
                    "role": "head" if active == "1" else "",
                    "live": True,
                }
            )
    return {
        "attached_clients": attached_clients,
        "session_incarnation": session_incarnation,
        "panes": panes,
    }


def activate_managed_session(
    project: str,
    worktree_id: str,
    worktree_path: str,
    mux_session: str,
    mux_bin: str,
    *,
    root: Path | None = None,
) -> dict:
    """Register + observe one live Manager-owned mux embodiment.

    Revision allocation happens atomically inside
    :func:`mux_daemon.activate_mapping` (a single locked
    read-current/allocate-next/write critical section), not via a separate
    unlocked ``get_mapping`` call followed by a second-lock ``register``
    (Copilot review finding on PR #3829: that two-step shape was a
    read-then-write race where two concurrent activations could both
    observe the same current entry and register at the same, indistinguishable
    revision)."""
    metadata = _session_metadata(mux_bin, mux_session)
    payload = {
        "project": project,
        "worktree_id": worktree_id,
        "worktree_path": worktree_path,
        "mux_session": mux_session,
        "mux_bin": mux_bin,
        "session_incarnation": metadata["session_incarnation"],
        "panes": metadata["panes"],
        "attached_clients": metadata["attached_clients"],
        "live": True,
        "observed_at": _now_iso(),
    }
    mapping_result = mux_daemon.activate_mapping(payload, root=root)
    mapping_revision = mapping_result["revision"]
    daemon_running = mux_daemon.ensure_daemon_running(root)
    observation_payload = {
        key: value for key, value in payload.items() if key != "mux_bin"
    }
    observation_payload["mapping_revision"] = mapping_revision
    observe_result = managed_mux_link.mux_live_with_boot(
        payload=observation_payload,
        fallback=lambda: {"delivered": False, "reason": "unreachable"},
    )
    return {
        "mapping": mapping_result,
        "daemon_running": daemon_running,
        "observation": observe_result,
        "mapping_revision": mapping_revision,
    }


def deactivate_managed_session(
    project: str,
    worktree_id: str,
    mux_session: str,
    *,
    root: Path | None = None,
) -> dict:
    """Tombstone + observe the loss of one Manager-owned mux embodiment.

    Revision allocation happens atomically inside
    :func:`mux_daemon.deactivate_mapping` (see :func:`activate_managed_session`
    for why the previous separate-get-then-remove shape was a race)."""
    current = mux_daemon.get_mapping(project, worktree_id, root=root)
    remove_result = mux_daemon.deactivate_mapping(project, worktree_id, mux_session, root=root)
    revision = remove_result.get("revision")
    if current is None or revision is None:
        return {
            "mapping": remove_result,
            "observation": {"delivered": False, "reason": "absent"},
            "mapping_revision": None,
        }
    observe_result = managed_mux_link.mux_live_with_boot(
        payload={
            "project": project,
            "worktree_id": worktree_id,
            "worktree_path": current.get("worktree_path"),
            "mux_session": mux_session,
            "session_incarnation": current.get("session_incarnation") or "",
            "panes": current.get("panes") or [],
            "attached_clients": 0,
            "live": False,
            "mapping_revision": revision,
            "observed_at": _now_iso(),
        },
        fallback=lambda: {"delivered": False, "reason": "unreachable"},
    )
    return {
        "mapping": remove_result,
        "observation": observe_result,
        "mapping_revision": revision,
    }
