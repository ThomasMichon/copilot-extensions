"""``_kill_pid`` must route through an identity-bound OS object when possible.

Regression/hardening: closes copilot-extensions#5006 for
``_kill_pid_identity``'s ``_kill_pid`` -- the check-then-bare-kill window
between a caller's ``_pid_is_agent_bridge`` re-verification and the actual
signal/``taskkill`` could let a pid the OS has since reused for an unrelated
process be killed in the verified process's place. Mirrors
``worktree_manager.mux_daemon_cutover``'s analogous fix (#5060): capture a
fresh ``process_start_time`` token immediately -- before any other check --
and terminate through ``zdd.diagnostics.terminate_pid_if_identity``, which
performs its own final re-verification right at the kill.
"""

from __future__ import annotations

import errno
import signal
import subprocess
import sys
import time

import pytest

import agent_bridge._kill_pid_identity as m
from zdd import diagnostics


def _allow_pidfd(monkeypatch, *, self_probe_ok: bool = True) -> None:
    """Make ``_identity_termination_available()`` see a working pidfd
    primitive (attribute present AND a self-probe that doesn't report
    definite ``ENOSYS`` kernel-level absence)."""
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda fd, sig: None, raising=False)
    if self_probe_ok:
        monkeypatch.setattr(m.os, "pidfd_open", lambda pid, flags=0: 7, raising=False)
    else:
        def _raise(pid, flags=0):
            raise OSError(errno.ENOSYS, "Function not implemented")

        monkeypatch.setattr(m.os, "pidfd_open", _raise, raising=False)
    monkeypatch.setattr(m.os, "close", lambda fd: None)


def test_kill_pid_captures_token_before_ownership_check(monkeypatch):
    """The identity token must be captured FIRST, before
    ``_pid_is_agent_bridge``'s own (slower) ownership re-verification --
    so a pid reused during that slower check is still caught by
    ``terminate_pid_if_identity``'s own final re-check, rather than this
    function instead capturing a replacement process's token."""
    monkeypatch.setattr(m.sys, "platform", "linux")
    _allow_pidfd(monkeypatch)
    order: list[str] = []
    monkeypatch.setattr(
        diagnostics, "process_start_time", lambda pid: order.append("token") or "123456"
    )
    monkeypatch.setattr(
        m, "_pid_is_agent_bridge", lambda pid: order.append("ownership") or True
    )
    monkeypatch.setattr(
        diagnostics,
        "terminate_pid_if_identity",
        lambda pid, st: {"killed": True, "identity_verified": True, "method": "pidfd"},
    )

    m._kill_pid(4242)

    assert order == ["token", "ownership"]


def test_kill_pid_skips_termination_when_ownership_recheck_fails(monkeypatch):
    """A failed ownership re-check must prevent any termination attempt,
    even though the token was already captured by that point."""
    monkeypatch.setattr(m.sys, "platform", "linux")
    _allow_pidfd(monkeypatch)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "123456")
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: False)
    terminate_calls = []
    monkeypatch.setattr(
        diagnostics,
        "terminate_pid_if_identity",
        lambda pid, st: terminate_calls.append((pid, st))
        or {"killed": True, "identity_verified": True, "method": "pidfd"},
    )

    m._kill_pid(4242)

    assert terminate_calls == []


