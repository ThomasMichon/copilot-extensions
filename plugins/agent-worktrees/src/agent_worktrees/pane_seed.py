"""Type a detached session's first prompt into its pane (``mux_seed_pane``).

Split out of :mod:`agent_worktrees.sessions` (which re-exports it); the
readiness grammar lives in :mod:`agent_worktrees.pane_readiness`.
"""

from __future__ import annotations


def mux_seed_pane(
    pane_id: str,
    seed: str,
    *,
    session_name: str | None = None,
    mux: str | None = None,
    ready_timeout: float = 20.0,
    hard_timeout: float = 900.0,
    poll_interval: float = 0.5,
    settle: float = 0.6,
) -> dict:
    """Type ``seed`` as the first interactive prompt into a freshly spawned pane.

    Hardened against seeding into the wrong pane state: confirmed-ready uses
    the live input region, requires two stable polls, never types into an
    unconfirmed pane, echo-verifies before Enter, and auto-dismisses known
    blocking startup dialogs (see :mod:`agent_worktrees.pane_nudges`).

    ``ready_timeout`` is the idle window: visibly busy/changing panes keep the
    wait alive, capped by ``hard_timeout``. Returns ``{ok, pane, ready, sent,
    submitted, reason}`` -- ``ok`` is true only when the seed was actually
    delivered as a turn (``submitted``).
    """
    import re
    import subprocess
    import time

    from . import pane_nudges, pane_readiness, sessions_pane_retire

    from . import sessions as _sessions

    mux_bin = _sessions._mux_bin(mux)  # looked up late: tests patch it there

    # Session-qualified: psmux numbers %N per session, so a bare id can hit
    # another session's Copilot. The qualified ``session:window.pane`` is only a
    # position (a layout change can renumber it, and another Copilot can take
    # it), so it is resolved again from ``pane_id`` before every capture and
    # keystroke, never cached across the wait.
    def _where() -> str | None:
        return sessions_pane_retire._mux_qualified_pane_target(pane_id, mux_bin, session_name=session_name)

    # ...and each command names the pane by its id inside that session and
    # window (``session:window.%id``): the mux server resolves that only while
    # this pane is still there, atomically with the command, so a pane swapped
    # in after the lookup is never read or typed into -- the command fails.
    def _bound(at: str) -> str:
        if not pane_id.startswith("%") or "." not in at:
            return at
        return f"{at.rsplit('.', 1)[0]}.{pane_id}"

    target = _where()
    if not target:
        return {"ok": False, "pane": pane_id, "ready": False, "sent": False, "submitted": False,
                "reason": "pane-target-unresolved"}

    def _cap(at: str) -> str:
        try:
            r = subprocess.run(
                [mux_bin, "capture-pane", "-p", "-t", _bound(at)],
                capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace",
            )
            return (r.stdout or "") if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _lost(*, ready: bool = False, sent: bool = False) -> dict:
        """The pane is gone: fail closed. Lost before typing, no keystroke landed."""
        return {"ok": False, "pane": pane_id, "ready": ready, "sent": sent, "submitted": False,
                "reason": "pane-target-lost-before-enter" if sent else "pane-target-lost"}

    # Readiness must be STABLE (two polls) so a transient banner/spinner frame
    # can't trip it. A known blocking dialog is dismissed at most once per call.
    ready = False
    stable, dismissed_nudge = 0, False
    last_ready_sig: str | None = None
    last_region: str | None = None
    start = time.monotonic()
    idle_window = max(0.0, ready_timeout)
    hard_window = max(0.0, hard_timeout)
    idle_deadline = start + idle_window
    hard_deadline = start + hard_window
    while time.monotonic() < min(idle_deadline, hard_deadline):
        now = _where()
        if not now:
            return _lost()
        if now != target:  # moved: what was seen at the old position proves nothing here
            target, stable, last_ready_sig, last_region = now, 0, None, None
        cap = _cap(target)
        region = pane_readiness.input_region(cap)
        ready_sig = pane_readiness.ready_signature(cap)
        if ready_sig:
            stable = stable + 1 if ready_sig == last_ready_sig else 1
            last_ready_sig = ready_sig
            if stable >= 2:
                if _where() == target:
                    ready = True
                    break
                stable, last_ready_sig = 0, None  # moved under the second poll: confirm again
        elif not dismissed_nudge and pane_nudges.is_desktop_app_nudge(cap):
            stable, last_ready_sig = 0, None
            # Escape goes to a position: only if the pane is still where it was
            # captured, else it could hit whatever moved there. A moved pane's
            # captured nudge is discarded; the next poll captures it afresh.
            if _where() == target:
                dismissed_nudge = True
                pane_nudges.dismiss(mux_bin, _bound(target))
        else:
            stable, last_ready_sig = 0, None
        if (pane_readiness.is_busy(region) or region != last_region) and idle_window > 0:
            idle_deadline = min(hard_deadline, time.monotonic() + idle_window)
        last_region = region
        time.sleep(poll_interval)

    # Safety gate: without a confirmed-ready Copilot we do NOT type or submit --
    # blind keystrokes into a half-loaded TUI or a fallback shell could execute a
    # mistyped command. Degrade to "landed unseeded" (the operator can paste).
    if not ready:
        return {"ok": False, "pane": pane_id, "ready": False, "sent": False, "submitted": False,
                "reason": "not-ready-timeout"}

    def _send(*a: str) -> bool | None:
        """Send keys to the pane wherever it is now; ``None`` when it is gone
        and nothing was typed. Only the server's explicit refusal of the target
        (``can't find pane/window/session``: resolved before any key is sent)
        proves nothing landed, so only that is retried, once, where the pane
        went; any other failure may have typed part of the keys: fail closed."""
        tried = None
        for _ in range(2):
            at = _where()
            if not at:
                return None  # gone; any earlier attempt was a verified refusal
            if at == tried:
                return False  # refused, but not because it moved: a real failure
            tried = at
            try:
                r = subprocess.run([mux_bin, "send-keys", "-t", _bound(at), *a],
                                   capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                return False
            if r.returncode == 0:
                return True
            if "can't find" not in (r.stderr or "").lower():
                return False
        return False

    # A distinctive head of the seed, whitespace-squashed so terminal soft-wrap
    # (a newline inserted mid-line in the captured buffer) can't defeat the echo
    # check below.
    def _squash(s: str) -> str:
        return re.sub(r"\s+", "", s)

    head = _squash(seed)[:16]

    # ``-l`` sends the seed literally (no key-name interpretation), so the whole
    # multi-word prompt lands as one input line.
    sent = _send("-l", seed)
    if sent is None:
        return _lost(ready=True)
    time.sleep(settle)

    # Echo-verify: press Enter only once the seed's head is visible in the
    # editable input -- not anywhere in the pane, where a resumed transcript can
    # show an earlier prompt with the same head -- so a partially-eaten or lost
    # seed is never submitted as a bogus turn.
    echoed = False
    if sent and head:
        for _ in range(4):
            at = _where()
            if not at:
                return _lost(ready=True, sent=True)
            if head in _squash(pane_readiness.input_text(_cap(at))):
                echoed = True
                break
            time.sleep(poll_interval)
    elif sent:
        echoed = True  # empty seed: nothing to verify

    submitted = False
    reason = None
    if echoed:
        entered = _send("Enter")
        if entered is None:
            return _lost(ready=True, sent=True)
        submitted = entered
        if not submitted:
            reason = "enter-failed"
    else:
        reason = "seed-not-echoed" if sent else "send-failed"

    return {
        "ok": bool(submitted), "pane": pane_id, "ready": ready,
        "sent": bool(sent), "submitted": bool(submitted), "reason": reason,
    }
