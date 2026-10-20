"""Tests for the `status-context` left status-bar segment."""

from __future__ import annotations

import argparse

import pytest

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
        m, "_find_record_for_path", lambda _p: _record(machine="raw-host-01")
    )
    monkeypatch.setattr(repos_module, "resolve_path", lambda name: "/repo/test-chamber")
    entry = m.cfg.MachineEntry(
        key="owner_user-box1",
        display_name="box1",
        environment="Windows",
        alias="box1",
        hostname="raw-host-01",
    )
    monkeypatch.setattr(m.cfg, "load_machines_yaml", lambda repo_dir, **_kwargs: {"owner_user-box1": entry})
    rc = m.cmd_status_context(_ns())
    assert rc == 0
    assert capsys.readouterr().out.strip() == "box1  win  test-chamber:8e45"


def test_status_context_alias_resolution_fails_open(monkeypatch, capsys):
    """No resolvable repo_dir / no machines.yaml -> the raw value renders
    unchanged rather than raising or blanking the segment."""
    monkeypatch.setattr(
        m, "_find_record_for_path", lambda _p: _record(machine="raw-host-01")
    )
    monkeypatch.setattr(repos_module, "resolve_path", lambda name: None)
    monkeypatch.setattr(m.worktree_identity, "_find_repo_dir", lambda: None)
    rc = m.cmd_status_context(_ns())
    assert rc == 0
    assert capsys.readouterr().out.strip() == "raw-host-01  win  test-chamber:8e45"


@pytest.mark.parametrize(
    ("platform", "badge"),
    [
        ("windows", "win"),
        ("wsl", "wsl"),
    ],
)
def test_status_context_disambiguates_physical_host_using_record_platform(
    monkeypatch, capsys, platform, badge,
):
    entries = {
        name: m.cfg.MachineEntry(
            key=name,
            display_name="host",
            hostname="raw-host-01",
            execution_platform=environment,
            physical_host="host-family",
        )
        for name, environment in (("friendly-host", "windows"), ("friendly-host-wsl", "wsl"))
    }
    with pytest.raises(ValueError, match="physical-host metadata"):
        m.cfg.find_machine_entry(entries, "RAW-HOST-01")
    monkeypatch.setattr(
        m, "_find_record_for_path",
        lambda _p: _record(machine="RAW-HOST-01", platform=platform),
    )
    monkeypatch.setattr(m.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(repos_module, "resolve_path", lambda _name: "/repo/test-chamber")
    monkeypatch.setattr(m.cfg, "load_machines_yaml", lambda *_a, **_k: entries)
    assert m.cmd_status_context(_ns()) == 0
    assert capsys.readouterr().out.strip() == f"host-family  {badge}  test-chamber:8e45"


def test_status_context_preserves_ambiguous_physical_host(monkeypatch, capsys):
    entries = {
        name: m.cfg.MachineEntry(
            key=name,
            display_name=name,
            hostname="raw-host-01",
            execution_platform="windows",
            physical_host="host-family",
        )
        for name in ("host-one", "host-two")
    }
    monkeypatch.setattr(m, "_find_record_for_path", lambda _p: _record(machine="raw-host-01"))
    monkeypatch.setattr(repos_module, "resolve_path", lambda _name: "/repo/test-chamber")
    monkeypatch.setattr(m.cfg, "load_machines_yaml", lambda *_a, **_k: entries)
    assert m.cmd_status_context(_ns()) == 0
    assert capsys.readouterr().out.strip() == "raw-host-01  win  test-chamber:8e45"


@pytest.mark.parametrize("physical_host", ["declared-host", ""])
@pytest.mark.parametrize("source", ["tracked", "detected"])
def test_status_context_separates_host_display_from_opaque_execution_identity(
    monkeypatch, capsys, physical_host, source,
):
    record = _record(machine="literal-wsl", platform="wsl")
    entry = m.cfg.MachineEntry(
        key="literal-wsl", display_name="Guest", execution_platform="wsl",
        physical_host=physical_host,
    )
    monkeypatch.setattr(m, "_find_record_for_path",
                        lambda _p: record if source == "tracked" else None)
    monkeypatch.setattr(m.cfg, "detect_machine", lambda: "literal-wsl")
    monkeypatch.setattr(m.cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(repos_module, "resolve_path", lambda _name: "/repo/test-chamber")
    monkeypatch.setattr(m.worktree_identity, "_find_repo_dir", lambda: "/repo/test-chamber")
    monkeypatch.setattr(m.cfg, "load_machines_yaml", lambda *_a, **_k: {"literal-wsl": entry})
    assert m.cmd_status_context(_ns()) == 0
    locus = "  test-chamber:8e45" if source == "tracked" else ""
    assert capsys.readouterr().out.strip() == (
        f"{physical_host or 'literal-wsl'}  wsl{locus}")
    assert record.machine == entry.key == "literal-wsl"
    assert record.worktree_id.endswith("-8e45")


@pytest.mark.parametrize("identity,suffix", [
    ("host-wsl-20260101-010101-0123456789abcdef0123456789abcdef", "cdef"),
    ("external.id.without-hyphen", "phen"),
    ("abc", "abc"),
])
@pytest.mark.parametrize("plain", [True, False])
def test_status_context_bounds_external_worktree_identifier_to_four_characters(
    monkeypatch, capsys, identity, suffix, plain,
):
    record = _record(worktree_id=identity)
    monkeypatch.setattr(m, "_find_record_for_path", lambda _p: record)
    assert m.cmd_status_context(_ns(plain=plain)) == 0
    result = capsys.readouterr().out.strip()
    assert f"test-chamber:{suffix}" in result
    if len(identity) > 4:
        assert identity not in result
    assert record.worktree_id == identity