def test_kill_pid_linux_uses_identity_verified_termination(monkeypatch):
    """On Linux (pidfd available), the verified pid is terminated through
    the identity-bound primitive, with no legacy signal fallback."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "linux")
    _allow_pidfd(monkeypatch)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "123456")
    calls = []
    monkeypatch.setattr(
        diagnostics,
        "terminate_pid_if_identity",
        lambda pid, start_time: calls.append((pid, start_time))
        or {"killed": True, "identity_verified": True, "method": "pidfd"},
    )
    fallback_called = []
    monkeypatch.setattr(m.os, "kill", lambda pid, sig: fallback_called.append((pid, sig)))

    m._kill_pid(4242)

    assert calls == [(4242, "123456")]
    assert fallback_called == []


def test_kill_pid_linux_skips_on_identity_mismatch(monkeypatch):
    """A genuine identity mismatch (the pid was reused) must never fall
    back to a legacy signal -- that is the exact hazard this closes."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "linux")
    _allow_pidfd(monkeypatch)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "123456")
    monkeypatch.setattr(
        diagnostics,
        "terminate_pid_if_identity",
        lambda pid, start_time: {
            "killed": False,
            "identity_verified": False,
            "method": "pidfd-identity-mismatch",
        },
    )
    fallback_called = []
    monkeypatch.setattr(m.os, "kill", lambda pid, sig: fallback_called.append((pid, sig)))

    m._kill_pid(4242)

    assert fallback_called == []


def test_kill_pid_linux_never_falls_back_on_per_pid_lookup_failure(monkeypatch):
    """A per-pid lookup failure (this specific pid's start-time couldn't be
    read, e.g. it already exited) is NOT platform incapability -- it must
    not fall back to a bare, unverified kill either, since a pid reused in
    exactly that window is the hazard this closes."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "linux")
    _allow_pidfd(monkeypatch)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: None)
    fallback_called = []
    monkeypatch.setattr(m.os, "kill", lambda pid, sig: fallback_called.append((pid, sig)))

    m._kill_pid(4242)

    assert fallback_called == []


def test_kill_pid_falls_back_when_pidfd_attribute_missing(monkeypatch):
    """A platform with no identity-bound primitive AT ALL (e.g. macOS/BSD,
    no ``pidfd_open`` attribute) keeps the prior unconditional-kill
    guarantee."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "darwin")
    monkeypatch.delattr(m.os, "pidfd_open", raising=False)
    fallback_called = []
    monkeypatch.setattr(m.os, "kill", lambda pid, sig: fallback_called.append((pid, sig)))

    m._kill_pid(4242)

    assert fallback_called and fallback_called[0][0] == 4242


def test_kill_pid_falls_back_when_kernel_lacks_pidfd_support(monkeypatch):
    """``os.pidfd_open`` existing as a Python binding does not prove the
    running kernel actually supports it -- an older kernel raises ENOSYS,
    which the self-probe must detect and treat as genuine platform
    incapability (triggering the legacy fallback), not a per-victim
    lookup failure (which would instead skip the kill)."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "linux")
    _allow_pidfd(monkeypatch, self_probe_ok=False)
    fallback_called = []
    monkeypatch.setattr(m.os, "kill", lambda pid, sig: fallback_called.append((pid, sig)))

    m._kill_pid(4242)

    assert fallback_called and fallback_called[0][0] == 4242


def test_kill_pid_stays_on_identity_path_when_self_probe_is_inconclusive(monkeypatch):
    """A self-probe failure that is NOT definite ``ENOSYS`` absence (e.g.
    ``EMFILE``/``EPERM``, a resource/access error unrelated to kernel
    support) must NOT be treated as platform incapability -- it is
    inconclusive, so ``_kill_pid`` must stay on the fail-closed
    identity-bound path (skipping the kill on a later per-victim lookup
    failure) rather than opening the legacy unverified-kill fallback."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda fd, sig: None, raising=False)

    def _raise_emfile(pid, flags=0):
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(m.os, "pidfd_open", _raise_emfile, raising=False)
    # The real pidfd_open for the actual victim also fails (same resource
    # pressure) -- per-victim lookup failure, must skip the kill, not fall
    # back to a bare, unverified kill.
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: None)
    fallback_called = []
    monkeypatch.setattr(m.os, "kill", lambda pid, sig: fallback_called.append((pid, sig)))

    m._kill_pid(4242)

    assert fallback_called == []


