"""``_kill_pid`` must route through an identity-bound OS object when possible.

Regression/hardening: closes copilot-extensions#5006 for ``service_process_cli``'s
``_kill_pid`` -- the check-then-bare-kill window between a caller's
``_pid_is_agent_bridge`` re-verification and the actual signal/``taskkill`` could
let a pid the OS has since reused for an unrelated process be killed in the
verified process's place. Mirrors ``worktree_manager.mux_daemon_cutover``'s
analogous fix (#5060): capture a fresh ``process_start_time`` token immediately
before the kill and terminate through ``zdd.diagnostics.terminate_pid_if_identity``.
"""

from __future__ import annotations

import agent_bridge.service_process_cli as m
from zdd import diagnostics


def test_kill_pid_skips_entirely_when_ownership_recheck_fails(monkeypatch):
    """``_kill_pid`` re-verifies ownership itself (not just trusting the
    caller's earlier check) -- closing the window between that caller
    check and this function's own identity-token capture."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: False)
    calls = []
    monkeypatch.setattr(
        diagnostics, "process_start_time", lambda pid: calls.append(pid) or "123456"
    )

    m._kill_pid(4242)

    assert calls == []  # never even attempted to capture a token


def test_kill_pid_linux_uses_identity_verified_termination(monkeypatch):
    """On Linux (pidfd available), the verified pid is terminated through
    the identity-bound primitive, with no legacy signal fallback."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "linux")
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
    import signal

    monkeypatch.setattr(signal, "pidfd_send_signal", lambda fd, sig: None, raising=False)
    monkeypatch.setattr(m.os, "pidfd_open", lambda pid, flags=0: 7, raising=False)

    m._kill_pid(4242)

    assert calls == [(4242, "123456")]
    assert fallback_called == []


def test_kill_pid_linux_skips_on_identity_mismatch(monkeypatch):
    """A genuine identity mismatch (the pid was reused) must never fall
    back to a legacy signal -- that is the exact hazard this closes."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "linux")
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
    import signal

    monkeypatch.setattr(signal, "pidfd_send_signal", lambda fd, sig: None, raising=False)
    monkeypatch.setattr(m.os, "pidfd_open", lambda pid, flags=0: 7, raising=False)

    m._kill_pid(4242)

    assert fallback_called == []


def test_kill_pid_linux_never_falls_back_on_per_pid_lookup_failure(monkeypatch):
    """A per-pid lookup failure (pidfd couldn't be opened for this specific
    pid, e.g. it already exited) is NOT platform incapability -- it must
    not fall back to a bare, unverified kill either, since a pid reused in
    exactly that window is the hazard this closes."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: None)
    fallback_called = []
    monkeypatch.setattr(m.os, "kill", lambda pid, sig: fallback_called.append((pid, sig)))
    import signal

    monkeypatch.setattr(signal, "pidfd_send_signal", lambda fd, sig: None, raising=False)
    monkeypatch.setattr(m.os, "pidfd_open", lambda pid, flags=0: 7, raising=False)

    m._kill_pid(4242)

    assert fallback_called == []


def test_kill_pid_falls_back_only_when_platform_lacks_identity_primitive(monkeypatch):
    """A platform with no identity-bound primitive AT ALL (e.g. macOS/BSD,
    no ``pidfd_open``) keeps the prior unconditional-kill guarantee."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "darwin")
    monkeypatch.delattr(m.os, "pidfd_open", raising=False)
    fallback_called = []
    monkeypatch.setattr(m.os, "kill", lambda pid, sig: fallback_called.append((pid, sig)))

    m._kill_pid(4242)

    assert fallback_called and fallback_called[0][0] == 4242


def test_kill_pid_windows_terminates_root_and_live_descendants(monkeypatch):
    """Windows tree semantics (previously ``taskkill /T``) are preserved:
    descendants are enumerated and each terminated through its own
    identity-bound token, not a bare PID-only ``taskkill``."""
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(
        m, "_enumerate_descendant_pids_windows", lambda pid: [(5001, "aaa"), (5002, "bbb")]
    )

    start_times = {4242: "root-token"}
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: start_times.get(pid))
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
