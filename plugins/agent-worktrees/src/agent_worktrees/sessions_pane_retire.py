"""Pane-retirement helpers split out of ``sessions.py``.

This module exists primarily to keep ``agent_worktrees.sessions`` under its
grandfathered module-size baseline. It now also owns the pane-target resolution
helpers shared by the pane lifecycle primitives.
"""

from __future__ import annotations

import os
import platform


class MuxPaneTargetAmbiguityError(RuntimeError):
    """Bare pane id matched multiple mux sessions, so targeting is unsafe."""

    def __init__(self, pane_id: str, session_names: list[str]) -> None:
        self.pane_id = pane_id
        self.session_names = tuple(session_names)
        listed = ", ".join(self.session_names)
        super().__init__(
            f"pane id {pane_id} is ambiguous across mux sessions: {listed}"
        )


def _mux_bin(mux: str | None = None) -> str:
    """Resolve the multiplexer binary name (psmux on Windows, tmux elsewhere)."""
    if mux:
        return mux
    return "psmux" if platform.system() == "Windows" else "tmux"


def _list_matching_pane_targets(
    pane_id: str,
    mux_bin: str,
    *,
    session_name: str | None = None,
) -> list[tuple[str, str]]:
    """Return ``(session_name, window.pane)`` matches for one pane id.

    When ``session_name`` is known, scope the lookup to that exact session and
    resolve the specific ``window.pane`` address needed to build an unambiguous
    ``session:window.pane`` target. When it is unknown, scan across sessions so
    callers can detect an ambiguous bare ``%N`` on psmux instead of guessing.
    """
    import subprocess

    from . import sessions

    argv = [mux_bin, "list-panes"]
    if session_name:
        argv += ["-t", sessions._mux_named_session_target(session_name, mux_bin)]
    else:
        argv.append("-a")
    argv += ["-F", "#{session_name}\t#{window_index}.#{pane_index}\t#{pane_id}"]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    matches: list[tuple[str, str]] = []
    for line in getattr(result, "stdout", "").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        found_session, window_pane, found_pane_id = parts
        if found_pane_id != pane_id or not found_session or not window_pane:
            continue
        if session_name and found_session != session_name:
            continue
        matches.append((found_session, window_pane))
    return matches


def _resolve_unambiguous_pane_session(pane_id: str, mux_bin: str) -> str | None:
    """Return the sole session owning ``pane_id`` or raise on ambiguity."""
    session_names = sorted(
        {session_name for session_name, _window_pane in _list_matching_pane_targets(
            pane_id, mux_bin,
        )}
    )
    if not session_names:
        return None
    if len(session_names) > 1:
        raise MuxPaneTargetAmbiguityError(pane_id, session_names)
    return session_names[0]


def _mux_qualified_pane_target(
    pane_id: str | None,
    mux_bin: str,
    *,
    session_name: str | None = None,
) -> str | None:
    """Resolve ``pane_id`` to an exact ``session:window.pane`` target."""
    if not pane_id:
        return None
    from . import sessions

    resolved_session = session_name or _resolve_unambiguous_pane_session(pane_id, mux_bin)
    if not resolved_session:
        return None
    window_panes = sorted(
        {window_pane for _session, window_pane in _list_matching_pane_targets(
            pane_id, mux_bin, session_name=resolved_session,
        )}
    )
    if len(window_panes) != 1:
        return None
    return (
        f"{sessions._mux_named_session_target(resolved_session, mux_bin)}:"
        f"{window_panes[0]}"
    )


