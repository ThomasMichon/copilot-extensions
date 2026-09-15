"""Tests for the `status-context` left status-bar segment."""

from __future__ import annotations

import argparse

from agent_worktrees import __main__ as m
from agent_worktrees import repos as repos_module
from agent_worktrees import tracking


def _ns(**kw):
    base = {"path": None, "plain": True}
    base.update(kw)
    return argparse.Namespace(**base)


def _record(**kw):
    base = dict(
        worktree_id="anomalous-potato-win-20260625-221940-8e45",
        branch="worktree/anomalous-potato-win-20260625-221940-8e45",
        worktree_path="/w/anomalous-potato-win-20260625-221940-8e45",
        repo="test-chamber",
        machine="anomalous-potato",
        platform="windows",
        started_at="",
        last_resumed_at="",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
    )
    base.update(kw)
    return tracking.WorktreeRecord(**base)


def test_status_context_registered():
    assert m.COMMAND_MAP["status-context"] is m.cmd_status_context
    assert m._WORKTREE_VERBS["status-context"] == "status-context"


def test_platform_short_mapping():
    assert m._platform_short("windows") == "win"
    assert m._platform_short("wsl") == "wsl"
    assert m._platform_short("linux") == "linux"


def test_status_context_plain_with_record(monkeypatch, capsys):
    monkeypatch.setattr(m, "_find_record_for_path", lambda _p: _record())
    rc = m.cmd_status_context(_ns())
    assert rc == 0
    assert capsys.readouterr().out.strip() == "anomalous-potato  win  test-chamber:8e45"


def test_status_context_styled_with_record(monkeypatch, capsys):
    monkeypatch.setattr(m, "_find_record_for_path", lambda _p: _record())
    rc = m.cmd_status_context(_ns(plain=False))
    assert rc == 0
    out = capsys.readouterr().out
    # Identity values present, wrapped in tmux style directives.
    assert "anomalous-potato" in out
    assert "win" in out
    assert "test-chamber:8e45" in out
    assert "#[fg=" in out and "#[default]" in out
    # Environment renders as an OS-colored badge; no pipe delimiters.
    assert f"bg={m._ENV_BG['win']}" in out
    assert "|" not in out


def test_status_context_fallback_no_record(monkeypatch, capsys):
    monkeypatch.setattr(m, "_find_record_for_path", lambda _p: None)
    monkeypatch.setattr(m.cfg, "detect_machine", lambda *a, **k: "emancipation-cube")
    monkeypatch.setattr(m.cfg, "detect_platform", lambda: "wsl")
    rc = m.cmd_status_context(_ns())
    assert rc == 0
    # No record -> machine + env only, repo:id4 omitted.
    assert capsys.readouterr().out.strip() == "emancipation-cube  wsl"


def test_status_context_resolves_machine_alias_from_tracked_record(monkeypatch, capsys):
    """Regression: a worktree's tracking record freezes ``machine`` at
    registration time (a raw COMPUTERNAME), and this segment never re-resolved
    it through machines.yaml's hostname/alias mapping the way ``machine-context``
    does -- so a shared-pool box whose COMPUTERNAME differs from its canonical
    mesh alias (dotfiles machines.yaml's decoupled-hostname convention) showed
    the raw hostname in the mux forever, even after the alias mapping existed."""
    monkeypatch.setattr(
        m, "_find_record_for_path", lambda _p: _record(machine="cpc-tmich-oixui")
    )
    monkeypatch.setattr(repos_module, "resolve_path", lambda name: "/repo/test-chamber")
    entry = m.cfg.MachineEntry(
        key="tmichon-augloop1",
        display_name="augloop1",
        environment="Windows",
        alias="augloop1",
        hostname="cpc-tmich-oixui",
    )
    monkeypatch.setattr(m.cfg, "load_machines_yaml", lambda repo_dir: {"tmichon-augloop1": entry})
    rc = m.cmd_status_context(_ns())
    assert rc == 0
    assert capsys.readouterr().out.strip() == "augloop1  win  test-chamber:8e45"


def test_status_context_alias_resolution_fails_open(monkeypatch, capsys):
    """No resolvable repo_dir / no machines.yaml -> the raw value renders
    unchanged rather than raising or blanking the segment."""
    monkeypatch.setattr(
        m, "_find_record_for_path", lambda _p: _record(machine="cpc-tmich-oixui")
    )
    monkeypatch.setattr(repos_module, "resolve_path", lambda name: None)
    monkeypatch.setattr(m, "_find_repo_dir", lambda: None)
    rc = m.cmd_status_context(_ns())
    assert rc == 0
    assert capsys.readouterr().out.strip() == "cpc-tmich-oixui  win  test-chamber:8e45"
