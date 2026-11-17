"""Read-only pending-handoff lookup (#3307 Phase 8, visions/mux-companion),
plus the explicit, on-demand cutover trigger
(mux-companion-manual-cutover-diagnostics, #4369).

Split out of ``engine_client.py`` (module-size cap): resolving and reading a
worktree's pending context-handoff baton is a cohesive, separable concern
from that module's general ``--json`` verb plumbing, even though it reuses
``engine_client``'s own process-boundary ``_run``/``run_json`` helpers and
``EngineError``.
"""

from __future__ import annotations

import json
from pathlib import Path

from .engine_client import EngineError, _run, run_json


def worktree_state_dir(
    project: str | None,
    worktree_path: str,
    *,
    runner=None,
) -> str | None:
    """Resolve a worktree's machine-local state directory (outside the repo
    checkout) through the engine's ``get worktree-state-dir`` verb -- a plain-
    text (non-``--json``) value that infers its target from the process's own
    cwd, so this runs the engine FROM ``worktree_path``. Returns ``None`` on
    any resolution failure (unadopted/untracked path, engine unreachable)."""
    try:
        raw = _run(project, ["get", "worktree-state-dir"], cwd=worktree_path,
                   runner=runner)
    except EngineError:
        return None
    text = raw.strip()
    return text or None


def pending_handoff(
    project: str | None,
    worktree_path: str,
    *,
    runner=None,
) -> dict | None:
    """The most-recent, still-unconsumed context-handoff baton stored for the
    worktree at ``worktree_path``, or ``None`` -- read-only situational
    awareness for the Mux Companion (visions/mux-companion
    §session-lineage/companion-reads-handoff-schema-never-drives-it).

    Reads context-handoff's own file-backed schema directly (``kind ==
    "context-handoff"``, a ``consumed`` flag, a ``title``) under
    ``<worktree-state-dir>/handoff/*.json`` -- the SAME directory
    context-handoff's own ``saveFileHandoff``/``handoffDirFor`` write to and
    resolve via this identical engine verb. This is a pure read of an
    already-durable file, never a decode/consume/cutover action, and never an
    ``import`` of the context-handoff plugin -- only the engine's own
    ``worktree-state-dir`` resolution is reused, keeping the process-boundary
    rule ``engine_client``'s own module docstring states. Never raises: any
    resolution or parse failure degrades to ``None`` (the Companion simply
    omits the section)."""
    state_dir = worktree_state_dir(project, worktree_path, runner=runner)
    if not state_dir:
        return None
    handoff_dir = Path(state_dir) / "handoff"
    try:
        candidates = list(handoff_dir.glob("handoff-*.json"))
    except OSError:
        return None
    best: dict | None = None
    for f in candidates:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("kind") != "context-handoff":
            continue
        if data.get("consumed"):
            continue
        created = str(data.get("createdAt") or "")
        if best is None or created > str(best.get("createdAt") or ""):
            best = data
    if best is None:
        return None
    return {
        "title": best.get("title") or "",
        "createdAt": best.get("createdAt"),
        "sessionId": best.get("sessionId"),
    }


def trigger_cutover(
    project: str | None,
    worktree_id: str,
    *,
    runner=None,
) -> dict:
    """Explicit, on-demand invocation of ``agent-worktrees``'
    ``handoff-cutover-trigger`` verb for ONE worktree
    (mux-companion-manual-cutover-diagnostics, #4369) -- the manual-
    cutover-trigger Feature (visions/mux-companion). This IS the human
    action (the Companion's "Cut over" button); there is no separate
    read-only preview to call first. Returns the engine's own
    ``{worktree_id, head_before, head_after, session_count_before,
    session_count_after, changed}`` envelope. Raises :class:`EngineError`
    on any resolution failure (unadopted worktree, engine unreachable) --
    the Companion surfaces that as a visible error, never silently."""
    return run_json(
        project,
        ["handoff-cutover-trigger", "--worktree-id", worktree_id, "--json"],
        runner=runner,
    )
