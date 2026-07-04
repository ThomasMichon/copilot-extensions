"""Tests for CLI-mode routing: --project flag and unrouted help."""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import __main__ as m


def test_extract_project_flag_space():
    rest, proj = m._extract_project_flag(["--project", "foo", "list"])
    assert proj == "foo"
    assert rest == ["list"]


def test_get_pr_keys_registered():
    assert "pr-enabled" in m._GET_KEYS
    assert "pr-provider" in m._GET_KEYS


def test_get_pr_keys_values(monkeypatch, capsys):
    import argparse

    from agent_worktrees import config as cfg

    # cmd_get resolves cfg.project_dir(), which requires WORKTREE_PROJECT;
    # pin it so the test does not depend on the ambient environment.
    monkeypatch.setenv("WORKTREE_PROJECT", "ext")

    conf = cfg.Config(
        srcroot="/s", machine="m", platform="linux", repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor="/a", worktree_root="/w",
            pr=cfg.PRConfig(enabled=True, provider="gitea"),
        )},
    )
    monkeypatch.setattr("agent_worktrees.config.load_config", lambda *a, **k: conf)

    rc = m.cmd_get(argparse.Namespace(key="pr-enabled"))
    assert rc == 0
    assert capsys.readouterr().out.strip() == "true"

    rc = m.cmd_get(argparse.Namespace(key="pr-provider"))
    assert rc == 0
    assert capsys.readouterr().out.strip() == "gitea"


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
    assert "Could not resolve a project" in err
    assert "register" in err


def test_project_requiring_command_no_project_routes_to_help(monkeypatch, capsys):
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m.inst, "read_projects_registry", lambda: {"projects": {}})
    monkeypatch.setattr(m, "_git_toplevel", lambda p: None)
    rc = m.main(["list"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Could not resolve a project for 'list'" in err


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


def test_profiles_get_emits_self_diagonal(monkeypatch, capfd, tmp_path):
    """`profiles get --json` emits this host's column incl. the locked self."""
    import argparse

    from agent_worktrees import config as cfg

    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(cfg, "default_config_path", lambda: cfg_path)
    monkeypatch.setattr(m, "_profiles_host", lambda: ("Lambda-Core", "Win"))

    rc = m.cmd_profiles(argparse.Namespace(profiles_action="get", json=True))
    assert rc == 0
    out = capfd.readouterr().out
    assert '"machine": "Lambda-Core"' in out
    assert '"kind": "agent"' in out


def test_profiles_apply_writes_and_normalizes(monkeypatch, capfd, tmp_path):
    """`profiles apply --set` persists the column with self forced in."""
    import argparse
    import json as _json

    from agent_worktrees import config as cfg
    from agent_worktrees import profiles as profiles_mod

    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(cfg, "default_config_path", lambda: cfg_path)
    monkeypatch.setattr(m, "_profiles_host", lambda: ("Lambda-Core", "Win"))

    rc = m.cmd_profiles(argparse.Namespace(
        profiles_action="apply", json=True, no_mirror=True,
        set=_json.dumps([{"machine": "Borealis", "env": "Win", "kind": "shell"}]),
    ))
    assert rc == 0
    capfd.readouterr()
    loaded = profiles_mod.load_selection(cfg_path)
    assert profiles_mod.TargetSel("Lambda-Core", "Win", "agent") in loaded
    assert profiles_mod.TargetSel("Borealis", "Win", "shell") in loaded


def test_profiles_apply_rejects_bad_json(monkeypatch, tmp_path):
    import argparse

    from agent_worktrees import config as cfg

    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(cfg, "default_config_path", lambda: cfg_path)
    monkeypatch.setattr(m, "_profiles_host", lambda: ("Lambda-Core", "Win"))

    rc = m.cmd_profiles(argparse.Namespace(
        profiles_action="apply", json=True, no_mirror=True, set="{not json"))
    assert rc == 2


def test_picker_enable_disable_persists(monkeypatch, tmp_path):
    """`picker enable/disable` writes new_picker into the global config and
    preserves other keys."""
    import argparse

    import yaml

    from agent_worktrees import config as cfg

    gpath = tmp_path / "global.yaml"
    gpath.write_text("machine: lambda-core\nplatform: windows\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "global_config_path", lambda: gpath)

    assert m.cmd_picker(argparse.Namespace(picker_action="enable", json=False)) == 0
    data = yaml.safe_load(gpath.read_text(encoding="utf-8"))
    assert data["new_picker"] is True
    assert data["machine"] == "lambda-core"   # other keys preserved

    assert m.cmd_picker(argparse.Namespace(picker_action="disable", json=False)) == 0
    assert yaml.safe_load(gpath.read_text(encoding="utf-8"))["new_picker"] is False


def test_new_picker_enabled_precedence(monkeypatch):
    import types

    from agent_worktrees import picker_tui

    monkeypatch.delenv("AGENT_WORKTREES_NEW_PICKER", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_LEGACY_PICKER", raising=False)
    assert picker_tui.new_picker_enabled(types.SimpleNamespace(new_picker=True))
    assert not picker_tui.new_picker_enabled(types.SimpleNamespace(new_picker=False))
    assert not picker_tui.new_picker_enabled(None)
    # Legacy env always wins (rollback switch).
    monkeypatch.setenv("AGENT_WORKTREES_LEGACY_PICKER", "1")
    assert not picker_tui.new_picker_enabled(types.SimpleNamespace(new_picker=True))


def test_project_flag_sets_active_project_and_ignores_worktree_id(monkeypatch):
    """--project selects the project (assume CWD = its anchor). The inherited
    WORKTREE_ID is now simply IGNORED -- identity comes from CWD -- and is no
    longer scrubbed from the environment."""
    import os
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setenv("WORKTREE_ID", "caller-session-wt")
    monkeypatch.setenv("APERTURE_WORKTREE_ID", "caller-session-wt")
    monkeypatch.setitem(m.COMMAND_MAP, "status", lambda args: 0)

    rc = m.main(["--project", "demo", "status"])
    assert rc == 0
    assert m.cfg.active_project() == "demo"
    # Exported for legacy shell consumers; the Python resolver reads
    # cfg.active_project(), not this env var.
    assert os.environ.get("WORKTREE_PROJECT") == "demo"
    # No longer scrubbed -- present but irrelevant to CWD-based resolution.
    assert os.environ.get("WORKTREE_ID") == "caller-session-wt"
    assert os.environ.get("APERTURE_WORKTREE_ID") == "caller-session-wt"


def test_bare_invocation_ignores_inherited_worktree_id(monkeypatch):
    """Without --project, a bare launch resolves context from CWD; the inherited
    WORKTREE_ID is neither consulted nor deleted (it is simply irrelevant)."""
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


# ── repos namespace ───────────────────────────────────────────────────


def test_repos_subcommand_help_does_not_consume_value(monkeypatch, capsys):
    """`repos clone --help` must show usage, not clone a repo named '--help'."""
    from agent_worktrees import repos

    def _boom(*args, **kwargs):
        raise AssertionError("clone_repo must not run for `repos clone --help`")

    monkeypatch.setattr(repos, "clone_repo", _boom)
    rc = m.cmd_repos_dispatch(["clone", "--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "clone <remote>" in out


def test_repos_short_help_flag_shows_usage(monkeypatch, capsys):
    from agent_worktrees import repos

    monkeypatch.setattr(
        repos, "add_repo",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("add_repo must not run")),
    )
    rc = m.cmd_repos_dispatch(["add", "-h"])
    assert rc == 0
    assert "Repo classes:" in capsys.readouterr().out


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