def test_enumerate_descendant_pids_windows_attaches_each_levels_parent_creation(monkeypatch):
    """The census must attach each descendant's RECORDED-PARENT's own
    census-time creation date (not just the child's own), including at
    depth 2+ -- this is the data ``_verify_descendant_identity_windows``
    needs to validate parent generations, not just numeric ancestry."""
    import subprocess as sp

    class _Result:
        returncode = 0
        stdout = "\n".join(
            [
                "4242\t1\t2026-01-01T00:00:00.0000000Z",  # root
                "5001\t4242\t2026-01-01T00:00:01.0000000Z",  # level-1 child of root
                "6001\t5001\t2026-01-01T00:00:02.0000000Z",  # level-2 grandchild of 5001
            ]
        )

    monkeypatch.setattr(sp, "run", lambda *a, **k: _Result())

    descendants, census_ok = m._enumerate_descendant_pids_windows(4242)

    assert census_ok is True
    by_child = {d[0]: d for d in descendants}
    assert by_child[5001] == (
        5001,
        4242,
        "2026-01-01T00:00:01.0000000Z",
        "2026-01-01T00:00:00.0000000Z",
    )
    # parent creation is 5001's OWN (level-1), not root's
    assert by_child[6001] == (
        6001,
        5001,
        "2026-01-01T00:00:02.0000000Z",
        "2026-01-01T00:00:01.0000000Z",
    )


def test_enumerate_descendant_pids_windows_rejects_chronologically_stale_edge(monkeypatch):
    """An edge whose recorded child STRICTLY predates its recorded parent
    is definitively bogus and rejected at census time, before any
    traversal -- a genuine parent always exists (and is thus older)
    before any of its real children, so this is proof the "parent" pid
    has already been recycled by an unrelated process since the child's
    real parent exited. This definitive case is still reported as a
    successful (not merely incomplete) census: there is no ambiguity
    here, unlike an equal-timestamp edge."""
    import subprocess as sp

    class _Result:
        returncode = 0
        stdout = "\n".join(
            [
                "4242\t1\t2026-01-01T00:00:05.0000000Z",  # root
                # "child" 5001 predates its recorded parent 4242 -- bogus edge
                "5001\t4242\t2026-01-01T00:00:00.0000000Z",
            ]
        )

    monkeypatch.setattr(sp, "run", lambda *a, **k: _Result())

    descendants, census_ok = m._enumerate_descendant_pids_windows(4242)

    assert census_ok is True
    assert descendants == []  # the stale edge is excluded, not merely flagged


def test_enumerate_descendant_pids_windows_treats_equal_timestamps_as_incomplete(monkeypatch):
    """Equal child/parent ``CreationDate`` values are NOT proof of
    staleness -- a legitimate parent/child pair can genuinely tie at the
    WMI provider's own timestamp resolution. Such an edge must still be
    excluded from traversal (fail-closed, never guessed at), but the
    census as a whole must report itself INCOMPLETE rather than silently
    claiming success with an empty descendant list, since real children
    may exist beyond that ambiguous edge."""
    import subprocess as sp

    class _Result:
        returncode = 0
        stdout = "\n".join(
            [
                "4242\t1\t2026-01-01T00:00:05.0000000Z",  # root
                # child ties its recorded parent exactly -- ambiguous, not bogus
                "5001\t4242\t2026-01-01T00:00:05.0000000Z",
            ]
        )

    monkeypatch.setattr(sp, "run", lambda *a, **k: _Result())

    descendants, census_ok = m._enumerate_descendant_pids_windows(4242)

    assert descendants == []  # still excluded from traversal
    assert census_ok is False  # but the census is marked incomplete, not successful


def test_enumerate_descendant_pids_windows_fails_when_root_absent_from_census(monkeypatch):
    """If the root pid itself doesn't appear in the bulk census output
    (e.g. it already exited between the caller's check and this query),
    there is no trustworthy census-time generation to anchor ANY
    descendant's parent-generation check against -- the whole census must
    be treated as failed, not as "root has no descendants"."""
    import subprocess as sp

    class _Result:
        returncode = 0
        stdout = "5001\t9999\t2026-01-01T00:00:00.0000000Z"  # root 4242 nowhere in output

    monkeypatch.setattr(sp, "run", lambda *a, **k: _Result())

    descendants, census_ok = m._enumerate_descendant_pids_windows(4242)

    assert descendants == []
    assert census_ok is False


