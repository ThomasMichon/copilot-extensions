"""Lazy on-demand coordinator start: gating + no-double-start.

On an interactive-required host the coordinator runs from a non-elevated logon
auto-start; a fresh session (or one that outlived a service restart) may issue a
dispatch before that is up. The CLI therefore lazily starts a local coordinator
when none answers -- but never for an explicit remote target, on a WSL guest, or
when opted out, and never a *second* one when a live coordinator already answers.
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
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "https://gateway/dispatch")
    calls = _record_lazy(monkeypatch)
    m._ensure_local_coordinator(_args(["--shared", "list"]))
    assert calls == []


def test_ensure_skips_when_opted_out(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_NO_AUTOSTART", "1")
    calls = _record_lazy(monkeypatch)
    m._ensure_local_coordinator(_args(["list"]))
    assert calls == []


def test_ensure_skips_on_wsl(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_NO_AUTOSTART", raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: True)
    calls = _record_lazy(monkeypatch)
    m._ensure_local_coordinator(_args(["list"]))
    assert calls == []


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


def test_spawn_coordinator_uses_c_entry_not_runpy(monkeypatch, tmp_path):
    """Regression: the detached coordinator launch must use a ``-c`` entry, not
    ``python -m agent_dispatch serve``. Under a Windows venv the ``-m`` runpy form
    re-execs the base interpreter, spawning a redundant system-Python coordinator
    child of an idle venv launcher (see ``_SERVE_ENTRY``)."""
    captured: list[list[str]] = []

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            captured.append(argv)

    monkeypatch.setattr(m.subprocess, "Popen", _FakePopen)
    # A home without a venv -> python resolves to sys.executable (deterministic);
    # the log open + service.env read land under the temp install dir.
    monkeypatch.setattr(m.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".agent-dispatch").mkdir()

    m._spawn_coordinator_process()

    assert captured, "expected a detached coordinator Popen launch"
    argv = captured[0]
    assert argv[1] == "-c" and argv[2] == m._SERVE_ENTRY, (
        f"coordinator must launch via `-c _SERVE_ENTRY`, got {argv!r}"
    )
    assert "-m" not in argv, (
        "coordinator must not launch via `-m` (Windows venv runpy re-exec)"
    )
    assert "main(['serve'])" in m._SERVE_ENTRY
