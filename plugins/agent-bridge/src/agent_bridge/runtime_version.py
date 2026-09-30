"""Emit a ``running-version.json`` marker for the launch-path reconciler.

``copilot plugin update`` bumps the *installed plugin* (payload) but does **not**
restart the deployed daemon, so agent-bridge can silently keep serving an older
build than its plugin -- the exact incident that motivated dotfiles #533 (a
``plugin update`` advanced the payload while the running daemon kept serving the
old runtime until a manual ``agent-bridge service restart``). The reconciler
(agent-worktrees ``reconcile.py``) compares the payload version against the
runtime's on-disk ``deploy-manifest.json``, which can match the payload while the
*running* daemon still lags. Emitting the actually-imported version on boot gives
the reconciler a truthful running-version signal.

The file is distinct from ``deploy-manifest.json`` (installer-owned):
``{"version", "pid", "started_at"[, "generation_id"]}``. A reader treats a
**dead pid** (or a missing file) as *no running version* and falls back to the
on-disk manifest, so this is purely additive and safe.

``generation_id``: the daemon's own
:class:`~agent_bridge.session_manager.SessionManager` computes its true,
opaque generation id (``zdd.claims.generation_id``, a
``version-pid-started_at`` string with microsecond timestamp precision) once
per process; independently recomputing it from outside the process isn't
possible (the timestamp component is minted at an arbitrary instant during
startup), so it must be read back from wherever the process itself recorded
it. Deliberately NOT exposed via the ``/health`` HTTP endpoint: that endpoint
is a registered wire-protocol contract
(``plugins/agent-bridge/contract/registry.json``), and any field there needs
a captured fixture tied to the exact commit it was captured from, which this
local, non-contract, best-effort marker file has no such constraint on. It
already exists for exactly this class of "truthful running-state signal"
problem and is read locally, never over HTTP.

A ``--passive`` ZDD-cutover successor complicates this: it must NOT write its
own id straight into the shared canonical marker at boot, because it isn't
promoted yet -- if the cutover then aborts, the marker would be left
permanently pairing the surviving active daemon's own pid/version with an
abandoned generation's id. Such a process instead stages its id in a
separate, PID-keyed file (:func:`stage_pending_generation_id`); only
``_reconcile_service_marker`` (called with a CONFIRMED-promoted pid) ever
consumes a staged entry (:func:`consume_pending_generation_id`) into the
canonical marker. An aborted/never-promoted passive's staged entry is simply
never consumed and is pruned the next time anything stages a new one.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .install_paths import effective_config_dir

RUNNING_VERSION_FILE = "running-version.json"
PENDING_GENERATION_IDS_FILE = "pending-generation-ids.json"


def install_dir() -> Path:
    """Runtime root for the current daemon process."""
    return effective_config_dir()


def write_running_version(
    directory: Path | None = None,
    *,
    pid: int | None = None,
    version: str | None = None,
    generation_id: str | None = None,
) -> None:
    """Record the running daemon's version + pid on boot (best-effort).

    On the normal boot path both ``pid`` and ``version`` default to *this*
    process (``os.getpid()`` / ``__version__``). The cutover reconciler passes
    them explicitly to point the marker at the freshly cut-over daemon, whose pid
    differs from the deploy process's and which -- being a relay-disabled passive
    -- never wrote its own marker (dotfiles #533 caveat #1).

    ``generation_id`` is optional here because the caller that fires this on
    the normal boot path (``app.py``'s ``lifespan()``) runs BEFORE the
    ``SessionManager`` that actually computes it exists yet -- see
    :func:`set_running_generation_id` for the follow-up call that adds it
    once available, without re-stamping ``pid``/``version``/``started_at``.
    The cutover-promotion caller (``_reconcile_service_marker``) instead
    passes through whatever ``generation_id`` it consumed for the CONFIRMED
    promoted pid (see :func:`consume_pending_generation_id`), so a full
    rewrite here never silently drops it.

    Never raises: a write failure only degrades the reconciler's running-version
    signal (it falls back to the on-disk manifest), never the daemon.
    """
    d = directory or install_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": version or __version__,
            "pid": pid if pid is not None else os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        if generation_id is not None:
            payload["generation_id"] = generation_id
        (d / RUNNING_VERSION_FILE).write_text(
            json.dumps(payload), encoding="utf-8"
        )
    except OSError:
        pass


def set_running_generation_id(
    generation_id: str, directory: Path | None = None
) -> None:
    """Merge the daemon's real ``generation_id`` into the marker written by
    :func:`write_running_version`, once the owning ``SessionManager`` exists
    and has actually computed it. Only ever called for a process that IS
    (or is about to become) the canonical running daemon -- see
    :func:`stage_pending_generation_id` for a not-yet-promoted passive.

    A read-merge-write onto the EXISTING marker (preserving whatever
    ``pid``/``version``/``started_at`` the earlier boot-time write recorded)
    rather than a second full :func:`write_running_version` call, so this
    never contradicts the earlier record. If the marker doesn't exist yet,
    is unreadable, or isn't even a JSON object (a malformed/legacy/partial
    write), starts a fresh one with this process's own defaults instead of
    raising on the merge -- still best-effort, never raises.
    """
    d = directory or install_dir()
    path = d / RUNNING_VERSION_FILE
    try:
        payload = None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if not isinstance(payload, dict):
            payload = {
                "version": __version__,
                "pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        payload["generation_id"] = generation_id
        d.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def record_manager_generation_id(session_manager, directory: Path | None = None) -> None:
    """``app.py``'s ``lifespan()`` call site for a NORMAL (non-passive) boot
    -- reads the just-constructed ``SessionManager``'s own real
    ``_generation_id`` and, if present, records it straight into the
    canonical marker via :func:`set_running_generation_id`. Never raises.
    """
    gen_id = getattr(session_manager, "_generation_id", None)
    if gen_id:
        set_running_generation_id(gen_id, directory)


def stage_manager_generation_id(session_manager, directory: Path | None = None) -> None:
    """``app.py``'s ``lifespan()`` call site for a ``--passive`` ZDD-cutover
    successor's boot -- stages the just-constructed ``SessionManager``'s own
    real ``_generation_id`` under this process's own pid via
    :func:`stage_pending_generation_id`, WITHOUT touching the canonical
    marker (see that function's docstring for why). Never raises.
    """
    gen_id = getattr(session_manager, "_generation_id", None)
    if gen_id:
        stage_pending_generation_id(os.getpid(), gen_id, directory)


def _load_pending(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _prune_dead_pids(pending: dict[str, str]) -> dict[str, str]:
    from .session_host.osutil import pid_alive

    alive = {}
    for pid_str, gen_id in pending.items():
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if pid_alive(pid):
            alive[pid_str] = gen_id
    return alive


def stage_pending_generation_id(
    pid: int, generation_id: str, directory: Path | None = None
) -> None:
    """A ``--passive`` ZDD-cutover successor's own boot-time call: stage its
    real ``generation_id`` keyed by its OWN pid, WITHOUT touching the shared
    canonical ``running-version.json`` marker (it isn't promoted yet, and
    may never be). Opportunistically prunes entries for pids that are no
    longer alive (an earlier aborted/retired passive) so this file never
    grows unbounded across many cutover attempts. Never raises.
    """
    d = directory or install_dir()
    path = d / PENDING_GENERATION_IDS_FILE
    try:
        pending = _prune_dead_pids(_load_pending(path))
        pending[str(pid)] = generation_id
        d.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(pending), encoding="utf-8")
    except OSError:
        pass


def consume_pending_generation_id(
    pid: int, directory: Path | None = None
) -> str | None:
    """``_reconcile_service_marker``'s call site, at CONFIRMED-promotion time:
    pop (read once, then remove) the specific ``pid``'s staged generation id,
    or ``None`` if nothing was ever staged for it. Consumed exactly once so a
    stale/reused entry can never be replayed onto a later, unrelated pid.
    Never raises.
    """
    d = directory or install_dir()
    path = d / PENDING_GENERATION_IDS_FILE
    try:
        pending = _load_pending(path)
        gen_id = pending.pop(str(pid), None)
        if gen_id is not None:
            path.write_text(json.dumps(pending), encoding="utf-8")
        return gen_id
    except OSError:
        return None