def _mux_pane_pid(
    pane_id: str | None,
    *,
    mux: str | None = None,
    session_name: str | None = None,
) -> int | None:
    """Return the root pid of one mux pane, or ``None`` when unavailable."""
    if not pane_id:
        return None
    import subprocess

    mux_bin = _mux_bin(mux)
    target = (
        _mux_qualified_pane_target(pane_id, mux_bin, session_name=session_name)
        if session_name
        else pane_id
    )
    if not target:
        return None
    try:
        r = subprocess.run(
            [
                mux_bin, "display-message", "-p", "-t", target,
                "#{pane_pid}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0:
            return None
        pid = int(r.stdout.strip())
        return pid if pid > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _mux_pane_process_tree(
    pane_id: str | None,
    *,
    mux: str | None = None,
    session_name: str | None = None,
) -> set[int]:
    """Snapshot the exact process tree rooted at ``pane_id`` before teardown."""
    pane_pid = _mux_pane_pid(pane_id, mux=mux, session_name=session_name)
    if not pane_pid:
        return set()
    try:
        from . import reclaim

        table = reclaim.build_process_table()
        return {pane_pid, *reclaim.descendants_of(pane_pid, table)}
    except OSError:
        return {pane_pid}


def _retire_failed_successor(
    pane_id: str | None,
    process_tree: set[int],
    *,
    mux: str | None = None,
    mux_session: str | None = None,
) -> dict:
    """Retire a failed successor pane and terminate its exact surviving tree."""
    import signal
    import time

    retire = (
        mux_retire_pane(pane_id, mux=mux, mux_session=mux_session)
        if pane_id else {"ok": True, "gone": True, "method": "no-pane"}
    )
    terminated: list[int] = []
    survivors: list[int] = []
    try:
        from . import locks, procs

        # Children first, pane root last. The snapshot is pane-specific, so this
        # cannot splash onto the predecessor or another pane in the same worktree.
        for pid in sorted(process_tree, reverse=True):
            if not locks.pid_alive(pid):
                continue
            if procs.terminate_pid(pid):
                terminated.append(pid)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            survivors = [
                pid for pid in sorted(process_tree) if locks.pid_alive(pid)
            ]
            if not survivors:
                break
            time.sleep(0.05)
        # procs.terminate_pid is SIGTERM on POSIX. Escalate the exact pane tree
        # after the bounded grace period; Windows already uses TerminateProcess.
        if survivors and platform.system() != "Windows":
            for pid in survivors:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                survivors = [
                    pid for pid in survivors if locks.pid_alive(pid)
                ]
                if not survivors:
                    break
                time.sleep(0.05)
    except OSError:
        survivors = sorted(process_tree)
    return {
        "retire": retire, "process_tree": sorted(process_tree), "terminated": terminated,
        "survivors": survivors, "ok": bool(retire.get("gone")) and not survivors,
    }


def _mux_pane_alive(
    pane_id: str,
    mux_bin: str,
    session_name: str | None = None,
) -> bool:
    """Whether ``pane_id`` still exists, refusing ambiguous bare psmux hits."""
    try:
        resolved_session = session_name or _resolve_unambiguous_pane_session(pane_id, mux_bin)
    except MuxPaneTargetAmbiguityError:
        return False
    if not resolved_session:
        return False
    return bool(
        _list_matching_pane_targets(pane_id, mux_bin, session_name=resolved_session)
    )


def wait_for_handoff_candidate(
    record_path,
    token: str,
    pane_id: str | None,
    *,
    timeout: float = 30.0,
    mux_session: str | None = None,
    predecessor_session_id: str | None = None,
    pane_scan_interval: float = 1.0,
) -> tuple[str | None, str]:
    """Wait until a successor is confirmed for the exact handoff token.

    Races two confirmation paths: the successor's own sessionStart-hook
    self-report (needs the mux to propagate ``-e`` env, e.g. tmux), and
    agent-worktrees' own pane-process-ancestry match (env-var-free; see
    :func:`associate_pane_matched_candidate` -- the only path that works on
    psmux/Windows, where env propagation is not trusted). The pane/process
    scan is throttled to ``pane_scan_interval`` -- each attempt spawns a
    ``list-panes`` subprocess and rebuilds process ancestry per live session,
    so running it on every 50ms poll tick would multiply into hundreds of
    subprocess spawns over a 30s wait (worse on a record with several stacked
    successors -- the exact failure mode this is recovering from).
    """
    import time

    from . import tracking

    deadline = time.monotonic() + timeout
    mux_bin = _mux_bin()
    next_pane_scan = 0.0
    while time.monotonic() < deadline:
        try:
            candidate_record = tracking.load_record(record_path)
            handoff = next(
                (item for item in candidate_record.handoffs if item.token == token),
                None,
            )
        except (OSError, ValueError):
            handoff = None
        if handoff is not None and handoff.candidate:
            if not mux_session:
                return handoff.candidate, "session-associated"
            # Pane-aware wait: `handoff.candidate` is a record-wide field, not
            # proof it belongs to *this* pane -- a racing/earlier attempt's
            # self-report could have set it. Confirm via the same
            # process-ancestry check before trusting it. Without a known
            # `pane_id` to check against, fail closed (keep polling) rather
            # than accept an unverifiable candidate.
            binding = None
            if pane_id:
                try:
                    from . import sessions

                    binding = sessions.mux_binding_for_session(
                        handoff.candidate, expected_session_name=mux_session,
                    )
                except Exception:
                    binding = None
            if binding and binding.get("pane_id") == pane_id:
                return handoff.candidate, "session-associated"
        now = time.monotonic()
        if pane_id and mux_session and now >= next_pane_scan:
            next_pane_scan = now + pane_scan_interval
            matched = associate_pane_matched_candidate(
                record_path, token, mux_session, pane_id, predecessor_session_id,
            )
            if matched:
                return matched, "pane-process-associated"
        if pane_id and not _mux_pane_alive(pane_id, mux_bin):
            return None, "pane-exited-before-session"
        time.sleep(0.05)
    return None, "session-association-timeout"


def associate_pane_matched_candidate(
    record_path,
    token: str,
    mux_session: str,
    pane_id: str,
    predecessor_session_id: str | None,
) -> str | None:
    """Confirm handoff candidacy by process ancestry alone -- no env var.

    Self-registration via ``AGENT_WORKTREES_HANDOFF_TOKEN`` requires the mux to
    propagate a custom ``-e`` environment value into the new pane's actual child
    process environment. psmux (Windows) does not guarantee this the way tmux's
    ``-e`` does, so a Windows successor can never self-report the token even
    though it is a perfectly live, correct successor -- the exact shape of the
    stacking-panes bug this guards against. This is the reverse, spawner-side
    check: for every session already registered against this worktree (ordinary
    ``register-session`` on sessionStart, which needs no token at all -- only
    cwd/mux-ancestry resolution), ask :func:`sessions.mux_binding_for_session`
    -- itself pure process-ancestry against the live ``inuse.<pid>.lock``, no
    env var -- whether that session is the one actually running under the pane
    this spawn just opened. A match means agent-worktrees itself confirms
    candidacy; the successor never needs to cooperate.
    """
    from . import sessions, tracking

    try:
        record = tracking.load_record(record_path)
    except (OSError, ValueError):
        return None
    for entry in record.sessions or []:
        if entry.session_id == predecessor_session_id:
            continue
        if entry.ended_at or entry.state != "active":
            continue
        try:
            binding = sessions.mux_binding_for_session(
                entry.session_id, expected_session_name=mux_session,
            )
        except Exception:
            binding = None
        if not binding or binding.get("pane_id") != pane_id:
            continue
        try:
            with tracking._RecordLock(record_path):
                locked_record = tracking.load_record(record_path)
                # Revalidate against the locked snapshot: the unlocked binding
                # check above can race a concurrent deregistration/conclusion
                # between that check and taking the lock.
                locked_entry = locked_record.session_entry(entry.session_id)
                if (
                    locked_entry is None
                    or locked_entry.ended_at
                    or locked_entry.state != "active"
                ):
                    continue
                tracking.associate_handoff_candidate(
                    locked_record, token, entry.session_id, save=True,
                )
        except tracking.SessionLifecycleError:
            # Someone else (the successor's own self-report, or a concurrent
            # call) already associated a DIFFERENT candidate for this token, or
            # the handoff is no longer pending -- re-read the authoritative
            # state rather than trusting our own attempted association.
            try:
                current = tracking.load_record(record_path)
                won = next(
                    (h for h in current.handoffs if h.token == token), None,
                )
            except (OSError, ValueError):
                won = None
            if won is None or won.candidate != entry.session_id:
                return None
        except (OSError, ValueError):
            return None
        return entry.session_id
    return None


def _mux_pane_session_name(pane_id: str, mux_bin: str) -> str | None:
    """Return the mux session name containing ``pane_id``."""
    import subprocess

    try:
        r = subprocess.run(
            [mux_bin, "display-message", "-p", "-t", pane_id, "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        return getattr(r, "stdout", "").strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _mux_session_window_count(session_name: str, mux_bin: str) -> int | None:
    """Return the number of windows in ``session_name`` when the mux reports it."""
    import subprocess

    target = session_name if mux_bin == "psmux" else f"={session_name}"
    try:
        r = subprocess.run(
            [mux_bin, "list-windows", "-t", target, "-F", "#{window_id}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        return len([line for line in getattr(r, "stdout", "").splitlines() if line.strip()])
    except (OSError, subprocess.TimeoutExpired):
        return None


def _mux_last_window_guard(
    pane_id: str,
    mux_bin: str,
    session_name: str | None = None,
) -> dict | None:
    """Return guard context when retiring ``pane_id`` would close a wt session."""
    pane_session = session_name or _mux_pane_session_name(pane_id, mux_bin)
    if not pane_session or not pane_session.startswith("wt-"):
        return None
    window_count = _mux_session_window_count(pane_session, mux_bin)
    if window_count == 1:
        return {"session": pane_session, "window_count": window_count}
    return None


def mux_retire_pane(
    pane_id: str,
    *,
    mux: str | None = None,
    mux_session: str | None = None,
    settle_timeout: float = 6.0,
    poll_interval: float = 0.3,
    ctrl_c_gap: float = 0.6,
    escalate_after: float = 1.5,
    hard_kill_settle: float = 1.5,
) -> dict:
    """Retire a specific pane by asking its Copilot to quit cleanly.

    Copilot CLI exits on a **double Ctrl-C** ~600 ms apart (a single one does
    little) -- its native clean-quit path (cf. :func:`graceful_quit_mux_session`).
    Unlike that session-scoped helper, this targets one ``pane_id`` so it retires
    the OLD Copilot after a cutover without touching the successor (the session's
    new active pane). Falls back to ``kill-pane`` if it does not exit in time.

    **Escalation ladder (up to three Ctrl-C).** Two interrupts is the common
    case, but some Copilot states swallow the second (mid-render, a modal, a busy
    turn flushing state) -- so after the double-interrupt we wait a brief
    ``escalate_after`` window and, only if the pane is still alive, deliver a
    conditional **third** Ctrl-C before the hard ``kill-pane`` fallback. This
    mirrors :func:`graceful_quit_mux_session` (a76ab47 / #2614) so a stubborn old
    pane is retired cleanly (persisting session state) instead of being severed,
    which is the failure mode behind a lingering un-retired pane (#3946).

    Returns ``{ok, pane, gone, method}`` where ``method`` is ``already-gone``,
    ``graceful``, ``hard``, or ``failed``.
    """
    import subprocess
    import time

    mux_bin = _mux_bin(mux)
    target = (
        _mux_qualified_pane_target(pane_id, mux_bin, session_name=mux_session)
        if mux_session
        else None
    )
    if mux_session and not target:
        return {
            "ok": False,
            "pane": pane_id,
            "gone": False,
            "method": "failed",
            "session": mux_session,
        }

    def _send(keys: str) -> bool:
        try:
            r = subprocess.run(
                [mux_bin, "send-keys", "-t", target or pane_id, keys],
                capture_output=True, timeout=5,
            )
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _gone_within(window: float) -> bool:
        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            if not _mux_pane_alive(pane_id, mux_bin, mux_session):
                return True
            time.sleep(poll_interval)
        return not _mux_pane_alive(pane_id, mux_bin, mux_session)

    if not _mux_pane_alive(pane_id, mux_bin, mux_session):
        return {"ok": True, "pane": pane_id, "gone": True, "method": "already-gone"}

    guard = _mux_last_window_guard(pane_id, mux_bin, mux_session)
    if guard:
        try:
            from . import activity

            activity.log_event(
                "handoff_retire_guard",
                source="python",
                old_pane=pane_id,
                reason="last-window-skip",
                method="guard",
                outcome="left-running",
                mux_session=guard.get("session"),
                window_count=guard.get("window_count"),
            )
        except Exception:
            pass
        return {
            "ok": True, "pane": pane_id, "gone": False,
            "method": "last-window-skip", "session": guard.get("session"),
        }

    _send("C-c")
    time.sleep(ctrl_c_gap)
    _send("C-c")

    # Brief window for the double-interrupt to land before escalating.
    escalate_at = min(max(escalate_after, 0.0), settle_timeout)
    if _gone_within(escalate_at):
        return {"ok": True, "pane": pane_id, "gone": True, "method": "graceful"}

    # Still alive after two -- conditional third, then wait out the budget.
    _send("C-c")
    if _gone_within(settle_timeout - escalate_at):
        return {"ok": True, "pane": pane_id, "gone": True, "method": "graceful"}

    # Graceful quit did not land -- hard-kill the pane.
    try:
        subprocess.run(
            [mux_bin, "kill-pane", "-t", target or pane_id],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    gone = _gone_within(max(hard_kill_settle, 0.0))
    return {
        "ok": gone, "pane": pane_id, "gone": gone,
        "method": "hard" if gone else "failed",
    }
