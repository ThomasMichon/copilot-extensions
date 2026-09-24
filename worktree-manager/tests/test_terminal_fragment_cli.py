"""Tests for the Phase 3e Step 4/5 CLI surface (terminal-fragment / profiles).

Exercises ``worktree_manager.__main__``'s ``terminal-fragment``/``profiles``
commands end-to-end via ``main()`` against a synthetic ``USERPROFILE`` home
(matching ``test_harness_state.py``'s own fixture style), proving Phase 3e's
relocated ``terminal_fragment``/``terminal_profiles``/``harness_state``
modules are wired correctly through the CLI dispatch -- not just importable.

Step 5 added the real deploy/mirror mechanism (``terminal-fragment --deploy``,
``profiles apply --mirror``), always previewed first and gated behind an
explicit ``--live`` flag: without it, both commands compute the full plan but
never touch disk, and ``profiles apply`` without ``--mirror`` at all keeps its
pre-Step-5 behaviour exactly (``mirrored: false``, no plan computed).
"""
from __future__ import annotations

import json

from worktree_manager.__main__ import main


def _make_home(tmp_path, *, machine="book2"):
    awt = tmp_path / ".agent-worktrees"
    awt.mkdir(parents=True)
    anchor = tmp_path / "checkout"
    (anchor / ".agent-worktrees").mkdir(parents=True)
    (anchor / ".agent-worktrees" / "machines.yaml").write_text(
        "machines:\n"
        f"  {machine}:\n"
        f"    display_name: {machine.title()}\n",
        encoding="utf-8",
    )
    (awt / "projects.yaml").write_text(
        "schema_version: 2\n"
        "projects:\n"
        "  myproj:\n"
        f"    anchor: \"{str(anchor).replace(chr(92), chr(92) * 2)}\"\n",
        encoding="utf-8",
    )
    (awt / "repos.yaml").write_text("schema_version: 1\nrepos: {}\n", encoding="utf-8")
    proj_cfg = tmp_path / ".myproj"
    proj_cfg.mkdir()
    (proj_cfg / "config.yaml").write_text(f"machine: {machine}\n", encoding="utf-8")
    return tmp_path


def _run(monkeypatch, tmp_path, args):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return main(args)


def test_terminal_fragment_json_default(monkeypatch, tmp_path, capsys):
    _make_home(tmp_path)
    assert _run(monkeypatch, tmp_path, ["terminal-fragment", "myproj"]) == 0
    out = capsys.readouterr().out
    fragment = json.loads(out)
    assert any(p["name"] == "Myproj" for p in fragment["profiles"])
    assert fragment["schemes"][0]["name"] == "Aperture Science"


def test_terminal_fragment_explain(monkeypatch, tmp_path, capsys):
    _make_home(tmp_path)
    assert _run(monkeypatch, tmp_path, ["terminal-fragment", "myproj", "--explain"]) == 0
    out = capsys.readouterr().out
    assert "myproj" in out
    assert "unmanaged -> default column" in out


def test_terminal_fragment_requires_resolvable_machine(monkeypatch, tmp_path, capsys):
    tmp_path.joinpath(".agent-worktrees").mkdir()
    assert _run(monkeypatch, tmp_path, ["terminal-fragment", "no-such-project"]) == 2
    assert "could not resolve" in capsys.readouterr().out


def test_profiles_get_reports_default_column(monkeypatch, tmp_path, capsys):
    _make_home(tmp_path)
    assert _run(monkeypatch, tmp_path, ["profiles", "myproj", "get", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["machine"] == "book2"
    assert payload["managed"] is False
    assert any(t["kind"] == "agent" for t in payload["targets"])


def test_profiles_apply_then_get_roundtrips(monkeypatch, tmp_path, capsys):
    _make_home(tmp_path)
    rc = _run(monkeypatch, tmp_path, [
        "profiles", "myproj", "apply",
        "--set", json.dumps([{"machine": "other", "env": "Win", "kind": "shell"}]),
        "--json",
    ])
    assert rc == 0
    applied = json.loads(capsys.readouterr().out)
    # Without --mirror, apply persists the selection but never mirrors it
    # (Phase 3e Step 5's opt-in path -- see the --mirror tests below).
    assert applied["mirrored"] is False
    assert any(t["machine"] == "other" for t in applied["targets"])

    assert _run(monkeypatch, tmp_path, ["profiles", "myproj", "get", "--json"]) == 0
    fetched = json.loads(capsys.readouterr().out)
    assert fetched["managed"] is True
    assert any(t["machine"] == "other" for t in fetched["targets"])


def test_profiles_apply_requires_set(monkeypatch, tmp_path, capsys):
    _make_home(tmp_path)
    rc = _run(monkeypatch, tmp_path, ["profiles", "myproj", "apply"])
    assert rc == 2
    assert "requires --set" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Phase 3e Step 5 -- deploy/mirror, dry-run by default, --live opts in.
# ---------------------------------------------------------------------------

def test_terminal_fragment_deploy_defaults_to_dry_run(monkeypatch, tmp_path, capsys):
    _make_home(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    rc = _run(monkeypatch, tmp_path, ["terminal-fragment", "myproj", "--deploy"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN: nothing was written" in out
    assert not (tmp_path / "localappdata" / "Microsoft" / "Windows Terminal").exists()


def test_terminal_fragment_deploy_live_writes_fragment(monkeypatch, tmp_path, capsys):
    _make_home(tmp_path)
    local = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    rc = _run(monkeypatch, tmp_path, ["terminal-fragment", "myproj", "--deploy", "--live"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "LIVE: writes applied." in out
    fragment_path = local / "Microsoft" / "Windows Terminal" / "Fragments" / "AgentWorktrees" / "agent-worktrees.json"
    assert fragment_path.exists()
    fragment = json.loads(fragment_path.read_text())
    assert any(p["name"] == "Myproj" for p in fragment["profiles"])


def test_profiles_apply_mirror_without_live_is_a_preview(monkeypatch, tmp_path, capsys):
    _make_home(tmp_path)
    local = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    rc = _run(monkeypatch, tmp_path, [
        "profiles", "myproj", "apply",
        "--set", json.dumps([{"machine": "other", "env": "Win", "kind": "shell"}]),
        "--mirror", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mirrored"] is False
    assert "mirror_plan" in payload
    assert not (local / "Microsoft" / "Windows Terminal").exists()


def test_profiles_apply_mirror_live_writes_fragment(monkeypatch, tmp_path, capsys):
    _make_home(tmp_path)
    local = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    rc = _run(monkeypatch, tmp_path, [
        "profiles", "myproj", "apply",
        "--set", json.dumps([{"machine": "other", "env": "Win", "kind": "shell"}]),
        "--mirror", "--live", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mirrored"] is True
    fragment_path = local / "Microsoft" / "Windows Terminal" / "Fragments" / "AgentWorktrees" / "agent-worktrees.json"
    assert fragment_path.exists()
