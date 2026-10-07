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
    ``os.pidfd_open``/``signal.pidfd_send_signal`` are callable AND a
    self-probe (``pidfd_open`` against our own, guaranteed-alive pid)
    does not report definite kernel-level absence of the syscall
    (``ENOSYS``) -- the Python bindings existing does not prove the
    running kernel supports the syscall; an older kernel raises
    ``ENOSYS`` there.

    Any OTHER self-probe failure (``EMFILE``/``ENFILE``/``EPERM``/etc.) is
    deliberately treated as inconclusive, NOT as capability absence: this
    function still returns ``True`` in that case, keeping ``_kill_pid`` on
    the fail-closed identity-bound path (where a subsequent per-victim
    lookup failure simply skips the kill) rather than opening up the
    legacy unverified-kill fallback on a transient/unrelated resource
    error that says nothing about whether the real victim pid could be
    safely identity-verified.
    """
    if sys.platform == "win32":
        return True
    import errno
    import signal as _signal

    if not callable(getattr(os, "pidfd_open", None)) or not callable(
        getattr(_signal, "pidfd_send_signal", None)
    ):
        return False
    try:
        fd = os.pidfd_open(os.getpid(), 0)
    except OSError as exc:
        return exc.errno != errno.ENOSYS
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
    """Capture *child_pid*'s current process-start-time identity token
    FIRST, then verify it is STILL a live child of *recorded_parent_pid* --
    returning that same, already-captured token, or ``None`` if either
    step fails.

    Mirrors ``_kill_pid``'s own token-before-ownership-check ordering:
    capturing the token before the (slower) ancestry re-check means a pid
    reused during that check is still caught by
    ``terminate_pid_if_identity``'s own final re-verification at the
    actual kill -- using a token captured any later (e.g. after the
    ancestry check, as a prior version of this function did) would
    instead risk capturing a *replacement* process's token if reuse
    happened during that check, since nothing would subsequently catch
    the mismatch for a replacement that happens to share the same parent.
    """
    import subprocess as sp

    from zdd import diagnostics

    start_time = diagnostics.process_start_time(child_pid)
    if start_time is None:
        return None

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
    return start_time


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

    If the ROOT's own identity verification fails (the census ran before
    the root died and its pid got reused), descendant cleanup is skipped
    entirely: a descendant census taken from that point can describe the
    *replacement* process's children, not the verified daemon's, so
    killing them would terminate unrelated processes even though the root
    kill itself was correctly refused.

    A failed census (vs. a genuinely empty one) is surfaced via a stderr
    warning rather than silently treated as "no descendants" -- the root
    is still killed (best effort, once its own identity is verified), but
    some live children may survive when the census itself could not run,
    and that is made visible rather than silently assumed away. This
    never falls back to a bare, unverified ``taskkill``.
    """
    from zdd import diagnostics

    root_result = diagnostics.terminate_pid_if_identity(pid, start_time)
    if not root_result.get("identity_verified"):
        return

    descendants, census_ok = _enumerate_descendant_pids_windows(pid)
    if not census_ok:
        print(
            f"[WARN] agent-bridge: descendant-process census failed for pid {pid}; "
            "only the root process was identity-verified and killed -- some live "
            "children may survive",
            file=sys.stderr,
        )
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