def test_kill_pid_windows_terminates_root_and_live_descendants(monkeypatch):
    """Windows tree semantics (previously ``taskkill /T``) are preserved:
    descendants are enumerated and each re-verified/terminated through its
    own identity-bound token, not a bare PID-only ``taskkill``."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(
        m,
        "_enumerate_descendant_pids_windows",
        lambda pid: ([(5001, 4242, "c1", "root-c"), (5002, 4242, "c2", "root-c")], True),
    )
    verified = {5001: "aaa", 5002: "bbb"}
    monkeypatch.setattr(
        m,
        "_verify_descendant_identity_windows",
        lambda child, parent, child_creation, parent_creation: verified.get(child),
    )
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "root-token")
    terminated = []
    monkeypatch.setattr(
        diagnostics,
        "terminate_pid_if_identity",
        lambda pid, start_time: terminated.append((pid, start_time))
        or {"killed": True, "identity_verified": True, "method": "windows-verified-handle"},
    )
    taskkill_called = []
    import subprocess as sp

    monkeypatch.setattr(sp, "run", lambda *a, **k: taskkill_called.append(a))

    m._kill_pid(4242)

    assert (4242, "root-token") in terminated
    assert (5001, "aaa") in terminated
    assert (5002, "bbb") in terminated
    assert taskkill_called == []  # never a bare taskkill once identity-bound succeeded


def test_kill_pid_windows_census_runs_before_root_kill(monkeypatch):
    """The descendant census must run BEFORE the root is killed -- never
    after -- so the root's own pid (still guaranteed alive and verified at
    census time) cannot be freed and reused by an unrelated process in the
    window between killing it and enumerating its children."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "win32")
    order: list[str] = []
    monkeypatch.setattr(
        m,
        "_enumerate_descendant_pids_windows",
        lambda pid: (order.append("census") or [], True),
    )
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "root-token")
    monkeypatch.setattr(
        diagnostics,
        "terminate_pid_if_identity",
        lambda pid, start_time: order.append("root-kill")
        or {"killed": True, "identity_verified": True, "method": "windows-verified-handle"},
    )

    m._kill_pid(4242)

    assert order == ["census", "root-kill"]


