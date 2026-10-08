"""Attached-client count probe for a managed mux session (#4564).

Split out of :mod:`worktree_manager.mux_daemon` purely to stay under this
repo's per-module line cap -- this module owns exactly one small, pure
subprocess probe, with no dependency on the rest of the mux-companion
daemon.
"""

from __future__ import annotations

import subprocess

from agent_procutil import no_window_flags


def mux_attached_clients(mux_bin: str, session: str) -> int | None:
    """Bounded, best-effort count of clients currently attached to
    ``session`` (``list-clients``, supported by both tmux and psmux).

    Returns ``None`` on any probe failure (exception, timeout, or a nonzero
    exit) -- an *unknown* count, not a confirmed zero. The caller treats
    ``None`` as "skip this refresh": a transient probe hiccup must never
    stomp a previously-observed, real attached-client count with a
    misleading ``0`` (see #4564 -- the field this closes). Only a clean,
    zero-exit ``list-clients`` call is trusted to report the real count,
    including a genuine zero (an empty-but-successful listing)."""
    try:
        result = subprocess.run(
            [mux_bin, "list-clients", "-t", session],
            capture_output=True,
            text=True,
            timeout=1,
            creationflags=no_window_flags(),
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    stdout = result.stdout or ""
    return len([line for line in stdout.splitlines() if line.strip()])


def refresh_attached_clients(registry, current: dict) -> dict:
    """Opportunistically refresh ``current``'s registry-tracked
    ``attached_clients`` via a real :func:`mux_attached_clients` probe.
    Returns the (possibly updated) mapping dict; a probe failure (``None``)
    or an unchanged count is a no-op, per :func:`mux_attached_clients`'s own
    docstring.

    The actual persist goes through
    :meth:`~worktree_manager.mux_mapping_registry.MuxMappingRegistry.
    update_attached_clients`, an atomic identity-guarded single-field update
    -- ``list-clients`` can block for up to several
    seconds, long enough for a concurrent ``register()`` to replace this
    mapping's ``mux_session`` at the SAME revision (a case ``register()``'s
    own monotonic-revision guard deliberately permits, so it does not
    reject this on its own). Writing the stale whole-entry snapshot back
    after that race would silently resurrect the superseded session;
    ``update_attached_clients`` re-verifies identity and persists only the
    one field, atomically, so a superseded refresh is correctly abandoned
    instead."""
    observed = mux_attached_clients(current["mux_bin"], current["mux_session"])
    if observed is None or observed == current.get("attached_clients"):
        return current
    result = registry.update_attached_clients(
        current["project"],
        current["worktree_id"],
        observed,
        mapping_revision=current["mapping_revision"],
        mux_session=current["mux_session"],
        session_incarnation=current.get("session_incarnation"),
    )
    if not result.get("applied"):
        return current
    updated = dict(current)
    updated["attached_clients"] = observed
    return updated
