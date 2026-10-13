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
      "new_pid": 12401,                 # the spawn_passive daemon's own pid
      "error": null
    }

A breadcrumb in a **non-terminal** state (``started``/``flipped``/``draining``)
marks an in-progress *or aborted* cutover: if the orchestrator were still alive
it would have advanced the state, so finding one on disk after the deploy
process is gone means the cutover aborted mid-flight.

**The matching gap for the *new* daemon (the downstream tracker).** The breadcrumb
above was designed to heal the stranded *old* survivor. It does not, by itself,
help the *new* passive daemon ``spawn_passive`` just stood up: if the
orchestrator dies before that passive is ever promoted (flipped to ``active``),
the passive never becomes anyone's "old" and self-retire's active-ness gate
never arms for it -- it lingers indefinitely holding a port/pid. ``new_pid``
closes that gap: it is recorded the moment the passive is spawned (before the
flip is even attempted), so a breadcrumb that outlives its orchestrator names
both survivors -- the old one to undrain, and the new one to reap if it was
never promoted. See :func:`reap_abandoned_passive`.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from zdd.routing import format_authority

log = logging.getLogger("zdd")

_BREADCRUMB_FILENAME = "cutover.json"

# States from which the orchestrator would still advance if it were alive; a
# breadcrumb left in one of these is an aborted cutover.
_NON_TERMINAL = frozenset({
    "started", "flipped", "draining",
    "publication-uncertain",
    "conditional-started", "conditional-flipped", "conditional-draining",
    "conditional-publication-uncertain",
})
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


def recovery_refusal(config_dir: str | os.PathLike[str], record: dict | None) -> str | None:
    """Shared refusal for existing custody that legacy paths cannot interpret."""
    if requires_conditional_recovery(record):
        return "conditional cutover requires owner-led recovery"
    if record is not None and record.get("state") == "publication-uncertain":
        return "publication commit uncertain; owner-led recovery required"
    if (
        record is None and breadcrumb_path(config_dir).exists()
    ) or (
        record is not None and record.get("state") not in _NON_TERMINAL | _TERMINAL
    ):
        return "recovery state cannot be verified; owner-led recovery required"
    return None


