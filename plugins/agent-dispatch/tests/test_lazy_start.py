"""Lazy on-demand coordinator start: gating + no-double-start.

On an interactive-required host the coordinator runs from a non-elevated logon
auto-start; a fresh session (or one that outlived a service restart) may issue a
dispatch before that is up. The CLI therefore lazily starts a local coordinator
when none answers -- but never for an explicit remote target, on a WSL guest
opted into Windows-client mode, or when opted out, and never a *second* one when
a live coordinator already answers.
"""

from __future__ import annotations

import agent_dispatch.__main__ as m


def _args(argv):
    return m.build_parser().parse_args(argv)


def _record_lazy(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(m, "_lazy_start_coordinator", lambda **_k: calls.append(True) or True)
    return calls


def test_ensure_skips_explicit_url(monkeypatch):
    calls = _record_lazy(monkeypatch)
    m._ensure_local_coordinator(_args(["--url", "http://direct:9847", "list"]))
    assert calls == []


def test_ensure_skips_shared(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "https://coordinator.example/dispatch")
    calls = _record_lazy(monkeypatch)
    m._ensure_local_coordinator(_args(["--shared", "list"]))
    assert calls == []


def test_ensure_skips_when_opted_out(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_NO_AUTOSTART", "1")
    calls = _record_lazy(monkeypatch)
    m._ensure_local_coordinator(_args(["list"]))
    assert calls == []


def test_ensure_skips_on_wsl_windows_client_optin(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_NO_AUTOSTART", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_WSL_WINDOWS_CLIENT", "1")
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: True)
    calls = _record_lazy(monkeypatch)
    m._ensure_local_coordinator(_args(["list"]))
    assert calls == []


def test_ensure_starts_on_wsl_by_default(monkeypatch):
    # Per-environment ownership: a WSL guest (no Windows-client opt-in) autostarts
    # its OWN coordinator, exactly like a standalone Linux host.
    monkeypatch.delenv("AGENT_DISPATCH_NO_AUTOSTART", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_WSL_WINDOWS_CLIENT", raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: True)
    calls = _record_lazy(monkeypatch)
    m._ensure_local_coordinator(_args(["list"]))
    assert calls == [True]


def test_ensure_starts_local_when_eligible(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_NO_AUTOSTART", raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: False)
    calls = _record_lazy(monkeypatch)
    m._ensure_local_coordinator(_args(["list"]))
    assert calls == [True]


def test_ensure_swallows_lazy_start_failure(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_NO_AUTOSTART", raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: False)

    def _boom(**_k):
        raise RuntimeError("spawn blew up")

    monkeypatch.setattr(m, "_lazy_start_coordinator", _boom)
    # Must not propagate -- the command itself will fail loudly if truly down.
    m._ensure_local_coordinator(_args(["list"]))


def test_lazy_start_noop_when_live(monkeypatch):
    spawned: list[bool] = []
    monkeypatch.setattr("agent_dispatch.config.has_live_local_coordinator", lambda: True)
    monkeypatch.setattr(m, "_spawn_coordinator_process", lambda: spawned.append(True))
    assert m._lazy_start_coordinator(timeout=1.0) is True
    assert spawned == []  # a live coordinator answered -> never spawn a second


def test_lazy_start_spawns_when_absent(monkeypatch, tmp_path):
    spawned: list[bool] = []
    # Never live -> starter spawns once, then we time out fast (still not live).
    monkeypatch.setattr("agent_dispatch.config.has_live_local_coordinator", lambda: False)
    monkeypatch.setattr("agent_dispatch.config.run_dir", lambda: tmp_path)
    monkeypatch.setattr(m, "_spawn_coordinator_process", lambda: spawned.append(True))
    assert m._lazy_start_coordinator(timeout=0.5) is False
    assert spawned == [True]
