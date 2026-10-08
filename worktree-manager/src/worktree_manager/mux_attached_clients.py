"""Bounded, round-robin attachment observations for managed mux sessions.

Probe failures preserve stored counts; identity-guarded updates cannot
overwrite concurrent session replacements or tombstones.
"""

from __future__ import annotations

import logging
import subprocess
import time

from agent_procutil import no_window_flags

from .mux_mapping_registry import MuxMappingRegistry


def mux_attached_clients(mux_bin: str, session: str, timeout_s: float = 1.0) -> int | None:
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
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            creationflags=no_window_flags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.getLogger(__name__).warning("Mux attachment probe failed for %s: %s", session, exc)
        return None
    if result.returncode != 0:
        logging.getLogger(__name__).warning(
            "Mux attachment probe failed for %s with exit code %s", session, result.returncode
        )
        return None
    stdout = result.stdout or ""
    return len([line for line in stdout.splitlines() if line.strip()])


def refresh_attached_clients(
    registry: MuxMappingRegistry, current: dict, timeout_s: float = 1.0
) -> dict:
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
    observed = mux_attached_clients(current["mux_bin"], current["mux_session"], timeout_s)
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


class AttachedClientObserver:
    """Round-robin observation with at most two probes and a shared
    two-second budget per observation cycle. Keep the cursor on the resident
    runtime so large fleets converge over successive cycles without
    increasing the added drain latency with the number of mappings.
    Attempt cadence is independent of monitor availability or publication
    success and begins 20 seconds after the previous attempt completes."""

    def __init__(self) -> None:
        self._last_key: tuple[str, str] | None = None
        self._next_attempt_at = 0.0

    def observe(self, registry: MuxMappingRegistry) -> None:
        if time.monotonic() < self._next_attempt_at:
            return
        try:
            self._observe_cycle(registry)
        finally:
            self._next_attempt_at = time.monotonic() + 20.0

    def _observe_cycle(self, registry: MuxMappingRegistry) -> None:
        entries = [
            (key, entry)
            for key, entry in sorted(registry.snapshot().items())
            if entry["live"]
        ]
        if not entries:
            return
        start = next(
            (i for i, (key, _) in enumerate(entries) if self._last_key is None or key > self._last_key),
            0,
        )
        deadline = time.monotonic() + 2.0
        for offset in range(min(2, len(entries))):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            key, entry = entries[(start + offset) % len(entries)]
            refresh_attached_clients(registry, entry, timeout_s=min(1.0, remaining))
            self._last_key = key
