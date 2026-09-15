"""Tests for agent-ssh's shared reachability probe (probe.py)."""

from __future__ import annotations

from types import SimpleNamespace

from agent_ssh import probe


def test_probe_alias_reachable(monkeypatch) -> None:
    monkeypatch.setattr(
        probe.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0)
    )
    assert probe.probe_alias("host-a", 8) is True


def test_probe_alias_unreachable(monkeypatch) -> None:
    monkeypatch.setattr(
        probe.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=255)
    )
    assert probe.probe_alias("host-a", 8) is False


def test_probe_alias_uses_shell_agnostic_noop(monkeypatch) -> None:
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    probe.probe_alias("host-a", 5)
    assert captured["argv"][-2:] == ["host-a", "exit 0"]
    assert "ConnectTimeout=5" in captured["argv"]
