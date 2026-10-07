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


def test_kill_pid_windows_terminates_root_and_live_descendants(monkeypatch):
    """Windows tree semantics (previously ``taskkill /T``) are preserved:
    descendants are enumerated and each re-verified/terminated through its
    own identity-bound token, not a bare PID-only ``taskkill``."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(
        m,
        "_enumerate_descendant_pids_windows",
        lambda pid: ([(5001, 4242, "c1"), (5002, 4242, "c2")], True),
    )
    verified = {5001: "aaa", 5002: "bbb"}
    monkeypatch.setattr(
        m,
        "_verify_descendant_identity_windows",
        lambda child, parent, creation: verified.get(child),
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
    import subprocess as sp

    class _Result:
        returncode = 0
        stdout = "4242\tC1"  # matches recorded parent + creation date

    order: list[str] = []
    monkeypatch.setattr(
        diagnostics, "process_start_time", lambda pid: order.append("token") or "zzz"
    )
    monkeypatch.setattr(sp, "run", lambda *a, **k: order.append("ancestry") or _Result())

    result = m._verify_descendant_identity_windows(5001, 4242, "C1")

    assert result == "zzz"
    assert order == ["token", "ancestry"]


def test_verify_descendant_identity_windows_rejects_parent_mismatch(monkeypatch):
    """A census-recorded ancestry relationship that no longer holds at
    re-check time (the child's current parent differs, e.g. the pid was
    reused, or an ancestor died and Windows reparented it) must be
    rejected -- the already-captured token is discarded, never returned to
    the caller for use in a kill."""
    import subprocess as sp

    class _Result:
        returncode = 0
        stdout = "9999\tC1"  # a different parent than recorded

    monkeypatch.setattr(sp, "run", lambda *a, **k: _Result())
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "zzz")

    result = m._verify_descendant_identity_windows(5001, 4242, "C1")

    assert result is None


def test_verify_descendant_identity_windows_rejects_generation_mismatch(monkeypatch):
    """A matching numeric parent pid is NOT sufficient on its own: if
    *child_pid* was reused by an unrelated process between the census and
    now, its current WMI ``CreationDate`` will differ from the
    census-captured one even when the replacement happens to share the
    same parent pid by coincidence -- this generation fingerprint re-check
    is what actually closes that gap."""
    import subprocess as sp

    class _Result:
        returncode = 0
        stdout = "4242\tC2"  # parent matches, but creation date does not

    monkeypatch.setattr(sp, "run", lambda *a, **k: _Result())
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "zzz")

    result = m._verify_descendant_identity_windows(5001, 4242, "C1")

    assert result is None


def test_kill_pid_tree_windows_skips_descendant_termination_when_root_identity_fails(
    monkeypatch,
):
    """If the root's own identity verification fails (a reused pid), no
    descendant must be terminated -- even though the census (which now
    runs BEFORE the root kill, precisely to avoid being fooled by the
    root's own pid being freed and reused) did complete and return
    entries."""
    monkeypatch.setattr(
        diagnostics,
        "terminate_pid_if_identity",
        lambda pid, st: {
            "killed": False,
            "identity_verified": False,
            "method": "windows-handle-identity-mismatch",
        },
    )
    monkeypatch.setattr(
        m, "_enumerate_descendant_pids_windows", lambda pid: ([(5001, 4242, "c1")], True)
    )
    verify_called = []
    monkeypatch.setattr(
        m,
        "_verify_descendant_identity_windows",
        lambda child, parent, creation: verify_called.append(child) or "zzz",
    )

    m._kill_pid_tree_windows_if_identity(4242, "stale-token")

    assert verify_called == []  # no descendant identity check, let alone a kill


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
