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
) -> tuple[list[tuple[int, int, str, str]], bool]:
    """``(child_pid, parent_pid, child_creation, parent_creation)``
    quadruples for every live descendant of *pid* on Windows, plus whether
    the census itself succeeded.

    ``child_creation``/``parent_creation`` are WMI's own ``CreationDate``
    for those pids, captured in the SAME bulk snapshot as the ancestry
    data (full-precision, invariant-culture ISO-8601 via
    ``.ToString('o')`` -- the default ``CreationDate`` string
    interpolation this module used to rely on silently truncates to
    whole seconds, which is not enough resolution to tell two distinct
    process generations on the same reused pid apart). This is NOT the
    same token format ``zdd.diagnostics`` uses for the actual
    identity-bound kill (that is captured separately, per-child,
    immediately before use -- see ``_verify_descendant_identity_windows``);
    it exists solely so a later re-check can detect "this pid (or its
    recorded parent) was reused since the census" via the same cheap WMI
    mechanism, without trusting a bare numeric ancestry match alone --
    Windows never live-reparents an orphan the way POSIX does; a dead
    parent's pid simply gets reused by something else entirely while the
    child's own ``ParentProcessId`` field keeps pointing at that now
    numerically-recycled value forever, so a numeric-only parent check can
    never detect that the parent itself changed generation.

    The returned ``bool`` distinguishes a trustworthy descendant list
    (query succeeded, *pid* itself was found in the census, and every
    reachable edge was definitively resolvable) from an untrustworthy one
    -- the query itself didn't run, *pid* wasn't found in the census (no
    anchor to validate any descendant's parent generation against), OR an
    AMBIGUOUS edge (equal child/parent ``CreationDate``, proving neither
    staleness nor legitimacy) was reachable from *pid* and had to be
    excluded rather than guessed at. Callers must not treat a ``False``
    result as "no descendants to kill" in any of these cases.
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
                '{ "$($_.ProcessId)`t$($_.ParentProcessId)`t'
                '$($_.CreationDate.ToUniversalTime().ToString(\'o\'))" }',
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

    if pid not in by_pid:
        # The root itself wasn't present in the census -- there is no
        # trustworthy census-time generation to anchor ANY descendant's
        # parent-generation check against. Treat the whole census as
        # failed rather than silently validating against nothing.
        return [], False

    # Build child->parent edges, but reject any that are DEFINITIVELY
    # stale at census time: a genuine parent always exists (and so has a
    # strictly older CreationDate) before any of its real children. If
    # the recorded child's own CreationDate is strictly EARLIER than its
    # recorded parent's, that "parent" pid has already been recycled by
    # an unrelated process since the child's real parent exited -- the
    # edge itself is bogus, independent of anything that happens later.
    # These full-precision ISO-8601 UTC timestamps (fixed-width, 'o'
    # format) sort correctly via plain string comparison.
    #
    # Equal timestamps are NOT proof of staleness -- the WMI provider's
    # own timestamp resolution can genuinely tie for a fast-spawning
    # legitimate parent/child pair, and ``.ToString('o')`` does not add
    # precision beyond what the provider actually reports. An equal-
    # timestamp edge is therefore AMBIGUOUS, not bogus: it is excluded
    # from traversal the same way a bogus edge is (fail-closed, never
    # guessed at), but tracked separately so the census can report itself
    # incomplete -- rather than silently claiming success with an empty
    # descendant list -- whenever such an edge is actually reachable from
    # the requested root.
    children_of: dict[int, list[int]] = {}
    ambiguous_children_of: dict[int, list[int]] = {}
    for child_pid, (parent_pid, child_creation) in by_pid.items():
        parent_entry = by_pid.get(parent_pid)
        if parent_entry is None:
            continue
        parent_creation = parent_entry[1]
        if child_creation < parent_creation:
            continue
        if child_creation == parent_creation:
            ambiguous_children_of.setdefault(parent_pid, []).append(child_pid)
            continue
        children_of.setdefault(parent_pid, []).append(child_pid)

    descendants: list[tuple[int, int, str, str]] = []
    seen = {pid}
    frontier = [pid]
    census_incomplete = False
    while frontier:
        next_frontier = []
        for parent_pid in frontier:
            if ambiguous_children_of.get(parent_pid):
                census_incomplete = True
            parent_creation = by_pid[parent_pid][1]
            for child_pid in children_of.get(parent_pid, []):
                if child_pid in seen:
                    continue
                seen.add(child_pid)
                child_creation = by_pid[child_pid][1]
                descendants.append((child_pid, parent_pid, child_creation, parent_creation))
                next_frontier.append(child_pid)
        frontier = next_frontier
    return descendants, not census_incomplete


def _query_pid_ancestry_windows(pid: int) -> tuple[int, str] | None:
    """The CURRENT ``(ParentProcessId, CreationDate)`` for *pid*, via the
    same full-precision WMI mechanism as the bulk census, or ``None`` if
    *pid* is not currently a live process."""
    import subprocess as sp

    try:
        out = sp.run(
            [
                _powershell_host(),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-CimInstance Win32_Process -Filter "
                f"'ProcessId={pid}' -ErrorAction SilentlyContinue) | "
                "ForEach-Object { \"$($_.ParentProcessId)`t"
                '$($_.CreationDate.ToUniversalTime().ToString(\'o\'))" }',
            ],
            capture_output=True,
            text=True,
            timeout=10,
            **_core().no_window_kwargs(),
        )
    except (OSError, sp.TimeoutExpired):
        return None
    parts = (out.stdout or "").strip().split("\t", 1)
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    return int(parts[0]), parts[1]


def _verify_descendant_identity_windows(
    child_pid: int,
    recorded_parent_pid: int,
    recorded_child_creation: str,
    recorded_parent_creation: str,
) -> str | None:
    """Capture *child_pid*'s current process-start-time identity token
    FIRST, then verify it is STILL the SAME process the census named, AND
    that its recorded parent is STILL the same parent generation the
    census observed -- returning that already-captured token, or ``None``
    if any step fails.

    Three independent checks, all against the full-precision WMI
    ``CreationDate`` fingerprint:

    1. *child_pid*'s current ``ParentProcessId`` still matches
       *recorded_parent_pid* (the ancestry edge itself hasn't changed).
    2. *child_pid*'s own current ``CreationDate`` still matches
       *recorded_child_creation* -- closing the gap where *child_pid*
       itself was reused by an unrelated process between the census and
       now, even when the replacement happens to share the same
       numerically-recorded parent pid.
    3. The recorded PARENT's current ``CreationDate`` still matches
       *recorded_parent_creation* -- closing the gap a bare ancestry
       check can never see: Windows does not live-reparent an orphan
       (unlike POSIX); a child's ``ParentProcessId`` field keeps pointing
       at a now-dead parent's pid number forever, even after that number
       gets reused by something else entirely. Without this check, an
       orphan whose original parent died (its pid later recycled by the
       daemon tree itself, or by anything else) would pass a bare
       ancestry check against the *wrong, unrelated* "parent".

    Capturing the identity token before any of these re-checks mirrors
    ``_kill_pid``'s own root-level ordering: a pid reused during the
    (slower) WMI lookups is still caught by
    ``terminate_pid_if_identity``'s own final re-verification at the
    actual kill.
    """
    from zdd import diagnostics

    start_time = diagnostics.process_start_time(child_pid)
    if start_time is None:
        return None

    child_now = _query_pid_ancestry_windows(child_pid)
    if child_now is None:
        return None
    current_parent_pid, current_child_creation = child_now
    if current_parent_pid != recorded_parent_pid:
        return None
    if current_child_creation != recorded_child_creation:
        return None

    parent_now = _query_pid_ancestry_windows(recorded_parent_pid)
    if parent_now is None:
        return None
    _, current_parent_creation = parent_now
    if current_parent_creation != recorded_parent_creation:
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
    exactly that window.

    The root is verified (a non-destructive check: does its CURRENT
    process-start-time still match *start_time*?) but **NOT terminated**
    until every descendant has already been processed. Descendants are
    then verified and killed **deepest-first** (``_enumerate_descendant
    _pids_windows`` returns them in breadth-first/shallowest-first order,
    so this function walks that list in reverse): every descendant's
    recorded parent -- root or an intermediate ancestor -- therefore
    remains alive and queryable via ``_verify_descendant_identity_windows``
    at the moment it is checked. A prior version of this function killed
    the root (or an intermediate parent) before processing its own
    children, which broke parent-generation verification entirely: once a
    parent pid is dead, re-querying its current ``CreationDate`` returns
    nothing, and every one of its children would be (incorrectly) rejected
    as unverifiable and left running. The root is terminated last, using
    the SAME *start_time* token captured at the very start of ``_kill_pid``.

    If the ROOT's own (non-destructive) identity check fails, NOTHING is
    terminated -- neither the root nor any descendant: the census was
    taken assuming this *was* the verified daemon, and that assumption is
    void once the root check itself comes back negative.

    A failed census (vs. a genuinely empty one) is surfaced via a stderr
    warning rather than silently treated as "no descendants" -- the root
    is still (eventually) killed, best effort, once its own identity is
    verified, but some live children may survive when the census itself
    could not run, and that is made visible rather than silently assumed
    away. This never falls back to a bare, unverified ``taskkill``.
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

    # Non-destructive root identity gate: confirm this is still the
    # verified process WITHOUT killing it yet -- descendants are handled
    # first, while root (and every intermediate ancestor) stays alive and
    # queryable for their own parent-generation checks.
    if diagnostics.process_start_time(pid) != start_time:
        return

    for (
        child_pid,
        recorded_parent_pid,
        recorded_child_creation,
        recorded_parent_creation,
    ) in reversed(descendants):
        child_start = _verify_descendant_identity_windows(
            child_pid, recorded_parent_pid, recorded_child_creation, recorded_parent_creation
        )
        if child_start is not None:
            diagnostics.terminate_pid_if_identity(child_pid, child_start)

    diagnostics.terminate_pid_if_identity(pid, start_time)


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
