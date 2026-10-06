"""Identity-bound process termination for agent-bridge's daemon lifecycle CLI.

Split out of ``service_process_cli`` to keep both modules under the repo's
module-size cap. Routes a verified pid's termination through
``zdd.diagnostics``'s identity-bound primitives (a Windows handle / POSIX
pidfd bound to an exact ``(pid, start_time)`` pair) instead of a bare
signal/``taskkill`` by pid alone -- closing the PID-reuse hazard tracked at
copilot-extensions#5006 for this call site.
"""

from __future__ import annotations

import os
import sys


def _core():
    from . import __main__ as core

    return core


def _powershell_host() -> str:
    from .service_process_cli import _powershell_host as _host

    return _host()


def _pid_is_agent_bridge(pid: int) -> bool:
    from .service_process_cli import _pid_is_agent_bridge as _check

    return _check(pid)


def _identity_termination_available() -> bool:
    """Whether this platform has a genuine identity-bound termination
    primitive at all (a build-time/kernel capability, independent of any
    specific pid). Windows always does (ctypes/kernel32); POSIX only when
    ``os.pidfd_open``/``signal.pidfd_send_signal`` are callable AND an
    actual self-probe (``pidfd_open`` against our own, guaranteed-alive
    pid) succeeds -- the Python bindings existing does not prove the
    running kernel supports the syscall (an older kernel raises
    ``ENOSYS``, which would otherwise be indistinguishable from a benign
    per-victim lookup failure and incorrectly left unkilled instead of
    falling back)."""
    if sys.platform == "win32":
        return True
    import signal as _signal

    if not callable(getattr(os, "pidfd_open", None)) or not callable(
        getattr(_signal, "pidfd_send_signal", None)
    ):
        return False
    try:
        fd = os.pidfd_open(os.getpid(), 0)
    except OSError:
        return False
    else:
        os.close(fd)
        return True


def _enumerate_descendant_pids_windows(pid: int) -> tuple[list[tuple[int, int]], bool]:
    """``(pid, parent_pid)`` pairs for every live descendant of *pid* on
    Windows, plus whether the census itself succeeded.

    Deliberately returns bare ancestry pairs, NOT identity tokens: a token
    read any later than this one bulk snapshot would be sampling whichever
    process currently owns that pid, not the one the snapshot actually
    named -- see ``_verify_descendant_identity_windows``, which re-verifies
    both ancestry and identity together, immediately before each kill.

    The returned ``bool`` distinguishes a genuinely empty descendant list
    (query succeeded, *pid* simply has no children) from an enumeration
    failure (the query itself didn't run) -- callers must not treat the
    latter as "no descendants to kill".
    """
    import subprocess as sp

    try:
        out = sp.run(
            [
                _powershell_host(),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_Process | ForEach-Object "
                '{ "$($_.ProcessId)`t$($_.ParentProcessId)" }',
            ],
            capture_output=True,
            text=True,
            timeout=15,
            **_core().no_window_kwargs(),
        )
    except (OSError, sp.TimeoutExpired):
        return [], False
    if out.returncode != 0:
        return [], False

    children_of: dict[int, list[int]] = {}
    for line in (out.stdout or "").splitlines():
        if "\t" not in line:
            continue
        pid_s, ppid_s = line.split("\t", 1)
        try:
            child_pid = int(pid_s.strip())
            parent_pid = int(ppid_s.strip())
        except ValueError:
            continue
        children_of.setdefault(parent_pid, []).append(child_pid)

    descendants: list[tuple[int, int]] = []
    seen = {pid}
    frontier = [pid]
    while frontier:
        next_frontier = []
        for parent_pid in frontier:
            for child_pid in children_of.get(parent_pid, []):
                if child_pid in seen:
                    continue
                seen.add(child_pid)
                descendants.append((child_pid, parent_pid))
                next_frontier.append(child_pid)
        frontier = next_frontier
    return descendants, True


