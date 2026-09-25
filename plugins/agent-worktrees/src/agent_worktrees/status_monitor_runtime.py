"""Resident monitor runtime helpers extracted from ``__main__``."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from agent_procutil import windowless_python

from . import activity, locks, sessions_pane_retire, tracking
from . import config as cfg
from . import status_updater_cli


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "reconcile-sessions",
        help="Run one bounded record/session/projection reconciliation pass",
    )
    p.add_argument("--record-budget", type=int, default=16)
    p.add_argument("--session-budget", type=int, default=32)
    p.add_argument("--projection-budget", type=int, default=16)
    sub.add_parser(
        "status-monitor-restart",
        help="Reap a superseded status-monitor and spawn the current-runtime "
        "one (invoked by the auto-update cutover so a deploy never leaves "
        "live sessions' status bars frozen)",
    )


_STATUS_MONITOR_ENV = "AGENT_WORKTREES_STATUS_MONITOR"
_STATUS_MONITOR_HANDOFF_CLAIM_STALE_SECONDS_ENV = (
    "AGENT_WORKTREES_STATUS_MONITOR_HANDOFF_CLAIM_STALE_SECONDS"
)
_STATUS_MONITOR_HANDOFF_CLAIM_STALE_SECONDS_DEFAULT = 180.0


def _status_monitor_enabled() -> bool:
    """Whether the resident coalescing monitor is active (default-ON / opt-out).

    Enabled unless ``AGENT_WORKTREES_STATUS_MONITOR`` is explicitly falsy
    (``0``/``false``/``no``/``off``), in which case each session runs its own
    per-session ``status-updater`` (the a-la-carte inline fallback). Mirrors the
    self-retire opt-out convention.
    """
    return os.environ.get(_STATUS_MONITOR_ENV, "").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _aw_runtime_home() -> Path:
    """The selected legacy or installation-cell runtime/state root."""
    override = _core_helper("_aw_runtime_home", _aw_runtime_home)
    if override is not _aw_runtime_home:
        return override()
    return cfg.install_dir()


def _monitor_lock_path() -> Path:
    override = _core_helper("_monitor_lock_path", _monitor_lock_path)
    if override is not _monitor_lock_path:
        return override()
    return _aw_runtime_home() / "status-monitor.lock"


def _monitor_registry_dir() -> Path:
    override = _core_helper("_monitor_registry_dir", _monitor_registry_dir)
    if override is not _monitor_registry_dir:
        return override()
    return _aw_runtime_home() / "status-monitor.d"


def _monitor_handoff_claim_root() -> Path:
    override = _core_helper("_monitor_handoff_claim_root", _monitor_handoff_claim_root)
    if override is not _monitor_handoff_claim_root:
        return override()
    return _aw_runtime_home() / "status-monitor-handoffs.d"


def _monitor_handoff_claim_stale_seconds() -> float:
    raw = os.environ.get(_STATUS_MONITOR_HANDOFF_CLAIM_STALE_SECONDS_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return _STATUS_MONITOR_HANDOFF_CLAIM_STALE_SECONDS_DEFAULT


def _monitor_handoff_claim_segment(value: str | None, fallback: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(value or "")).strip("._-")
    return safe[:160] or fallback


def _monitor_handoff_claim_path(worktree_id: str | None, token: str | None) -> Path:
    return (
        _monitor_handoff_claim_root()
        / _monitor_handoff_claim_segment(worktree_id, "unknown-worktree")
        / f"{_monitor_handoff_claim_segment(token, 'unknown-handoff')}.json"
    )


def _monitor_handoff_claim_created_at(
    path: Path,
    claim: dict[str, object] | None,
) -> datetime | None:
    value = claim.get("created_at") if isinstance(claim, dict) else None
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _monitor_handoff_claim_staleness(
    path: Path,
    claim: dict[str, object] | None = None,
) -> dict[str, object]:
    claim = claim if isinstance(claim, dict) else locks.read_lock(path)
    created_at = _monitor_handoff_claim_created_at(path, claim)
    age_seconds = (
        max(
            0.0,
            (datetime.now(timezone.utc) - created_at).total_seconds(),
        )
        if created_at is not None
        else None
    )
    if isinstance(claim, dict):
        pid = claim.get("pid")
        if isinstance(pid, int) and pid > 0:
            if not locks.pid_alive(pid):
                return {
                    "stale": True,
                    "reason": "pid-gone",
                    "age_seconds": age_seconds,
                    "claim": claim,
                }
            recorded = claim.get("start_time")
            if recorded:
                current = locks.process_start_time(pid)
                if current is not None and str(current) != str(recorded):
                    return {
                        "stale": True,
                        "reason": "pid-reused",
                        "age_seconds": age_seconds,
                        "claim": claim,
                    }
    if age_seconds is not None and age_seconds >= _monitor_handoff_claim_stale_seconds():
        return {
            "stale": True,
            "reason": "age-expired",
            "age_seconds": age_seconds,
            "claim": claim,
        }
    return {
        "stale": False,
        "reason": None,
        "age_seconds": age_seconds,
        "claim": claim,
    }


def _monitor_publish_handoff_cutover_claim(
    path: Path,
    payload: dict[str, object],
) -> tuple[bool, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False, None
        return True, None
    except OSError as exc:
        return False, str(exc)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _monitor_reclaim_stale_handoff_cutover_claim(
    path: Path,
    payload: dict[str, object],
    *,
    expected_stale_claim: dict[str, object] | None = None,
) -> tuple[bool, bool, str | None]:
    """Displace a stale claim at ``path`` and publish ``payload`` in its place.

    ``os.replace`` only guarantees exclusivity over the *name* ``path``, not
    over *which claim* is currently sitting there -- if a second racing
    reclaimer displaces ``path`` after a first reclaimer has already
    displaced-and-republished it (both had assessed the *same original*
    claim as stale, e.g. via a barrier synchronizing them at the staleness
    check), the second reclaimer's ``os.replace`` silently steals the
    first's brand-new, genuinely-live claim instead of the stale one either
    of them actually meant to evict -- both then report ``claimed=True``.
    When ``expected_stale_claim`` is given, verify the displaced content
    still matches it before publishing; on a mismatch (someone else's live
    claim was displaced instead), put it back and report "not reclaimed"
    rather than silently overwriting live work.
    """
    displaced = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.stale"
    )
    try:
        os.replace(path, displaced)
    except FileNotFoundError:
        return True, False, None
    except OSError as exc:
        return False, False, str(exc)
    if expected_stale_claim is not None:
        dc = locks.read_lock(displaced)
        match = dc == expected_stale_claim
        if not match:
            with contextlib.suppress(OSError):
                os.replace(displaced, path)
            return True, False, None
    try:
        claimed, error = _monitor_publish_handoff_cutover_claim(path, payload)
        return error is None, claimed, error
    finally:
        with contextlib.suppress(OSError):
            displaced.unlink()


def _monitor_claim_handoff_cutover(
    request: dict[str, object],
) -> dict[str, object]:
    """Atomically claim one handoff token so only one monitor pass can spawn it."""
    override = _core_helper("_monitor_claim_handoff_cutover", _monitor_claim_handoff_cutover)
    if override is not _monitor_claim_handoff_cutover:
        return override(request)
    token = str(request.get("token") or "").strip()
    worktree_id = str(request.get("worktree_id") or "").strip()
    path = _monitor_handoff_claim_path(worktree_id, token)
    owner_pid = os.getpid()
    payload = {
        "schema": 1,
        "kind": "handoff-cutover-claim",
        "token": token,
        "worktree_id": worktree_id or None,
        "session_id": str(request.get("predecessor_session_id") or "").strip() or None,
        "pid": owner_pid,
        "start_time": locks.process_start_time(owner_pid),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Disambiguates two claims that would otherwise be byte-for-byte
        # identical (same pid, same start_time, same second-granularity
        # timestamp -- routine for two threads/processes racing within the
        # same wall-clock second). Without it, a reclaimer verifying "did I
        # just displace the stale claim I inspected, or someone else's
        # fresh one" can't tell the two apart by content alone and may
        # wrongly treat a coincidental content match as a legitimate
        # target.
        "claim_nonce": secrets.token_hex(8),
    }

    def _acquired_response() -> dict[str, object]:
        log_data = {
            "worktree_id": worktree_id or None,
            "session_id": payload["session_id"],
            "source": "python",
            "handoff_token": token or None,
            "reason": "monitor-cutover",
            "outcome": "acquired",
        }
        if reclaim_reason is not None:
            log_data["claim_reclaimed"] = True
            log_data["stale_reason"] = reclaim_reason
            log_data["stale_age_seconds"] = (
                round(reclaim_age_seconds, 3)
                if reclaim_age_seconds is not None
                else None
            )
            if isinstance(prior_claim, dict):
                prior_pid = prior_claim.get("pid")
                if isinstance(prior_pid, int) and prior_pid > 0:
                    log_data["prior_pid"] = prior_pid
                prior_session = str(prior_claim.get("session_id") or "").strip()
                if prior_session:
                    log_data["prior_session_id"] = prior_session
        activity.log_event("handoff_cutover_claim", **log_data)
        return {"ok": True, "claimed": True, "path": str(path)}

    reclaim_reason = None
    reclaim_age_seconds = None
    prior_claim: dict[str, object] | None = None
    for _attempt in range(3):
        claimed, error = _monitor_publish_handoff_cutover_claim(path, payload)
        if error is not None:
            activity.log_event(
                "handoff_cutover_claim",
                worktree_id=worktree_id or None,
                session_id=payload["session_id"],
                source="python",
                handoff_token=token or None,
                reason="monitor-cutover",
                outcome="error",
                error=error,
            )
            return {
                "ok": False, "claimed": False, "path": str(path), "error": error,
            }
        if claimed:
            return _acquired_response()
        if not path.exists():
            continue
        staleness = _monitor_handoff_claim_staleness(path)
        if not staleness.get("stale"):
            break
        prior_claim = staleness.get("claim") if isinstance(staleness.get("claim"), dict) else None
        reclaim_reason = str(staleness.get("reason") or "").strip() or "unknown"
        reclaim_age_raw = staleness.get("age_seconds")
        reclaim_age_seconds = (
            float(reclaim_age_raw) if isinstance(reclaim_age_raw, (int, float)) else None
        )
        ok, reclaimed, reclaim_error = _monitor_reclaim_stale_handoff_cutover_claim(
            path, payload, expected_stale_claim=prior_claim,
        )
        if reclaim_error is not None:
            activity.log_event(
                "handoff_cutover_claim",
                worktree_id=worktree_id or None,
                session_id=payload["session_id"],
                source="python",
                handoff_token=token or None,
                reason="monitor-cutover",
                outcome="error",
                error=reclaim_error,
            )
            return {
                "ok": False,
                "claimed": False,
                "path": str(path),
                "error": reclaim_error,
            }
        if ok and reclaimed:
            return _acquired_response()
        reclaim_reason = None
        reclaim_age_seconds = None
        prior_claim = None
        break

    if path.exists():
        activity.log_event(
            "handoff_cutover_claim",
            worktree_id=worktree_id or None,
            session_id=payload["session_id"],
            source="python",
            handoff_token=token or None,
            reason="monitor-cutover",
            outcome="already-claimed",
        )
        return {"ok": True, "claimed": False, "path": str(path)}
    claimed, error = _monitor_publish_handoff_cutover_claim(path, payload)
    if error is not None:
        activity.log_event(
            "handoff_cutover_claim",
            worktree_id=worktree_id or None,
            session_id=payload["session_id"],
            source="python",
            handoff_token=token or None,
            reason="monitor-cutover",
            outcome="error",
            error=error,
        )
        return {"ok": False, "claimed": False, "path": str(path), "error": error}
    if claimed:
        return _acquired_response()
    activity.log_event(
        "handoff_cutover_claim",
        worktree_id=worktree_id or None,
        session_id=payload["session_id"],
        source="python",
        handoff_token=token or None,
        reason="monitor-cutover",
        outcome="already-claimed",
    )
    return {"ok": True, "claimed": False, "path": str(path)}


def _valid_monitor_session(sess: str) -> bool:
    """A safe ``wt-*`` session name usable as a registry filename.

    ``sess`` originates from the untrusted ``--session`` CLI arg, so it must be a
    bare ``wt-<id>`` token (alphanumerics + ``-._``) with no path separator,
    ``..``, or absolute component -- otherwise it could escape the registry dir
    (path traversal / absolute-path write). Every real session is
    ``wt-<worktree_id>``, so this allow-list costs nothing.
    """
    return (
        bool(sess)
        and sess.startswith("wt-")
        and ".." not in sess
        and all(c.isalnum() or c in "-._" for c in sess)
    )


def _register_session_for_monitor(sess: str, path: str | None) -> bool:
    """Record ``sess -> worktree path`` so the resident monitor can serve it.

    One file per session (``status-monitor.d/<sess>``), so concurrent session
    starts never contend on a shared document -- the registry doubles as the
    monitor's refcount.  Returns whether the entry was persisted; the caller
    falls back to the per-session updater when it was not, so a session is never
    left without a status bar.  Never raises.
    """
    if not path or not _valid_monitor_session(sess):
        return False
    import tempfile

    try:
        d = _monitor_registry_dir()
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=sess + ".", suffix=".tmp", dir=str(d))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(path))
        os.replace(tmp, str(d / sess))
        return True
    except OSError:
        return False


def _read_monitor_registry(reg_dir: Path) -> dict[str, str]:
    """Map ``session name -> worktree path`` from the registry dir (best-effort)."""
    out: dict[str, str] = {}
    try:
        for f in reg_dir.iterdir():
            if f.is_file() and not f.name.endswith(".tmp") and _valid_monitor_session(f.name):
                try:
                    out[f.name] = f.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
    except OSError:
        pass
    return out


def _remove_monitor_entry(reg_dir: Path, sess: str) -> None:
    try:
        (reg_dir / sess).unlink()
    except OSError:
        pass


def _windowless_python() -> str:
    """The interpreter for a detached daemon with no recurring console children.

    On Windows a venv ``python.exe`` is a *console* launcher: even started with
    ``DETACHED_PROCESS`` it re-launches the base interpreter as a child that
    allocates its OWN console, which Windows Terminal (DefTerm) then surfaces as
    a visible window/tab.
    ``pythonw.exe`` (the GUI-subsystem sibling) never allocates a console, so a
    fully-background daemon (DEVNULL stdio, no console I/O) stays truly
    windowless through the trampoline. Falls back to ``sys.executable`` when no
    sibling ``pythonw`` exists (non-standard layout) or off Windows. Daemons
    with recurring console descendants intentionally retain console Python
    under ``windowless_daemon_kwargs()`` instead, so those descendants inherit
    one hidden console tree.
    """
    return windowless_python(sys.executable)


def _spawn_detached(argv: list[str]) -> bool:
    """Spawn a survivable, windowless daemon rooted at HOME.

    Rooted at HOME (never the plugin payload dir): a detached child that keeps
    the payload dir as its cwd holds an open handle that blocks
    ``copilot plugin update`` on Windows.  Never raises.

    On Windows the console-subsystem interpreter is intentionally retained and
    launched under ``CREATE_NO_WINDOW``. Its periodic console children then
    inherit one hidden console tree instead of each allocating a Default
    Terminal host from a consoleless ``pythonw`` parent.
    """
    override = _core_helper("_spawn_detached", _spawn_detached)
    if override is not _spawn_detached:
        return override(argv)
    argv = list(argv)
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": os.path.expanduser("~"),
        "env": _core_helper("_background_environment", status_updater_cli._background_environment)(),
    }
    kwargs.update(_core().windowless_daemon_kwargs(breakaway=True))
    try:
        subprocess.Popen(argv, **kwargs)  # detached: fixed, trusted argv
        return True
    except Exception:
        return False


def _ensure_status_monitor() -> bool:
    """Start the resident monitor unless one is already live on the CURRENT
    runtime.  Returns whether a current monitor is believed running (already
    live, or freshly spawned) -- the caller falls back to the per-session updater
    when it is not.  Idempotent + cheap: a live, non-superseded monitor is a
    no-op; a superseded (older-runtime) one is left to self-retire while a
    current one is spawned to take over."""
    try:
        from . import locks as _locks

        data = _locks.read_lock(_monitor_lock_path())
        if _locks.lock_is_live(data) and isinstance(data, dict):
            other_prefix = data.get("prefix")
            superseded_fn = _core_helper("_runtime_superseded", status_updater_cli._runtime_superseded)
            if not other_prefix or not superseded_fn(prefix=other_prefix):
                import shutil

                caller_has_mux = bool(shutil.which("psmux") or shutil.which("tmux"))
                if data.get("mux") is not False or not caller_has_mux:
                    return True
    except Exception:
        pass
    return _spawn_detached([sys.executable, "-m", "agent_worktrees", "status-monitor"])


def _restart_status_monitor() -> dict:
    """Reap a superseded status-monitor and (re)spawn the current-runtime one.

    Called by the auto-update cutover (the installer, after it activates a new
    runtime slot) so a deploy never leaves live sessions' status bars frozen:
    the outgoing monitor self-retires on supersession, but only RESPAWNS on the
    next session start -- so a long-lived, un-restarted session's bar stays stuck
    until then (the observed "mux bar frozen after a deploy" bug). This closes
    the gap: reap the superseded monitor now and start the current one now, which
    then re-serves every live registered ``wt-*`` session's bar with no session
    restart needed.

    Best-effort; never raises. Returns a small status dict for logging. A no-op
    (no spawn) when the resident monitor is opted out
    (``AGENT_WORKTREES_STATUS_MONITOR=0``). Runs from the NEWLY-ACTIVATED slot's
    interpreter, so ``sys.executable`` / ``sys.prefix`` are the current runtime.
    """
    result: dict = {
        "enabled": _status_monitor_enabled(),
        "reaped": None,
        "spawned": False,
        "already_current": False,
    }
    if not result["enabled"]:
        return result
    try:
        from . import locks as _locks

        lock = _monitor_lock_path()
        data = _locks.read_lock(lock)
        if _locks.lock_is_live(data) and isinstance(data, dict):
            pid = data.get("pid")
            prefix = data.get("prefix")
            superseded = bool(prefix) and _core_helper("_runtime_superseded", status_updater_cli._runtime_superseded)(prefix=prefix)
            if pid == os.getpid():
                pass  # our own lock (shouldn't happen from the installer) -- ignore
            elif superseded and isinstance(pid, int):
                # The running monitor is on the outgoing slot: reap it now (it
                # would otherwise linger up to a full tick) and clear its lock so
                # the fresh monitor doesn't defer to a ghost owner.
                from . import procs as _procs

                if _procs.terminate_pid(pid):
                    result["reaped"] = pid
                _locks.remove_lock(lock)
            elif not superseded:
                # A CURRENT monitor already owns the host -- nothing to restart.
                result["already_current"] = True
                return result
        else:
            # Stale/dead lock -> clear it so the new monitor doesn't stand down
            # behind a ghost owner.
            _locks.remove_lock(lock)
    except Exception:
        pass
    result["spawned"] = _spawn_detached(
        [sys.executable, "-m", "agent_worktrees", "status-monitor"]
    )
    return result



def cmd_status_monitor_restart(args: argparse.Namespace) -> int:
    """``status-monitor-restart`` -- reap+respawn the resident monitor.

    The auto-update cutover seam (installer-invoked from the new slot). Prints a
    one-line summary; always exits 0 (advisory, never fails a deploy)."""
    r = _restart_status_monitor()
    if not r.get("enabled"):
        print("status-monitor: disabled (AGENT_WORKTREES_STATUS_MONITOR=0) -- skipped")
        return 0
    if r.get("already_current"):
        print("status-monitor: a current monitor already owns the host -- left as-is")
        return 0
    bits = []
    if r.get("reaped"):
        bits.append(f"reaped superseded pid {r['reaped']}")
    bits.append("spawned current monitor" if r.get("spawned") else "spawn failed")
    print("status-monitor: " + ", ".join(bits))
    return 0


def cmd_reconcile_sessions(args: argparse.Namespace) -> int:
    """Run one bounded record/session/projection reconciliation pass."""
    import shutil

    from . import session_catalog

    reconciler = session_catalog.ResidentSessionReconciler(
        record_budget=args.record_budget,
        session_budget=args.session_budget,
        projection_budget=args.projection_budget,
    )
    mux = "psmux" if shutil.which("psmux") else ("tmux" if shutil.which("tmux") else None)
    mux_bin = (shutil.which(mux) or mux) if mux else None
    if mux_bin is None:
        reconciler.observe_mux(set())
    else:
        live = _monitor_list_sessions(mux_bin)
        if live is not None:
            reconciler.observe_mux(set(live))
    report = reconciler.step()
    report["mux_observed"] = reconciler.has_mux_observation
    _core()._json_output(report)
    return 0


def _monitor_mux_set(mux_bin: str, sess: str, opt: str, val: str) -> bool:
    """Push a session-scoped mux option (best-effort, bounded)."""
    override = _core_helper("_monitor_mux_set", _monitor_mux_set)
    if override is not _monitor_mux_set:
        return override(mux_bin, sess, opt, val)
    try:
        result = subprocess.run(
            [mux_bin, "set-option", "-t", sess, opt, val],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def _monitor_list_sessions(
    mux_bin: str,
) -> dict[str, tuple[int, str]] | None:
    """Enumerate mux sessions with the monitor's OWN selected binary.

    ``sessions._list_mux_sessions`` chooses tmux/psmux by *platform*, which can
    disagree with the binary ``cmd_status_monitor`` actually resolved
    (psmux-if-present) -- a mismatch would enumerate the wrong multiplexer and
    return nothing every sweep, so the monitor neither serves nor idle-exits.
    Query ``mux_bin`` directly instead. Returns
    ``{session_name: (attached, incarnation)}`` or ``None`` on a transient
    failure (so the caller neither prunes nor exits).
    """
    override = _core_helper("_monitor_list_sessions", _monitor_list_sessions)
    if override is not _monitor_list_sessions:
        return override(mux_bin)
    try:
        r = subprocess.run(
            [
                mux_bin,
                "list-sessions",
                "-F",
                "#{session_name}:#{session_attached}:#{session_id}:#{session_created}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    out: dict[str, tuple[int, str]] = {}
    for line in (r.stdout or "").strip().splitlines():
        if line.count(":") < 3:
            continue
        name, _, created = line.rpartition(":")
        name, _, session_id = name.rpartition(":")
        name, _, cnt = name.rpartition(":")
        incarnation = f"{session_id}:{created}"
        try:
            out[name] = (int(cnt), incarnation)
        except ValueError:
            out[name] = (0, incarnation)
    return out


def _monitor_session_state_handoff_path(
    session_id: str | None,
) -> Path | None:
    """Return the session-state handoff request path for ``session_id``."""
    if not session_id:
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id))[:160] or "unknown"
    return Path.home() / ".copilot" / "session-state" / safe / "handoff-request.json"


def _monitor_read_session_state_handoff(
    path: str | os.PathLike[str] | None,
) -> dict[str, object] | None:
    """Read a context-handoff session-state request marker."""
    override = _core_helper("_monitor_read_session_state_handoff", _monitor_read_session_state_handoff)
    if override is not _monitor_read_session_state_handoff:
        return override(path)
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _monitor_pending_handoff_request(
    record: tracking.WorktreeRecord,
) -> dict[str, object] | None:
    """Return one actionable pending handoff request for the monitor to pick up."""
    if not _status_monitor_enabled():
        return None
    worktree_id = getattr(record, "worktree_id", None)
    if not worktree_id:
        return None
    requested = {
        str(event.get("handoff_id") or "").strip(): event
        for event in activity.read_events(
            worktree_id=worktree_id,
            event="handoff_requested",
            limit=64,
        )
        if str(event.get("handoff_id") or "").strip()
    }
    already_attempted = sessions_pane_retire.already_attempted_handoff_tokens(worktree_id)
    for handoff in reversed(record.pending_handoffs):
        token = str(getattr(handoff, "token", "") or "").strip()
        confirmed = getattr(handoff, "candidate", None) or getattr(handoff, "successor", None)
        if not token or confirmed or token in already_attempted:
            continue
        request_event = requested.get(token, {})
        predecessor_session = str(
            request_event.get("session_id") or getattr(handoff, "predecessor", "") or ""
        ).strip()
        if not predecessor_session:
            continue
        session_state = request_event.get("session_state")
        if not session_state:
            fallback = _monitor_session_state_handoff_path(predecessor_session)
            session_state = str(fallback) if fallback is not None else None
        request = _monitor_read_session_state_handoff(session_state)
        if request is None or request.get("consumed"):
            continue
        request_token = str(request.get("handoffId") or "").strip()
        if request_token and request_token != token:
            continue
        request_worktree = str(request.get("worktree") or "").strip()
        if request_worktree and request_worktree != worktree_id:
            continue
        seed = str(request.get("seed") or "").strip()
        if not seed:
            continue
        predecessor_pid_raw = request_event.get("predecessor_pid")
        predecessor_pid = None
        if predecessor_pid_raw not in (None, ""):
            try:
                predecessor_pid = int(str(predecessor_pid_raw).strip())
            except (TypeError, ValueError):
                predecessor_pid = None
        predecessor_start = None
        if predecessor_pid is not None:
            try:
                predecessor_start = locks.process_start_time(predecessor_pid)
            except Exception:
                predecessor_start = None
        request_data = {
            "token": token,
            "seed": seed,
            "worktree_id": worktree_id,
            "predecessor_session_id": predecessor_session,
            "predecessor_pid": predecessor_pid,
            "predecessor_start_time": predecessor_start,
            "session_state_path": session_state,
            "storage": str(request.get("storage") or request_event.get("storage") or "").strip()
            or None,
        }
        claim = _monitor_claim_handoff_cutover(request_data)
        if not claim.get("ok") or not claim.get("claimed"):
            continue
        request_data["claim_path"] = claim.get("path")
        return request_data
    return None
