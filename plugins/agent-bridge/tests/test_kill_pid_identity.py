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

import signal

import agent_bridge._kill_pid_identity as m
from zdd import diagnostics


def _allow_pidfd(monkeypatch, *, self_probe_ok: bool = True) -> None:
    """Make ``_identity_termination_available()`` see a working pidfd
    primitive (attribute present AND a successful self-probe)."""
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda fd, sig: None, raising=False)
    if self_probe_ok:
        monkeypatch.setattr(m.os, "pidfd_open", lambda pid, flags=0: 7, raising=False)
    else:
        def _raise(pid, flags=0):
            raise OSError("ENOSYS")

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


def test_kill_pid_windows_terminates_root_and_live_descendants(monkeypatch):
    """Windows tree semantics (previously ``taskkill /T``) are preserved:
    descendants are enumerated and each re-verified/terminated through its
    own identity-bound token, not a bare PID-only ``taskkill``."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(
        m,
        "_enumerate_descendant_pids_windows",
        lambda pid: ([(5001, 4242), (5002, 4242)], True),
    )
    verified = {5001: "aaa", 5002: "bbb"}
    monkeypatch.setattr(
        m, "_verify_descendant_identity_windows", lambda child, parent: verified.get(child)
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


def test_verify_descendant_identity_windows_rejects_parent_mismatch(monkeypatch):
    """A census-recorded ancestry relationship that no longer holds at
    re-check time (the child's current parent differs, e.g. the pid was
    reused) must be rejected -- never falling through to capture and
    trust a stale/wrong identity token."""
    import subprocess as sp

    class _Result:
        returncode = 0
        stdout = "9999"  # a different parent than recorded

    monkeypatch.setattr(sp, "run", lambda *a, **k: _Result())
    token_calls = []
    monkeypatch.setattr(
        diagnostics, "process_start_time", lambda pid: token_calls.append(pid) or "zzz"
    )

    result = m._verify_descendant_identity_windows(5001, 4242)

    assert result is None
    assert token_calls == []  # never even attempted once ancestry disagreed
