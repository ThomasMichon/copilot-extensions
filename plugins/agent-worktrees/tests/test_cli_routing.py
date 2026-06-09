"""Tests for CLI-mode routing: --project flag and unrouted help."""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import __main__ as m


def test_extract_project_flag_space():
    rest, proj = m._extract_project_flag(["--project", "foo", "list"])
    assert proj == "foo"
    assert rest == ["list"]


def test_extract_project_flag_equals():
    rest, proj = m._extract_project_flag(["--project=bar", "worktree", "create"])
    assert proj == "bar"
    assert rest == ["worktree", "create"]


def test_extract_project_flag_short():
    rest, proj = m._extract_project_flag(["-p", "baz", "status", "wt-1"])
    assert proj == "baz"
    assert rest == ["status", "wt-1"]


def test_extract_project_flag_absent():
    rest, proj = m._extract_project_flag(["list", "--json"])
    assert proj is None
    assert rest == ["list", "--json"]


def test_extract_project_flag_only_first_consumed():
    rest, proj = m._extract_project_flag(["--project", "a", "--project", "b"])
    assert proj == "a"
    assert rest == ["--project", "b"]


def test_extract_project_flag_trailing_value_missing():
    rest, proj = m._extract_project_flag(["--project"])
    assert proj is None
    assert rest == []


def test_bare_no_project_routes_to_help(monkeypatch, capsys):
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m.inst, "read_projects_registry", lambda: {"projects": {}})
    monkeypatch.setattr(m, "_git_toplevel", lambda p: None)
    rc = m.main([])
    assert rc == 1
    err = capsys.readouterr().err
    assert "No project context" in err
    assert "register" in err


def test_project_requiring_command_no_project_routes_to_help(monkeypatch, capsys):
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m.inst, "read_projects_registry", lambda: {"projects": {}})
    monkeypatch.setattr(m, "_git_toplevel", lambda p: None)
    rc = m.main(["list"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "No project context for 'list'" in err


def test_project_flag_sets_env_and_bypasses_help(monkeypatch):
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    called = {}

    def fake_launch(argv):
        called["launched"] = True
        return 0

    # With a project set via flag and no subcommand, should launch, not help.
    monkeypatch.setattr(m, "cmd_launch", fake_launch)
    rc = m.main(["--project", "demo"])
    assert rc == 0
    assert called.get("launched") is True
    import os
    assert os.environ.get("WORKTREE_PROJECT") == "demo"


def test_version_works_without_project(monkeypatch, capsys):
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    rc = m.main(["--version"])
    assert rc == 0
    assert "agent-worktrees" in capsys.readouterr().out


def test_help_unrouted_inside_adopted_project(monkeypatch, capsys, tmp_path: Path):
    anchor = tmp_path / "myproj"
    anchor.mkdir()
    monkeypatch.setattr(
        m.inst, "read_projects_registry",
        lambda: {"projects": {"myproj": {"anchor": str(anchor)}}},
    )
    monkeypatch.setattr(m, "_git_toplevel", lambda p: anchor)
    rc = m.cmd_help_unrouted()
    assert rc == 1
    err = capsys.readouterr().err
    assert "inside the 'myproj' project" in err


def test_help_unrouted_unadopted_git_repo(monkeypatch, capsys, tmp_path: Path):
    repo = tmp_path / "orphan"
    repo.mkdir()
    monkeypatch.setattr(m.inst, "read_projects_registry", lambda: {"projects": {}})
    monkeypatch.setattr(m, "_git_toplevel", lambda p: repo)
    rc = m.cmd_help_unrouted()
    assert rc == 1
    err = capsys.readouterr().err
    assert "not adopted yet" in err
    assert "register orphan" in err
