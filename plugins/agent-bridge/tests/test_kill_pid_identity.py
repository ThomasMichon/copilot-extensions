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


def test_kill_pid_uses_identity_verified_termination(monkeypatch):
    """When the platform's identity-bound primitive verifies the pid, no
    legacy signal/taskkill fallback is attempted."""
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "123456")
    calls = []
    monkeypatch.setattr(
        diagnostics,
        "terminate_pid_if_identity",
        lambda pid, start_time: calls.append((pid, start_time))
        or {"killed": True, "identity_verified": True, "method": "fake-verified"},
    )
    fallback_called = []
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setattr(
        m.os,
        "kill",
        lambda pid, sig: fallback_called.append((pid, sig)),
    )

    m._kill_pid(4242)

    assert calls == [(4242, "123456")]
    assert fallback_called == []  # no legacy fallback once identity was verified


def test_kill_pid_skips_on_identity_mismatch(monkeypatch):
    """A genuine identity mismatch (the pid was reused) must never fall back
    to a legacy signal/taskkill -- that is the exact hazard this closes."""
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
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setattr(
        m.os,
        "kill",
        lambda pid, sig: fallback_called.append((pid, sig)),
    )

    m._kill_pid(4242)

    assert fallback_called == []


def test_kill_pid_falls_back_when_identity_primitive_unavailable(monkeypatch):
    """A platform lacking the identity-bound primitive (e.g. non-Linux POSIX
    without pidfd) keeps the prior unconditional-kill guarantee."""
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "123456")
    monkeypatch.setattr(
        diagnostics,
        "terminate_pid_if_identity",
        lambda pid, start_time: {
            "killed": False,
            "identity_verified": False,
            "method": "pidfd-unavailable",
        },
    )
    fallback_called = []
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setattr(
        m.os,
        "kill",
        lambda pid, sig: fallback_called.append((pid, sig)),
    )

    m._kill_pid(4242)

    assert fallback_called and fallback_called[0][0] == 4242


def test_kill_pid_falls_back_when_no_start_time_available(monkeypatch):
    """No identity token at all (process already gone, or platform lacks the
    primitive entirely) also falls back to the legacy kill."""
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: None)
    monkeypatch.setattr(m.sys, "platform", "linux")
    fallback_called = []
    monkeypatch.setattr(
        m.os,
        "kill",
        lambda pid, sig: fallback_called.append((pid, sig)),
    )

    m._kill_pid(4242)

    assert fallback_called and fallback_called[0][0] == 4242