def _verify_descendant_identity_windows(child_pid: int, recorded_parent_pid: int) -> str | None:
    """Re-verify *child_pid* is STILL a live child of *recorded_parent_pid*
    and return its current process-start-time identity token, or ``None``
    if either check fails.

    Performed as one narrow, per-pid re-check immediately before use --
    never trusting the bulk census alone -- so a pid the OS reused for an
    unrelated process between the census and this point (a different
    parent, or no live parent at all) is rejected here rather than
    accepted on stale ancestry evidence.
    """
    import subprocess as sp

    from zdd import diagnostics

    try:
        out = sp.run(
            [
                _powershell_host(),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-CimInstance Win32_Process -Filter "
                f"'ProcessId={child_pid}' -ErrorAction SilentlyContinue).ParentProcessId",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            **_core().no_window_kwargs(),
        )
    except (OSError, sp.TimeoutExpired):
        return None
    current_ppid = (out.stdout or "").strip()
    if not current_ppid.isdigit() or int(current_ppid) != recorded_parent_pid:
        return None
    return diagnostics.process_start_time(child_pid)


def _kill_pid_tree_windows_if_identity(pid: int, start_time: str) -> None:
    """Identity-bound Windows termination of *pid* (using the
    already-captured *start_time*) AND its live descendants.

    ``zdd.diagnostics``'s Windows path (``TerminateProcess`` on a verified
    handle) only ever terminates the single named process -- unlike the
    legacy ``taskkill /T`` this replaces, it has no tree semantics of its
    own. Descendant ancestry is enumerated via one bulk census, but each
    descendant's ancestry AND identity token are re-verified together,
    immediately before its own kill (see
    ``_verify_descendant_identity_windows``) -- never a token sampled
    separately from a stale bulk snapshot.

    A failed census (vs. a genuinely empty one) is surfaced via a stderr
    warning rather than silently treated as "no descendants" -- the root
    is still killed (best effort), but some live children may survive
    when the census itself could not run, and that is made visible rather
    than silently assumed away. This never falls back to a bare,
    unverified ``taskkill``.
    """
    from zdd import diagnostics

    descendants, census_ok = _enumerate_descendant_pids_windows(pid)
    if not census_ok:
        print(
            f"[WARN] agent-bridge: descendant-process census failed for pid {pid}; "
            "only the root process was identity-verified and killed -- some live "
            "children may survive",
            file=sys.stderr,
        )
    diagnostics.terminate_pid_if_identity(pid, start_time)
    for child_pid, recorded_parent_pid in descendants:
        child_start = _verify_descendant_identity_windows(child_pid, recorded_parent_pid)
        if child_start is not None:
            diagnostics.terminate_pid_if_identity(child_pid, child_start)


def _kill_pid(pid: int) -> None:
    """Terminate *pid* (and, on Windows, its live descendants), routed
    through an identity-bound OS object whenever this platform has one.

    Captures the process-start-time identity token FIRST, before any
    other check -- including before ``_pid_is_agent_bridge(pid)``'s own
    (comparatively slow, subprocess-based) ownership re-verification. This
    ordering matters: once the token is captured, ``terminate_pid_if
    _identity`` performs its OWN final re-verification immediately before
    the actual kill, so a pid reused at ANY point after this capture is
    safely rejected there, regardless of how much time later operations
    (the ownership check, Windows descendant enumeration, etc.) take.
    Capturing the token any later -- e.g. after the ownership check, as a
    prior version of this function did -- would instead risk capturing a
    *replacement* process's token if reuse happened during that slower
    check, since nothing would subsequently catch the mismatch.

    Falls back to a legacy, unverified kill **only** when this platform
    has no identity-bound termination primitive available at all (e.g.
    non-Linux POSIX without a kernel that actually supports
    ``pidfd_open`` -- ``_identity_termination_available()``, which
    self-probes rather than just checking attribute existence),
    preserving this function's prior unconditional-kill behavior there.
    Windows always has the primitive, so it never takes this fallback. A
    **per-pid** lookup failure (the handle/pidfd could not be opened for
    *this* specific pid, its start-time could not be read, or the signal
    itself failed) is different from platform incapability and must never
    fall back to a bare, unverified kill -- a pid reused in exactly that
    window is the hazard this exists to close, so those cases simply skip
    the kill rather than risk signaling a replacement process.
    """
    identity_available = _identity_termination_available()
    if identity_available:
        from zdd import diagnostics

        start_time = diagnostics.process_start_time(pid)
    else:
        start_time = None

    if not _pid_is_agent_bridge(pid):
        return

    if identity_available:
        if start_time is None:
            return
        if sys.platform == "win32":
            _kill_pid_tree_windows_if_identity(pid, start_time)
        else:
            diagnostics.terminate_pid_if_identity(pid, start_time)
        return

    import signal as _signal
    import subprocess as sp

    if sys.platform == "win32":
        sp.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True, text=True)
    else:
        try:
            os.kill(pid, _signal.SIGTERM)
        except OSError:
            pass
