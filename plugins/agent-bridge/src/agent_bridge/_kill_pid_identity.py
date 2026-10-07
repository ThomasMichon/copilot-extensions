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


def _enumerate_descendant_pids_windows(
    pid: int,
) -> tuple[list[tuple[int, int, str]], bool]:
    """``(child_pid, parent_pid, creation_date)`` triples for every live
    descendant of *pid* on Windows, plus whether the census itself
    succeeded.

    ``creation_date`` is WMI's own ``CreationDate`` for *that pid*,
    captured in the SAME bulk snapshot as the ancestry data -- a cheap,
    already-available generation fingerprint. It is NOT the same token
    format ``zdd.diagnostics`` uses for the actual identity-bound kill
    (that is captured separately, per-child, immediately before use --
    see ``_verify_descendant_identity_windows``); it exists solely so a
    later re-check can detect "this pid was reused since the census" by
    comparing CreationDate again, via the same cheap WMI mechanism,
    without a second per-process round trip for every level of the tree.

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
                '{ "$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CreationDate)" }',
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

    # pid -> (parent_pid, creation_date)
    by_pid: dict[int, tuple[int, str]] = {}
    children_of: dict[int, list[int]] = {}
    for line in (out.stdout or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        pid_s, ppid_s, creation = parts
        try:
            child_pid = int(pid_s.strip())
            parent_pid = int(ppid_s.strip())
        except ValueError:
            continue
        by_pid[child_pid] = (parent_pid, creation.strip())
        children_of.setdefault(parent_pid, []).append(child_pid)

    descendants: list[tuple[int, int, str]] = []
    seen = {pid}
    frontier = [pid]
    while frontier:
        next_frontier = []
        for parent_pid in frontier:
            for child_pid in children_of.get(parent_pid, []):
                if child_pid in seen:
                    continue
                seen.add(child_pid)
                _, creation_date = by_pid[child_pid]
                descendants.append((child_pid, parent_pid, creation_date))
                next_frontier.append(child_pid)
        frontier = next_frontier
    return descendants, True


def _verify_descendant_identity_windows(
    child_pid: int, recorded_parent_pid: int, recorded_creation_date: str
) -> str | None:
    """Capture *child_pid*'s current process-start-time identity token
    FIRST, then verify it is STILL the SAME process the census named --
    both its ancestry (current parent pid matches *recorded_parent_pid*)
    AND its generation (current WMI ``CreationDate`` matches
    *recorded_creation_date*) -- returning that already-captured token, or
    ``None`` if any step fails.

    The ``CreationDate`` re-check is what closes the generation-reuse gap
    a bare ancestry (parent-pid-number) check cannot: if *child_pid* itself
    was reused by an unrelated process between the census and now, its
    current ``CreationDate`` will differ even when, by numeric
    coincidence, the replacement's parent pid happens to match too. A
    naturally orphaned descendant (an intermediate ancestor died first,
    e.g. during this same tree-kill) also fails this check once Windows
    reparents it (its ``ParentProcessId`` changes) -- conservatively
    skipping it rather than risk misidentifying a reparented process as
    still belonging to the verified tree.

    Capturing the identity token before this re-check mirrors ``_kill_pid``'s
    own root-level ordering: a pid reused during the (slower) WMI lookup
    is still caught by ``terminate_pid_if_identity``'s own final
    re-verification at the actual kill.
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
                f"'ProcessId={child_pid}' -ErrorAction SilentlyContinue) | "
                'ForEach-Object { "$($_.ParentProcessId)`t$($_.CreationDate)" }',
            ],
            capture_output=True,
            text=True,
            timeout=10,
            **_core().no_window_kwargs(),
        )
    except (OSError, sp.TimeoutExpired):
        return None
    parts = (out.stdout or "").strip().split("\t", 1)
    if len(parts) != 2:
        return None
    current_ppid_s, current_creation = parts
    if not current_ppid_s.isdigit() or int(current_ppid_s) != recorded_parent_pid:
        return None
    if current_creation != recorded_creation_date:
        return None
    return start_time


def _kill_pid_tree_windows_if_identity(pid: int, start_time: str) -> None:
    """Identity-bound Windows termination of *pid* (using the
    already-captured *start_time*) AND its live descendants.

    ``zdd.diagnostics``'s Windows path (``TerminateProcess`` on a verified
    handle) only ever terminates the single named process -- unlike the
    legacy ``taskkill /T`` this replaces, it has no tree semantics of its
    own. The descendant census runs FIRST, **before** the root is killed:
    the root's own pid is still guaranteed alive and verified at that
    point (its identity token was already captured by the caller), so the
    census cannot be fooled by that pid getting freed and reused by an
    unrelated process in between -- running the census only after killing
    the root (a prior version of this function did that) would reopen
    exactly that window. Each descendant's ancestry AND generation
    fingerprint are re-verified together, immediately before its own kill
    (see ``_verify_descendant_identity_windows``) -- never trusting the
    bulk census alone for the actual termination decision.

    If the ROOT's own identity verification fails, descendant cleanup is
    skipped entirely: the census was taken assuming this *was* the
    verified daemon, and that assumption is void once the root check
    itself comes back negative.

    A failed census (vs. a genuinely empty one) is surfaced via a stderr
    warning rather than silently treated as "no descendants" -- the root
    is still killed (best effort, once its own identity is verified), but
    some live children may survive when the census itself could not run,
    and that is made visible rather than silently assumed away. This
    never falls back to a bare, unverified ``taskkill``.
    """
    from zdd import diagnostics

    descendants, census_ok = _enumerate_descendant_pids_windows(pid)
    if not census_ok:
        print(
            f"[WARN] agent-bridge: descendant-process census failed for pid {pid}; "
            "only the root process will be identity-verified and killed -- some live "
            "children may survive",
            file=sys.stderr,
        )

    root_result = diagnostics.terminate_pid_if_identity(pid, start_time)
    if not root_result.get("identity_verified"):
        return

    for child_pid, recorded_parent_pid, recorded_creation_date in descendants:
        child_start = _verify_descendant_identity_windows(
            child_pid, recorded_parent_pid, recorded_creation_date
        )
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
