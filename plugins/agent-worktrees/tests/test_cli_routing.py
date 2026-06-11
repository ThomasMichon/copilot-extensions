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


def test_project_flag_blanks_inherited_worktree_id(monkeypatch):
    """#25: --project blanks the caller's inherited WORKTREE_ID so worktree
    resolution falls back to CWD instead of the wrong (cross-project) id."""
    import os
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setenv("WORKTREE_ID", "caller-session-wt")
    monkeypatch.setenv("APERTURE_WORKTREE_ID", "caller-session-wt")
    monkeypatch.setattr(m, "cmd_launch", lambda argv: 0)

    rc = m.main(["--project", "demo", "status"])
    assert rc == 0
    assert os.environ.get("WORKTREE_PROJECT") == "demo"
    assert os.environ.get("WORKTREE_ID") is None
    assert os.environ.get("APERTURE_WORKTREE_ID") is None


def test_bare_invocation_preserves_inherited_worktree_id(monkeypatch):
    """#25: without --project, a bare invocation still inherits the session's
    WORKTREE_ID -- the intended 'operate on my current worktree' path."""
    import os
    monkeypatch.setenv("WORKTREE_PROJECT", "demo")
    monkeypatch.setenv("WORKTREE_ID", "keep-me")
    monkeypatch.setattr(m, "_is_headless_project", lambda: False)
    monkeypatch.setattr(m, "cmd_launch", lambda argv: 0)

    rc = m.main([])
    assert rc == 0
    assert os.environ.get("WORKTREE_ID") == "keep-me"


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


# ── worktree namespace ────────────────────────────────────────────────


def test_worktree_verb_maps_to_canonical(monkeypatch):
    captured = {}

    def fake_handler(args):
        captured["command"] = args.command
        return 0

    monkeypatch.setitem(m.COMMAND_MAP, "push-changes", fake_handler)
    rc = m.cmd_worktree_dispatch(["push", "wt-1"])
    assert rc == 0
    assert captured["command"] == "push-changes"


def test_worktree_create_dispatches(monkeypatch):
    captured = {}

    def fake_create(args):
        captured["command"] = args.command
        captured["json"] = args.json
        return 0

    monkeypatch.setitem(m.COMMAND_MAP, "create", fake_create)
    rc = m.cmd_worktree_dispatch(["create", "--json"])
    assert rc == 0
    assert captured["command"] == "create"
    assert captured["json"] is True


def test_worktree_unknown_verb(capsys):
    rc = m.cmd_worktree_dispatch(["bogus"])
    assert rc == 1
    captured = capsys.readouterr()
    # output.err writes to stdout; usage to stderr.
    assert "Unknown worktree subcommand" in captured.out
    assert "worktree <command>" in captured.err


def test_worktree_no_args_shows_usage(capsys):
    rc = m.cmd_worktree_dispatch([])
    assert rc == 1
    assert "worktree <command>" in capsys.readouterr().err


def test_worktree_help_returns_zero(capsys):
    rc = m.cmd_worktree_dispatch(["--help"])
    assert rc == 0
    assert "worktree <command>" in capsys.readouterr().err


# ── headless projects ─────────────────────────────────────────────────


def test_bare_headless_project_lists_not_launches(monkeypatch):
    monkeypatch.setenv("WORKTREE_PROJECT", "ext")
    monkeypatch.setattr(m, "_is_headless_project", lambda: True)
    launched = {"v": False}

    def fake_launch(argv):
        launched["v"] = True
        return 0

    dispatched = {"v": None}

    def fake_dispatch(argv):
        dispatched["v"] = argv
        return 0

    monkeypatch.setattr(m, "cmd_launch", fake_launch)
    monkeypatch.setattr(m, "cmd_worktree_dispatch", fake_dispatch)
    monkeypatch.setattr(m.cfg, "project_name", lambda: "ext")
    rc = m.main([])
    assert rc == 0
    assert launched["v"] is False
    assert dispatched["v"] == ["list"]


def test_bare_non_headless_project_launches(monkeypatch):
    monkeypatch.setenv("WORKTREE_PROJECT", "demo")
    monkeypatch.setattr(m, "_is_headless_project", lambda: False)
    launched = {"v": False}

    def fake_launch(argv):
        launched["v"] = True
        return 0

    monkeypatch.setattr(m, "cmd_launch", fake_launch)
    rc = m.main([])
    assert rc == 0
    assert launched["v"] is True
