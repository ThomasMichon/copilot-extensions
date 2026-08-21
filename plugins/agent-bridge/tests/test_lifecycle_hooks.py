"""Tests for the daemon lifecycle hooks (startup sweep + start/stop records)."""

from __future__ import annotations

from pathlib import Path

from agent_bridge import lifecycle_hooks
from zdd import lifecycle, routing


def test_record_start_writes_start(tmp_path: Path):
    assert lifecycle_hooks.record_start(tmp_path, "0.4.0-dev321", 9999) is True
    ev = lifecycle.read_events(tmp_path)
    assert len(ev) == 1
    assert ev[0]["action"] == "start"
    assert ev[0]["service"] == "agent-bridge"
    assert ev[0]["port"] == 9999


def test_record_stop_writes_stop(tmp_path: Path):
    lifecycle_hooks.record_stop(tmp_path)
    ev = lifecycle.read_events(tmp_path)
    assert any(e["action"] == "stop" and e["service"] == "agent-bridge" for e in ev)


def test_startup_sweep_invokes_reap(tmp_path: Path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(routing, "reap_stale_active",
                        lambda *a, **k: calls.append(k.get("service")))
    lifecycle_hooks.startup_sweep(tmp_path)
    assert calls == ["agent-bridge"]


def test_hooks_are_fail_open(tmp_path: Path):
    # A config dir whose parent is a file makes the underlying record/sweep fail;
    # none of the hooks may raise, and record_start reports the failed write.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    under = blocker / "under"
    assert lifecycle_hooks.record_start(under, "1.0", 1) is False
    lifecycle_hooks.record_stop(under)     # no raise
    lifecycle_hooks.startup_sweep(under)   # no raise
