"""Lightweight, cached check for a newer Worktree Manager release.

Distinct from ``agent_worktrees.update_stage`` (the launcher's marketplace
payload staging): that module's ``indicator_state()`` reflects whether the
**agent-worktrees engine plugin**'s marketplace payload changed, not whether
a newer **Worktree Manager** version exists. The picker's topbar shows the
Manager's own ``v{VERSION}`` string, so a checkmark sourced only from the
engine's staging state next to it read as "the Manager is current" when it
never checked that at all -- confirmed live: an operator running an old
Manager build saw a plain "✓" next to their stale version number. This
module answers the question the operator actually asked.

Read-only/cheap functions (:func:`read_status`, :func:`indicator_state`,
:func:`should_check`) are safe on the render tick. The actual network fetch
(:func:`check_now`) belongs on a background thread, and is itself gated by
:data:`CHECK_INTERVAL_SECS` so a picker relaunch doesn't hit GitHub on every
single launch -- the cached status file persists across runs.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import self_install

#: Minimum time between real network checks -- avoids hitting GitHub on every
#: picker launch/render tick. A stale cache still answers instantly from disk.
CHECK_INTERVAL_SECS = 3600

_STATUS_FILE = "update-check.json"


def status_path(root: Path | None = None) -> Path:
    return (root or self_install.default_root()) / _STATUS_FILE


def read_status(root: Path | None = None) -> dict:
    """The last-persisted check result, or ``{}`` if none exists yet / it's
    unreadable. Never raises."""
    try:
        data = json.loads(status_path(root).read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_status(data: dict, root: Path | None = None) -> None:
    path = status_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def should_check(root: Path | None = None) -> bool:
    """Whether enough time has passed (or nothing has ever run) to justify
    another network check -- gates :func:`check_now` so a caller can poll
    this cheaply every launch without re-hitting the network each time.

    ``WORKTREE_NO_UPDATE=1`` (the same env var
    ``agent_worktrees.update_stage.indicator_state`` already honors to pause
    ITS check) also pauses this one -- an explicit opt-out, and the guard a
    test suite sets so mounting a real ``PickerScreen`` never makes a live
    network call."""
    if os.environ.get("WORKTREE_NO_UPDATE") == "1":
        return False
    checked_at = read_status(root).get("checked_at")
    if not isinstance(checked_at, (int, float)):
        return True
    return (time.time() - checked_at) >= CHECK_INTERVAL_SECS


def check_now(root: Path | None = None) -> dict:
    """Perform the real (network) check now and persist the result.

    Best-effort: :func:`self_install.fetch_remote_version` never raises, so
    neither does this -- a network failure just persists ``remote_version:
    None`` / ``available: False``, degrading to "idle" for
    :func:`indicator_state` rather than surfacing an error to the operator.
    Call this from a background thread; it is not cheap enough for the
    render tick. A no-op (returns ``{}``, writes nothing) under
    ``WORKTREE_NO_UPDATE=1`` -- callers should prefer gating on
    :func:`should_check` first, but this guards direct callers too."""
    if os.environ.get("WORKTREE_NO_UPDATE") == "1":
        return {}
    local = self_install.current_version(root) or self_install.payload_version()
    remote = self_install.fetch_remote_version(root)
    data = {
        "checked_at": time.time(),
        "local_version": local,
        "remote_version": remote,
        "available": bool(remote and local and remote != local),
    }
    _write_status(data, root)
    return data


def indicator_state(root: Path | None = None) -> str:
    """Cheap, read-only state for the picker's manager-update indicator:
    ``"available"`` | ``"current"`` | ``"idle"``. Never triggers a network
    check itself -- a caller drives that via :func:`should_check` +
    :func:`check_now` on its own background thread."""
    st = read_status(root)
    if not st:
        return "idle"
    if st.get("available"):
        return "available"
    if st.get("local_version") and st.get("remote_version"):
        return "current"
    return "idle"
