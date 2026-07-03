"""Durable cutover breadcrumb + stale-cutover recovery (#1756).

The cutover orchestrator runs in the short-lived ``agent-bridge deploy``
process, *separate* from the daemons it manipulates. If that process dies after
it has opened the old daemon's drain gate but before it retires the old daemon
(new daemon never adopts, orchestrator crashes, operator Ctrl-C's the deploy),
nothing in :meth:`CutoverOrchestrator.run` gets to roll back -- the old daemon
is left ``draining=true`` forever, refusing all new work, with **no record** of
why it is drained.

The breadcrumb closes that gap. It is a small JSON file written *before* the
drain gate is ever touched and updated at each phase, so an aborted cutover
leaves a durable, attributable trace on disk (which cutover, when, old/new
ports). :func:`recover_stale_cutover` reads it and undrains the stranded
survivor so the daemon does not stay closed to new sessions.

File layout (``<config_dir>/cutover.json``)::

    {
      "state": "draining",              # started|flipped|draining|
                                        # committed|rolled_back|failed
      "started_at": "2026-07-02T22:40:00Z",
      "updated_at": "2026-07-02T22:41:03Z",
      "pid": 12345,                     # the deploy orchestrator pid
      "old": {"bind": "127.0.0.1", "port": 9281},
      "new_port": 9282,
      "error": null
    }

A breadcrumb in a **non-terminal** state (``started``/``flipped``/``draining``)
marks an in-progress *or aborted* cutover: if the orchestrator were still alive
it would have advanced the state, so finding one on disk after the deploy
process is gone means the cutover aborted mid-flight.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("zdd")

_BREADCRUMB_FILENAME = "cutover.json"

# States from which the orchestrator would still advance if it were alive; a
# breadcrumb left in one of these is an aborted cutover.
_NON_TERMINAL = frozenset({"started", "flipped", "draining"})
_TERMINAL = frozenset({"committed", "rolled_back", "failed"})


def breadcrumb_path(config_dir: str | os.PathLike[str]) -> Path:
    """Absolute path of the cutover breadcrumb inside ``config_dir``."""
    return Path(config_dir) / _BREADCRUMB_FILENAME


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_breadcrumb(config_dir: str | os.PathLike[str]) -> dict | None:
    """Read and parse the cutover breadcrumb, or ``None`` if absent/unreadable."""
    path = breadcrumb_path(config_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        log.warning("Cutover breadcrumb at %s is corrupt -- ignoring", path)
        return None
    return data if isinstance(data, dict) else None


def write_breadcrumb(
    config_dir: str | os.PathLike[str],
    *,
    state: str,
    old: dict | None = None,
    new_port: int | None = None,
    pid: int | None = None,
    error: str | None = None,
    started_at: str | None = None,
) -> dict:
    """Write/update the cutover breadcrumb atomically. Returns the record.

    ``started_at`` is preserved across updates (pass the value from the initial
    ``started`` record); ``updated_at`` is always refreshed.
    """
    record = {
        "state": state,
        "started_at": started_at or _now_iso(),
        "updated_at": _now_iso(),
        "pid": pid if pid is not None else os.getpid(),
        "old": old,
        "new_port": new_port,
        "error": error,
    }
    _atomic_write(breadcrumb_path(config_dir), record)
    return record


def clear_breadcrumb(config_dir: str | os.PathLike[str]) -> bool:
    """Remove the breadcrumb (a cutover completed cleanly). Returns True if removed."""
    path = breadcrumb_path(config_dir)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def is_stale(record: dict | None) -> bool:
    """True when ``record`` marks an aborted (non-terminal) cutover."""
    if not record:
        return False
    return str(record.get("state")) in _NON_TERMINAL


def recover_stale_cutover(
    config_dir: str | os.PathLike[str],
    make_client: Callable[[str], Any],
    *,
    health_check: Callable[[str, int], bool] | None = None,
) -> dict:
    """Undrain a survivor stranded by an aborted cutover (#1756).

    Reads the breadcrumb; if it marks an aborted cutover (non-terminal state)
    and the old endpoint it names is still reachable, calls ``undrain`` on that
    survivor so it stops refusing new work, then marks the breadcrumb
    ``rolled_back``. A terminal or absent breadcrumb is a no-op.

    ``make_client(base_url)`` returns an object with an ``undrain()`` method
    (the same client protocol the orchestrator uses). ``health_check(host,
    port)`` is an optional liveness probe; when omitted the undrain is simply
    attempted and its failure tolerated.

    Returns a summary dict: ``{"recovered": bool, "reason": str, ...}``.
    """
    record = read_breadcrumb(config_dir)
    if not is_stale(record):
        return {"recovered": False, "reason": "no stale cutover breadcrumb"}

    old = record.get("old") if isinstance(record.get("old"), dict) else None
    if not old:
        # Nothing to undrain (drain gate was never opened before the abort).
        write_breadcrumb(
            config_dir, state="rolled_back",
            old=None, new_port=record.get("new_port"),
            error=record.get("error") or "aborted before drain",
            started_at=record.get("started_at"),
        )
        return {
            "recovered": False,
            "reason": "aborted before drain gate opened; nothing to undrain",
        }

    bind = str(old.get("bind") or "127.0.0.1")
    host = "127.0.0.1" if bind in ("0.0.0.0", "", "::") else bind
    if bind == "::":
        host = "::1"
    try:
        port = int(old["port"])
    except (KeyError, TypeError, ValueError):
        return {"recovered": False, "reason": "breadcrumb old endpoint invalid"}

    if health_check is not None and not health_check(host, port):
        # The survivor is gone -- nothing to heal; retire the breadcrumb so we
        # do not keep retrying a dead endpoint.
        write_breadcrumb(
            config_dir, state="rolled_back", old=old,
            new_port=record.get("new_port"),
            error=(record.get("error") or "aborted") + "; survivor unreachable",
            started_at=record.get("started_at"),
        )
        return {
            "recovered": False,
            "reason": f"survivor {host}:{port} unreachable; breadcrumb retired",
            "old_port": port,
        }

    base_url = f"http://{host}:{port}"
    try:
        make_client(base_url).undrain()
        undrained = True
        reason = f"undrained stranded survivor {host}:{port}"
        log.warning(
            "Recovered aborted cutover: undrained stranded survivor %s:%d "
            "(cutover started %s)", host, port, record.get("started_at"),
        )
    except Exception as exc:  # noqa: BLE001 -- recovery is best-effort
        undrained = False
        reason = f"undrain of {host}:{port} failed: {exc}"
        log.warning("Stale-cutover recovery could not undrain %s:%d: %s",
                    host, port, exc)

    write_breadcrumb(
        config_dir, state="rolled_back", old=old,
        new_port=record.get("new_port"),
        error=record.get("error") or "aborted cutover recovered",
        started_at=record.get("started_at"),
    )
    return {
        "recovered": undrained,
        "reason": reason,
        "old_port": port,
    }