def test_kill_pid_windows_census_failure_still_kills_root_and_warns(monkeypatch, capsys):
    """A failed descendant census (vs. a genuinely empty one) must not be
    silently treated as "no descendants" -- the root is still killed, but
    the gap is surfaced rather than hidden."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(m, "_enumerate_descendant_pids_windows", lambda pid: ([], False))
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "root-token")
    terminated = []
    monkeypatch.setattr(
        diagnostics,
        "terminate_pid_if_identity",
        lambda pid, start_time: terminated.append((pid, start_time))
        or {"killed": True, "identity_verified": True, "method": "windows-verified-handle"},
    )

    m._kill_pid(4242)

    assert terminated == [(4242, "root-token")]
    assert "census failed" in capsys.readouterr().err


def test_verify_descendant_identity_windows_captures_token_before_ancestry(monkeypatch):
    """The identity token must be captured FIRST, before the ancestry/
    generation re-check -- mirroring ``_kill_pid``'s own root-level
    ordering -- so a pid reused during the (slower) WMI lookup is still
    caught by ``terminate_pid_if_identity``'s own final re-verification at
    the kill, rather than this function instead capturing a replacement's
    token."""
    order: list[str] = []
    responses = {5001: (4242, "C1"), 4242: (0, "RootC")}
    monkeypatch.setattr(
        diagnostics, "process_start_time", lambda pid: order.append("token") or "zzz"
    )
    monkeypatch.setattr(
        m,
        "_query_pid_ancestry_windows",
        lambda pid: order.append("ancestry") or responses[pid],
    )

    result = m._verify_descendant_identity_windows(5001, 4242, "C1", "RootC")

    assert result == "zzz"
    assert order == ["token", "ancestry", "ancestry"]


def test_verify_descendant_identity_windows_rejects_parent_mismatch(monkeypatch):
    """A census-recorded ancestry relationship that no longer holds at
    re-check time (the child's current parent differs, e.g. the pid was
    reused) must be rejected -- the already-captured token is discarded,
    never returned to the caller for use in a kill."""
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "zzz")
    monkeypatch.setattr(m, "_query_pid_ancestry_windows", lambda pid: (9999, "C1"))

    result = m._verify_descendant_identity_windows(5001, 4242, "C1", "RootC")

    assert result is None


def test_verify_descendant_identity_windows_rejects_generation_mismatch(monkeypatch):
    """A matching numeric parent pid is NOT sufficient on its own: if
    *child_pid* was reused by an unrelated process between the census and
    now, its current WMI ``CreationDate`` will differ from the
    census-captured one even when the replacement happens to share the
    same parent pid by coincidence -- this generation fingerprint re-check
    is what actually closes that gap."""
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "zzz")
    monkeypatch.setattr(m, "_query_pid_ancestry_windows", lambda pid: (4242, "C2"))

    result = m._verify_descendant_identity_windows(5001, 4242, "C1", "RootC")

    assert result is None


def test_verify_descendant_identity_windows_rejects_stale_parent_generation(monkeypatch):
    """Even when the child's own ancestry (numeric parent pid) and its own
    generation fingerprint both still match, the recorded PARENT's own
    current generation must ALSO still match the census -- this is what
    catches an orphan whose original parent already died and had its pid
    number reused by something else entirely (Windows never live-reparents
    an orphan the way POSIX does, so a bare ancestry-number check alone
    can never detect this)."""
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "zzz")
    calls = {"child": (4242, "C1"), "parent": (999, "DifferentParentGeneration")}
    queried: list[int] = []

    def _fake_query(pid):
        queried.append(pid)
        return calls["child"] if pid == 5001 else calls["parent"]

    monkeypatch.setattr(m, "_query_pid_ancestry_windows", _fake_query)

    result = m._verify_descendant_identity_windows(5001, 4242, "C1", "RootC")

    assert result is None
    assert queried == [5001, 4242]  # child checked first, then its recorded parent


def test_kill_pid_tree_windows_skips_all_termination_when_root_identity_fails(
    monkeypatch,
):
    """If the root's own (non-destructive) identity gate fails -- its
    CURRENT process-start-time no longer matches the token captured at the
    start of ``_kill_pid`` -- NOTHING is terminated: not the root, and not
    any descendant, even though the census (which now runs BEFORE the
    root is touched at all) did complete and return entries."""
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "replacement-token")
    monkeypatch.setattr(
        m,
        "_enumerate_descendant_pids_windows",
        lambda pid: ([(5001, 4242, "c1", "root-c")], True),
    )
    verify_called = []
    monkeypatch.setattr(
        m,
        "_verify_descendant_identity_windows",
        lambda child, parent, child_creation, parent_creation: verify_called.append(child)
        or "zzz",
    )
    terminate_called = []
    monkeypatch.setattr(
        diagnostics,
        "terminate_pid_if_identity",
        lambda pid, st: terminate_called.append((pid, st))
        or {"killed": True, "identity_verified": True, "method": "windows-verified-handle"},
    )

    m._kill_pid_tree_windows_if_identity(4242, "original-token")

    assert verify_called == []  # no descendant identity check, let alone a kill
    assert terminate_called == []  # root itself never reached the kill call either


def test_kill_pid_tree_windows_kills_descendants_deepest_first_then_root(monkeypatch):
    """Descendants must be verified and killed in REVERSE of the census's
    breadth-first order (deepest-first), and the root must be terminated
    LAST -- not first. A prior version killed the root (or an intermediate
    parent) before processing its own children, which broke parent-
    generation verification entirely: once a parent pid is dead,
    re-querying its current identity returns nothing, and every one of
    its children would be (incorrectly) rejected as unverifiable."""
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "root-token")
    monkeypatch.setattr(
        m,
        "_enumerate_descendant_pids_windows",
        lambda pid: (
            [
                (5001, 4242, "c1", "root-c"),  # level 1
                (6001, 5001, "c2", "c1"),  # level 2 (appended after level 1 by BFS)
            ],
            True,
        ),
    )
    order: list[int] = []
    monkeypatch.setattr(
        m,
        "_verify_descendant_identity_windows",
        lambda child, parent, child_creation, parent_creation: order.append(child) or "zzz",
    )
    monkeypatch.setattr(
        diagnostics,
        "terminate_pid_if_identity",
        lambda pid, st: order.append(pid)
        or {"killed": True, "identity_verified": True, "method": "windows-verified-handle"},
    )

    m._kill_pid_tree_windows_if_identity(4242, "root-token")

    # Each descendant produces two entries (verify, then kill) back-to-back;
    # the deepest descendant (6001) must be fully handled before the
    # shallower one (5001), and the root (4242) must be last of all.
    assert order == [6001, 6001, 5001, 5001, 4242]


def _spawn_sleeper() -> subprocess.Popen:
    """A real, test-owned child process that outlives the test unless
    explicitly terminated -- used to exercise the actual OS-bound
    termination primitive (a Windows handle / POSIX pidfd), not a mock."""
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


_IDENTITY_TERMINATION_UNAVAILABLE_REASON = (
    "no identity-bound termination primitive on this platform (e.g. "
    "macOS/BSD, or a Linux kernel without pidfd support) -- "
    "terminate_pid_if_identity/process_start_time intentionally return "
    "None/unavailable there, which these direct-primitive tests would "
    "otherwise misreport as a safety failure"
)


@pytest.mark.skipif(
    not m._identity_termination_available(), reason=_IDENTITY_TERMINATION_UNAVAILABLE_REASON
)
def test_terminate_pid_if_identity_kills_a_real_process_with_matching_token():
    """Direct, unmocked exercise of the actual OS-bound termination
    primitive: a real child process, terminated through
    ``zdd.diagnostics.terminate_pid_if_identity`` using its own freshly
    captured ``process_start_time`` token, must actually die."""
    proc = _spawn_sleeper()
    try:
        start_time = diagnostics.process_start_time(proc.pid)
        assert start_time is not None  # the primitive must be available on this runner

        result = diagnostics.terminate_pid_if_identity(proc.pid, start_time)

        assert result.get("identity_verified") is True
        assert result.get("killed") is True
        proc.wait(timeout=10)
        assert proc.poll() is not None  # the real process actually exited
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


@pytest.mark.skipif(
    not m._identity_termination_available(), reason=_IDENTITY_TERMINATION_UNAVAILABLE_REASON
)
def test_terminate_pid_if_identity_leaves_a_real_process_alive_with_stale_token():
    """A stale/wrong start-time token (simulating pid reuse: the token
    belongs to a different process generation than the live one) must
    leave the real, live process untouched -- the actual safety guarantee
    this whole PR exists to provide, exercised against the real OS
    primitive rather than a mocked result."""
    proc = _spawn_sleeper()
    try:
        real_start_time = diagnostics.process_start_time(proc.pid)
        assert real_start_time is not None
        stale_token = "0" if real_start_time != "0" else "1"

        result = diagnostics.terminate_pid_if_identity(proc.pid, stale_token)

        assert result.get("killed") is False
        assert result.get("identity_verified") is False
        time.sleep(0.2)  # give a mistaken kill signal time to land, if one were sent
        assert proc.poll() is None  # the real process is still alive
    finally:
        proc.kill()
        proc.wait(timeout=10)