def write_breadcrumb(
    config_dir: str | os.PathLike[str],
    *,
    state: str,
    old: dict | None = None,
    new_port: int | None = None,
    new_pid: int | None = None,
    pid: int | None = None,
    error: str | None = None,
    started_at: str | None = None,
    publication_witness: str | None = None,
    new_process_start_time: str | None = None,
) -> dict:
    """Write/update the cutover breadcrumb atomically. Returns the record.

    ``started_at`` is preserved across updates (pass the value from the initial
    ``started`` record); ``updated_at`` is always refreshed.

    ``new_pid`` is the pid of the passive daemon ``spawn_passive`` stood up for
    this cutover (recorded as soon as it is known -- before the health gate,
    flip, or drain even begin) so :func:`reap_abandoned_passive` can find and
    retire it later if it is never promoted. Callers should thread the same
    ``new_pid`` through every subsequent update for this cutover attempt (like
    ``started_at``); omitting it on a later call clears it, which is only
    correct once the cutover has resolved one way or the other.
    """
    record = {
        "state": state,
        "started_at": started_at or _now_iso(),
        "updated_at": _now_iso(),
        "pid": pid if pid is not None else os.getpid(),
        "old": old,
        "new_port": new_port,
        "new_pid": new_pid,
        "error": error,
        "publication_witness": publication_witness,
        "new_process_start_time": new_process_start_time,
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


def requires_conditional_recovery(record: dict | None) -> bool:
    """Conditional attempts cannot be resolved by legacy undrain/reap."""
    return bool(record and str(record.get("state", "")).startswith("conditional-"))


def refusal_reason(outcome: dict) -> str:
    """Report malformed refusal evidence explicitly rather than crashing a CLI."""
    reason = outcome.get("reason")
    if isinstance(reason, str) and reason.strip():
        return reason
    message = "native recovery refused without a valid reason; outcome contract invalid"
    log.error(message)
    return message


def recover_stale_cutover(
    config_dir: str | os.PathLike[str],
    make_client: Callable[[str], Any],
    *,
    health_check: Callable[[str, int], bool] | None = None,
) -> dict:
    """Recover only while holding native transition authority, without waiting."""
    from .cutover_lock import CutoverLock, CutoverLockedError

    try:
        with CutoverLock(config_dir):
            return _recover_stale_cutover_locked(
                config_dir, make_client, health_check=health_check
            )
    except CutoverLockedError:
        log.error("Legacy recovery refused while native cutover authority is held")
        return {"recovered": False, "refused": True,
                "reason": "native cutover in progress; recovery refused"}
    except OSError as exc:
        reason = f"native recovery authority unavailable ({type(exc).__name__})"
        log.error(reason)
        return {"recovered": False, "refused": True, "reason": reason}


def _recover_stale_cutover_locked(
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

    This function only heals the *old* survivor. If the caller also wants the
    matching backstop for a never-promoted *new* passive (#5195), read the
    breadcrumb once and pass that same record to both this function and
    :func:`reap_abandoned_passive` -- this function rewrites the breadcrumb to
    a terminal state, after which ``reap_abandoned_passive``'s own staleness
    gate would see nothing to do.
    """
    record = read_breadcrumb(config_dir)
    refusal = recovery_refusal(config_dir, record)
    if refusal is not None:
        log.error("Legacy recovery refused: %s", refusal)
        return {"recovered": False, "refused": True,
                "reason": refusal}
    if not is_stale(record):
        return {"recovered": False, "reason": "no stale cutover breadcrumb"}

    old = record.get("old") if isinstance(record.get("old"), dict) else None
    if not old:
        # Nothing to undrain (drain gate was never opened before the abort).
        write_breadcrumb(
            config_dir, state="rolled_back",
            pid=record.get("pid"),
            old=None, new_port=record.get("new_port"),
            new_pid=record.get("new_pid"),
            publication_witness=record.get("publication_witness"),
            new_process_start_time=record.get("new_process_start_time"),
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
            pid=record.get("pid"),
            new_port=record.get("new_port"),
            new_pid=record.get("new_pid"),
            publication_witness=record.get("publication_witness"),
            new_process_start_time=record.get("new_process_start_time"),
            error=(record.get("error") or "aborted") + "; survivor unreachable",
            started_at=record.get("started_at"),
        )
        return {
            "recovered": False,
            "reason": f"survivor {host}:{port} unreachable; breadcrumb retired",
            "old_port": port,
        }

    base_url = f"http://{format_authority(host, port)}"
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
        pid=record.get("pid"),
        new_port=record.get("new_port"),
        new_pid=record.get("new_pid"),
        publication_witness=record.get("publication_witness"),
        new_process_start_time=record.get("new_process_start_time"),
        error=record.get("error") or "aborted cutover recovered",
        started_at=record.get("started_at"),
    )
    return {
        "recovered": undrained,
        "reason": reason,
        "old_port": port,
    }


# Default grace window before an abandoned passive is reaped (#5195): long
# enough that a genuinely in-flight cutover (spawn -> health-gate -> flip ->
# drain) is never touched -- the drain step alone can legitimately run for
# minutes on a busy daemon -- but short enough that a truly abandoned passive
# does not linger for hours before the next chance to heal it notices.
DEFAULT_ABANDONED_PASSIVE_GRACE_S = 600.0


def _record_age_seconds(record: dict, *, now: datetime | None = None) -> float | None:
    """Seconds since ``record["updated_at"]``, or ``None`` if unparseable.

    Conservative on the ambiguous axis: an unparseable/missing timestamp
    returns ``None`` so a destructive caller (like
    :func:`reap_abandoned_passive`) treats it as "cannot confirm age" rather
    than guessing.
    """
    updated = record.get("updated_at")
    if not isinstance(updated, str):
        return None
    try:
        ts = datetime.fromisoformat(updated)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - ts).total_seconds()


def reap_abandoned_passive(
    config_dir: str | os.PathLike[str],
    *,
    pid_alive: Callable[[int], bool],
    terminate: Callable[[int], bool],
    active_pid: int | None = None,
    grace_seconds: float = DEFAULT_ABANDONED_PASSIVE_GRACE_S,
    record: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Reap only under native transition authority with a fresh breadcrumb."""
    import time

    from . import routing
    from .cutover_lock import CutoverLock, CutoverLockedError

    try:
        with CutoverLock(config_dir):
            current = read_breadcrumb(config_dir)
            refusal = recovery_refusal(config_dir, current)
            if refusal is not None:
                log.error("Passive reap refused: %s", refusal)
                return {"reaped": False, "refused": True, "reason": refusal, "pid": None}
            if record is not None and (
                current is None or any(
                    current.get(key) != record.get(key)
                    for key in (
                        "pid", "started_at", "old", "new_pid", "new_port",
                        "publication_witness", "new_process_start_time",
                    )
                )
            ):
                reason = "cutover identity changed; stale passive-reap evidence refused"
                log.error(reason)
                return {"reaped": False, "refused": True, "reason": reason, "pid": None}
            with routing.routing_lock(config_dir, deadline=time.monotonic()):
                table = routing.read_table(config_dir, strict=True) or {}
                candidate = (record or current or {}).get("new_pid")
                candidate_port = (record or current or {}).get("new_port")
                for key in ("active", "previous"):
                    raw = table.get(key)
                    if raw is not None and not isinstance(raw, dict):
                        raise ValueError("routing entry is not an object")
                    endpoint = routing.Endpoint.from_dict(raw) if raw else None
                    if raw and endpoint is None:
                        raise ValueError("routing entry identity is invalid")
                    if endpoint is not None and (
                        (candidate is not None and endpoint.pid == candidate)
                        or endpoint.port == candidate_port
                    ):
                        return {
                            "reaped": False,
                            "reason": "recorded passive pid is published -- it was promoted",
                            "pid": candidate,
                        }
                return _reap_abandoned_passive_locked(
                    config_dir, pid_alive=pid_alive, terminate=terminate,
                    active_pid=active_pid, grace_seconds=grace_seconds,
                    record=record, now=now,
                )
    except CutoverLockedError:
        log.error("Passive reap refused while native cutover authority is held")
        return {"reaped": False, "refused": True,
                "reason": "native cutover in progress; reap refused", "pid": None}
    except (OSError, ValueError) as exc:
        reason = f"native routing authority unavailable; reap refused ({type(exc).__name__})"
        log.error(reason)
        return {"reaped": False, "refused": True, "reason": reason, "pid": None}


def _reap_abandoned_passive_locked(
    config_dir: str | os.PathLike[str],
    *,
    pid_alive: Callable[[int], bool],
    terminate: Callable[[int], bool],
    active_pid: int | None = None,
    grace_seconds: float = DEFAULT_ABANDONED_PASSIVE_GRACE_S,
    record: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Terminate a passive daemon spawned by an abandoned cutover (the downstream tracker).

    ``spawn_passive`` stands the new daemon up *before* the breadcrumb can
    prove it was ever promoted. If the orchestrator process itself dies --
    crash, ``kill -9``, an operator Ctrl-C -- before the flip (or dies after
    the flip but before the commit point, in a way :func:`recover_stale_cutover`
    cannot reconcile because the *old* survivor is also gone), the freshly
    spawned passive never becomes ``active``. Self-retire's active-ness gate
    only ever arms for a coordinator that has observed its own pid as
    ``active``, so a never-promoted passive arms nothing and lingers
    indefinitely holding a port/pid. :func:`recover_stale_cutover` already
    undrains the stranded *old* survivor; this is the matching backstop for
    the stranded *new* passive.

    Deliberately conservative -- every ambiguous case is a no-op:

    - no breadcrumb, or a terminal one (:func:`is_stale` is ``False``),
    - the breadcrumb has no recorded ``new_pid`` (a pre-fix breadcrumb, or a
      cutover that aborted before ``spawn_passive`` ever ran),
    - the breadcrumb is younger than ``grace_seconds`` (a genuinely in-flight
      cutover -- most importantly its own drain step -- must never be
      disturbed),
    - ``new_pid`` matches ``active_pid`` (the caller's own confirmed-live
      routing-table ``active`` pid): it *was* promoted, so it is not
      abandoned and must never be touched,
    - ``pid_alive(new_pid)`` is falsy (already gone -- nothing to do).

    The birth-time process token and pre-spawn publication witness must both
    remain verifiable. ``terminate`` must additionally bind the final mutation
    to that exact process using a native handle; preflight alone is not a fence.

    Callers must pass the **same breadcrumb record** they read before calling
    :func:`recover_stale_cutover` (via ``record=``) rather than letting this
    function re-read the file -- ``recover_stale_cutover`` rewrites the
    breadcrumb to a terminal state, which would make this function's own
    staleness check see nothing to do.

    Returns a summary dict: ``{"reaped": bool, "reason": str, "pid": int | None}``.
    """
    rec = record if record is not None else read_breadcrumb(config_dir)
    if requires_conditional_recovery(rec):
        log.error("Conditional cutover requires owner-led recovery; passive reap refused")
        return {"reaped": False, "refused": True,
                "reason": "conditional cutover requires owner-led recovery", "pid": None}
    if not is_stale(rec):
        return {"reaped": False, "reason": "no stale cutover breadcrumb", "pid": None}
    new_pid = rec.get("new_pid")
    if not isinstance(new_pid, int) or new_pid <= 0:
        return {
            "reaped": False,
            "reason": "breadcrumb has no recorded passive pid",
            "pid": None,
        }
    if active_pid is not None and new_pid == active_pid:
        return {
            "reaped": False,
            "reason": "recorded passive pid is the current active -- it was promoted",
            "pid": new_pid,
        }
    age = _record_age_seconds(rec, now=now)
    if age is None or age < grace_seconds:
        return {
            "reaped": False,
            "reason": "breadcrumb too fresh -- cutover may still be in flight",
            "pid": new_pid,
        }
    if not pid_alive(new_pid):
        return {"reaped": False, "reason": "recorded passive pid is not alive", "pid": new_pid}
    from . import diagnostics, publication_history, routing

    table = routing.read_table(config_dir, strict=True) or {}
    start = rec.get("new_process_start_time")
    if (
        not isinstance(start, str) or not start
        or diagnostics.process_start_time(new_pid) != start
        or not publication_history.never_published(
            table, rec.get("publication_witness"), pid=new_pid, port=rec.get("new_port"),
        )
    ):
        reason = "candidate promoted or publication history unverified; owner-led recovery required"
        log.error(reason)
        return {"reaped": False, "refused": True, "reason": reason, "pid": new_pid}
    try:
        ok = terminate(new_pid)
        if type(ok) is not bool:
            reason = "candidate termination returned invalid exit evidence; reap refused"
            log.error(reason)
            return {"reaped": False, "refused": True, "reason": reason, "pid": new_pid}
    except Exception as exc:  # noqa: BLE001 -- reap is best-effort, never raised
        reason = f"identity-bound candidate termination failed ({type(exc).__name__})"
        log.error(reason)
        return {"reaped": False, "refused": True, "reason": reason, "pid": new_pid}
    if ok:
        log.warning(
            "Reaped abandoned passive pid=%d from a cutover started %s (age=%.0fs, "
            "never promoted)", new_pid, rec.get("started_at"), age,
        )
    return {
        "reaped": ok,
        **({"refused": True} if not ok else {}),
        "reason": "terminated" if ok else "terminate reported failure",
        "pid": new_pid,
    }
