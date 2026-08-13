"""Tests for CLI-mode routing: --project flag and unrouted help."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import __main__ as m


def test_extract_project_flag_space():
    rest, proj = m._extract_project_flag(["--project", "foo", "list"])
    assert proj == "foo"
    assert rest == ["list"]


def test_resolve_new_accepts_owner_ref():
    # resource-obligation-settlement 3c: `resolve --new --owner-ref` stamps the
    # bridge/child worktree's owner_ref so its finalize settles the owner's claim
    # (parity with `create --owner-ref`).
    args = m.build_parser().parse_args(
        ["resolve", "--json", "--new", "--owner-ref", "mach/proj/wt-caller"])
    assert args.owner_ref == "mach/proj/wt-caller"


def test_resolve_owner_ref_defaults_none():
    args = m.build_parser().parse_args(["resolve", "--json", "--new"])
    assert getattr(args, "owner_ref", "MISSING") is None


def test_get_pr_keys_registered():
    assert "pr-enabled" in m._GET_KEYS
    assert "pr-provider" in m._GET_KEYS


def test_get_lease_origin_key_registered():
    assert "lease-origin" in m._GET_KEYS


def test_resolve_lease_origin_returns_store_url(monkeypatch):
    from agent_worktrees import lease_config

    monkeypatch.setattr(
        lease_config, "load_lease_settings",
        lambda *a, **k: lease_config.LeaseSettings(origin="https://store/x.git"),
    )
    assert m._resolve_lease_origin() == "https://store/x.git"


def test_resolve_lease_origin_guards_failure(monkeypatch):
    from agent_worktrees import lease_config

    def _boom(*a, **k):
        raise lease_config.ConfigError("no origin")

    monkeypatch.setattr(lease_config, "load_lease_settings", _boom)
    assert m._resolve_lease_origin() == ""


def test_get_lease_origin_value(monkeypatch, capsys):
    import argparse

    from agent_worktrees import config as cfg

    cfg.set_active_project("ext")
    conf = cfg.Config(
        srcroot="/s", machine="m", platform="linux", repo_name="ext",
        repos={"ext": cfg.RepoConfig(anchor="/a", worktree_root="/w")},
    )
    monkeypatch.setattr("agent_worktrees.config.load_config", lambda *a, **k: conf)
    monkeypatch.setattr(m, "_resolve_lease_origin", lambda: "https://store/x.git")

    rc = m.cmd_get(argparse.Namespace(key="lease-origin"))
    assert rc == 0
    assert capsys.readouterr().out.strip() == "https://store/x.git"


def test_get_pr_keys_values(monkeypatch, capsys):
    import argparse

    from agent_worktrees import config as cfg

    # cmd_get resolves cfg.project_dir(), which requires an active project;
    # pin it in-process so the test does not depend on the ambient environment.
    cfg.set_active_project("ext")

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


def test_project_flag_bypasses_help(monkeypatch):
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
    assert m.cfg.active_project() == "demo"
    import os
    # identity is threaded in-process, never round-tripped through the env
    assert "WORKTREE_PROJECT" not in os.environ


# ── `<repo> <slug>` command-surface router ───────────────────────────────────


def test_core_slugs_no_subcommand_collision():
    """A leading core slug must be a plugin namespace, never a worktrees verb --
    otherwise the router would shadow (or be shadowed by) a real subcommand."""
    import argparse

    parser = m.build_parser()
    subs = [a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)]
    names = set(subs[0].choices) if subs else set()
    assert names, "expected registered subcommands"
    collisions = m._CORE_SLUGS & names
    assert not collisions, f"core slug(s) collide with subcommands: {collisions}"


def test_router_dispatches_bridge_project_pinned(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        m, "_route_to_sibling_plugin",
        lambda slug, project, rest: captured.update(
            slug=slug, project=project, rest=rest) or 0,
    )
    rc = m.main(["--project", "demo", "bridge", "send", "cloud1", "hi"])
    assert rc == 0
    assert captured == {"slug": "bridge", "project": "demo",
                        "rest": ["send", "cloud1", "hi"]}


def test_router_bridge_cwd_addressed_forwards_no_project(monkeypatch):
    # Bare `agent-worktrees bridge …` (cwd-addressed): no --project injected.
    captured = {}
    monkeypatch.setattr(
        m, "_route_to_sibling_plugin",
        lambda slug, project, rest: captured.update(
            slug=slug, project=project, rest=rest) or 0,
    )
    rc = m.main(["bridge", "sessions"])
    assert rc == 0
    assert captured == {"slug": "bridge", "project": None, "rest": ["sessions"]}


def test_router_worktrees_folds_back_to_launch(monkeypatch):
    """`worktrees` strips + continues (== the bare `<repo> <verb>` alias); it
    must never dispatch to a sibling plugin."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(
        m, "_route_to_sibling_plugin",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("worktrees must not route to a sibling")),
    )
    called = {}
    monkeypatch.setattr(m, "cmd_launch",
                        lambda argv: called.__setitem__("launched", True) or 0)
    # `--project demo worktrees` strips to a bare launch, exactly like
    # `--project demo` with no subcommand.
    rc = m.main(["--project", "demo", "worktrees"])
    assert rc == 0
    assert called.get("launched") is True


def test_router_dispatches_codespaces_project_pinned(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        m, "_route_to_sibling_plugin",
        lambda slug, project, rest: captured.update(
            slug=slug, project=project, rest=rest) or 0,
    )
    rc = m.main(["--project", "demo", "codespaces", "list"])
    assert rc == 0
    assert captured == {"slug": "codespaces", "project": "demo",
                        "rest": ["list"]}


def test_route_to_sibling_marks_routed_project(monkeypatch, tmp_path):
    # When --project is injected, the child env carries the routed marker so the
    # sibling can distinguish a router-injected --project from an explicit one
    # (#1080). Forwarded argv still carries --project for the plugin to consume.
    import subprocess as _sp

    stub = tmp_path / "agent-bridge.cmd"
    stub.write_text("", encoding="utf-8")
    monkeypatch.setattr(m, "_sibling_binstub", lambda slug: stub)
    seen = {}

    class _R:
        returncode = 0

    def _fake_run(cmd, *a, **kw):
        seen["cmd"] = cmd
        seen["env"] = kw.get("env")
        return _R()

    monkeypatch.setattr(_sp, "run", _fake_run)
    rc = m._route_to_sibling_plugin("bridge", "demo", ["agents"])
    assert rc == 0
    assert seen["env"].get("AGENT_WORKTREES_PROJECT_ROUTED") == "1"
    assert "--project" in seen["cmd"] and "demo" in seen["cmd"]


def test_route_to_sibling_no_project_clears_stale_marker(monkeypatch, tmp_path):
    # A cwd-addressed route (no --project) must NOT set the routed marker, AND
    # must clear a stale/exported one from the parent env so the child never
    # treats a user's explicit --project (in rest) as routed (#1080 review).
    import subprocess as _sp

    stub = tmp_path / "agent-bridge.cmd"
    stub.write_text("", encoding="utf-8")
    monkeypatch.setattr(m, "_sibling_binstub", lambda slug: stub)
    monkeypatch.setenv("AGENT_WORKTREES_PROJECT_ROUTED", "1")  # stale/exported
    seen = {}

    class _R:
        returncode = 0

    def _fake_run(cmd, *a, **kw):
        seen["env"] = kw.get("env")
        return _R()

    monkeypatch.setattr(_sp, "run", _fake_run)
    rc = m._route_to_sibling_plugin("bridge", None, ["--project", "x", "sessions"])
    assert rc == 0
    assert "AGENT_WORKTREES_PROJECT_ROUTED" not in (seen["env"] or {})


def test_canonical_slug_tolerates_pluralization():
    assert m._canonical_slug("bridge") == "bridge"
    assert m._canonical_slug("codespaces") == "codespaces"
    assert m._canonical_slug("codespace") == "codespaces"   # singular -> plural
    assert m._canonical_slug("worktree") == "worktrees"     # singular -> plural
    assert m._canonical_slug("worktrees") == "worktrees"
    assert m._canonical_slug("bogusplugin") is None


def test_router_singular_slug_alias_routes_canonical(monkeypatch):
    """`<repo> codespace …` (singular) routes to the canonical `codespaces`
    plugin, with --project preserved."""
    captured = {}
    monkeypatch.setattr(
        m, "_route_to_sibling_plugin",
        lambda slug, project, rest: captured.update(
            slug=slug, project=project, rest=rest) or 0,
    )
    rc = m.main(["--project", "demo", "codespace", "list"])
    assert rc == 0
    assert captured == {"slug": "codespaces", "project": "demo",
                        "rest": ["list"]}


def test_router_worktree_singular_folds_back(monkeypatch):
    """`<repo> worktree …` (singular) folds back into this binstub, same as
    `worktrees`."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(
        m, "_route_to_sibling_plugin",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("worktree(s) must not route to a sibling")),
    )
    called = {}
    monkeypatch.setattr(m, "cmd_launch",
                        lambda argv: called.__setitem__("launched", True) or 0)
    rc = m.main(["--project", "demo", "worktree"])
    assert rc == 0
    assert called.get("launched") is True


def test_router_non_project_slug_omits_project(monkeypatch):
    """A routable slug that does NOT consume --project (e.g. mcp) routes as a
    cwd-preserving alias -- the router never forwards --project to it, even when
    the caller passed one."""
    assert "mcp" in m._CORE_SLUGS
    assert "mcp" not in m._PROJECT_ARG_SLUGS
    captured = {}
    monkeypatch.setattr(
        m, "_route_to_sibling_plugin",
        lambda slug, project, rest: captured.update(
            slug=slug, project=project, rest=rest) or 0,
    )
    rc = m.main(["--project", "demo", "mcp", "list"])
    assert rc == 0
    assert captured == {"slug": "mcp", "project": None, "rest": ["list"]}


def test_router_derives_from_installed_binstubs(monkeypatch):
    """A non-core slug is routable when its agent-<slug> binstub is installed
    (the routable set is derived, not hardcoded)."""
    monkeypatch.setattr(m, "_installed_sibling_slugs", lambda: {"newplugin"})
    captured = {}
    monkeypatch.setattr(
        m, "_route_to_sibling_plugin",
        lambda slug, project, rest: captured.update(
            slug=slug, project=project, rest=rest) or 0,
    )
    rc = m.main(["newplugin", "do-thing", "--flag"])
    assert rc == 0
    assert captured == {"slug": "newplugin", "project": None,
                        "rest": ["do-thing", "--flag"]}


def test_router_worktrees_verb_not_routed(monkeypatch, capsys):
    """A real worktrees verb must never be routed to a sibling (collision
    guard), even if some sibling shares the name."""
    assert "list" in m._worktrees_verbs()
    monkeypatch.setattr(
        m, "_route_to_sibling_plugin",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("a worktrees verb must not route to a sibling")),
    )
    # Even if a bogus sibling 'list' binstub were present, the verb wins.
    monkeypatch.setattr(m, "_installed_sibling_slugs", lambda: {"list"})
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m.inst, "read_projects_registry", lambda: {"projects": {}})
    monkeypatch.setattr(m, "_git_toplevel", lambda p: None)
    rc = m.main(["list"])  # falls through to normal handling (balks: no project)
    assert rc == 1


def test_installed_sibling_slugs_parses_binstub_names(monkeypatch, tmp_path):
    for n in ["agent-bridge.ps1", "agent-codespaces.cmd", "agent-logger",
              "agent-worktrees.ps1", "dotfiles.ps1", "not-agent.ps1"]:
        (tmp_path / n).write_text("x")
    monkeypatch.setattr(m.inst, "local_bin", lambda: tmp_path)
    slugs = m._installed_sibling_slugs()
    assert "bridge" in slugs and "codespaces" in slugs and "logger" in slugs
    assert "worktrees" not in slugs   # folds back, excluded
    assert "dotfiles" not in slugs    # not an agent-* binstub



def test_route_to_sibling_missing_binstub(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(m.inst, "local_bin", lambda: tmp_path)
    rc = m._route_to_sibling_plugin("bridge", "demo", ["sessions"])
    assert rc == 1
    assert "not installed" in capsys.readouterr().err


def _write_stub(tmp_path):
    import platform
    name = ("agent-bridge.ps1" if platform.system() == "Windows"
            else "agent-bridge")
    (tmp_path / name).write_text("stub")
    return name


def test_route_to_sibling_forwards_project(monkeypatch, tmp_path):
    name = _write_stub(tmp_path)
    monkeypatch.setattr(m.inst, "local_bin", lambda: tmp_path)
    captured = {}

    class _R:
        returncode = 0

    monkeypatch.setattr(m.subprocess, "run",
                        lambda cmd, *a, **k: captured.update(cmd=cmd) or _R())
    rc = m._route_to_sibling_plugin("bridge", "demo", ["send", "cloud1", "hi"])
    assert rc == 0
    cmd = [str(c) for c in captured["cmd"]]
    assert "--project" in cmd and "demo" in cmd
    assert cmd[-3:] == ["send", "cloud1", "hi"]
    assert any(name in c for c in cmd)


def test_route_to_sibling_no_project_omits_flag(monkeypatch, tmp_path):
    _write_stub(tmp_path)
    monkeypatch.setattr(m.inst, "local_bin", lambda: tmp_path)
    captured = {}

    class _R:
        returncode = 0

    monkeypatch.setattr(m.subprocess, "run",
                        lambda cmd, *a, **k: captured.update(cmd=cmd) or _R())
    rc = m._route_to_sibling_plugin("bridge", None, ["sessions"])
    assert rc == 0
    assert "--project" not in [str(c) for c in captured["cmd"]]


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


def test_picker_mock_launches_in_mock_mode(monkeypatch):
    """`picker mock` launches the TUI in explicit mock mode (mock_mode=True) and
    reports the decision without acting on it."""
    import argparse

    from agent_worktrees import picker_tui

    seen = {}

    def _fake_run(live=False, mock_mode=None):
        seen["live"] = live
        seen["mock_mode"] = mock_mode
        return {"action": "cancel"}

    monkeypatch.setattr(picker_tui, "run_tui_picker", _fake_run)
    monkeypatch.setattr(m, "_in_ssh_session", lambda: False)

    rc = m.cmd_picker(argparse.Namespace(picker_action="mock", json=True))
    assert rc == 0
    assert seen["mock_mode"] is True


def test_new_picker_enabled_precedence(monkeypatch):
    import types

    from agent_worktrees import picker_tui

    monkeypatch.delenv("AGENT_WORKTREES_NEW_PICKER", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_LEGACY_PICKER", raising=False)
    # Default is on: opt-out (new_picker=False) -> legacy; unset/None -> on.
    assert picker_tui.new_picker_enabled(types.SimpleNamespace(new_picker=True))
    assert not picker_tui.new_picker_enabled(types.SimpleNamespace(new_picker=False))
    assert picker_tui.new_picker_enabled(None)          # default everywhere
    # A machine opted out still gets the new picker for one invocation via env.
    monkeypatch.setenv("AGENT_WORKTREES_NEW_PICKER", "1")
    assert picker_tui.new_picker_enabled(types.SimpleNamespace(new_picker=False))
    monkeypatch.delenv("AGENT_WORKTREES_NEW_PICKER", raising=False)
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
    # $WORKTREE_PROJECT is no longer exported -- the Python resolver reads
    # cfg.active_project(), and identity never round-trips through the env
    # (cwd-resolution Phase 3).
    assert "WORKTREE_PROJECT" not in os.environ
    # WORKTREE_ID is no longer scrubbed -- present but irrelevant to CWD-based
    # resolution.
    assert os.environ.get("WORKTREE_ID") == "caller-session-wt"
    assert os.environ.get("APERTURE_WORKTREE_ID") == "caller-session-wt"


def test_bare_invocation_ignores_inherited_worktree_id(monkeypatch):
    """Without --project, a bare launch resolves context from CWD; the inherited
    WORKTREE_ID is neither consulted nor deleted (it is simply irrelevant)."""
    import os
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setenv("WORKTREE_ID", "keep-me")
    # Context comes from CWD resolution (not the retired $WORKTREE_PROJECT).
    monkeypatch.setattr(m, "_resolve_active_project", lambda proj: ("demo", None))
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


def test_reap_sessions_is_project_scoped_not_no_project():
    """reap-sessions correlates machine-wide mux sessions against a project's
    tracking records, so it must resolve a project (like cleanup/gc) rather than
    run context-free -- otherwise the bare binstub crashes in project_name()
    (copilot-extensions #102)."""
    assert "reap-sessions" not in m._NO_PROJECT_COMMANDS


def test_bare_reap_sessions_without_project_balks_not_crashes(monkeypatch, capsys):
    """`agent-worktrees reap-sessions` from a non-repo dir balks helpfully
    instead of raising RuntimeError deep in project_name() (#102)."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_git_toplevel", lambda p: None)

    def _boom(args):
        raise AssertionError("cmd_reap_sessions must not run without a project")

    monkeypatch.setitem(m.COMMAND_MAP, "reap-sessions", _boom)
    rc = m.main(["reap-sessions"])
    assert rc == 1
    assert "Could not resolve a project" in capsys.readouterr().err


def test_reap_sessions_resolves_project_from_flag(monkeypatch):
    """A project binstub injects ``--project <name>``; reap-sessions then
    resolves it and runs (the aperture-labs reap-sessions path)."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_anchor_for_project", lambda name: None)
    seen = {}

    def _ran(args):
        seen["project"] = m.cfg.active_project()
        return 0

    monkeypatch.setitem(m.COMMAND_MAP, "reap-sessions", _ran)
    rc = m.main(["--project", "demo", "reap-sessions"])
    assert rc == 0
    assert seen["project"] == "demo"


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
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_resolve_active_project", lambda proj: ("ext", None))
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
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_resolve_active_project", lambda proj: ("demo", None))
    monkeypatch.setattr(m, "_is_headless_project", lambda: False)
    launched = {"v": False}

    def fake_launch(argv):
        launched["v"] = True
        return 0

    monkeypatch.setattr(m, "cmd_launch", fake_launch)
    rc = m.main([])
    assert rc == 0
    assert launched["v"] is True


# ── the binstub seam (Phase 6 / DQ7 / DQ8) ────────────────────────────


def test_bare_prefers_worktree_manager_when_on_path(monkeypatch):
    """Bare, non-headless invocation execs the Manager when it is on PATH,
    threading the active project, and does NOT load the bundled Picker."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_resolve_active_project", lambda proj: ("demo", None))
    monkeypatch.setattr(m, "_is_headless_project", lambda: False)
    monkeypatch.setattr(m.cfg, "active_project", lambda: "demo")
    monkeypatch.setattr(m, "_usable_worktree_manager", lambda: "/usr/bin/worktree-manager")

    launched = {"v": False}
    monkeypatch.setattr(m, "cmd_launch", lambda argv: launched.__setitem__("v", True) or 0)

    seam = {"mgr": None, "project": "unset"}

    def fake_exec(mgr, project):
        seam["mgr"] = mgr
        seam["project"] = project
        return 0

    monkeypatch.setattr(m, "_exec_worktree_manager", fake_exec)
    rc = m.main([])
    assert rc == 0
    assert seam == {"mgr": "/usr/bin/worktree-manager", "project": "demo"}
    assert launched["v"] is False


def test_bare_falls_back_to_picker_without_manager(monkeypatch):
    """No Manager on PATH → the bundled Picker is the fallback while it ships."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_resolve_active_project", lambda proj: ("demo", None))
    monkeypatch.setattr(m, "_is_headless_project", lambda: False)
    monkeypatch.setattr(m, "_worktree_manager_path", lambda: None)
    monkeypatch.setattr(m, "_bundled_picker_available", lambda: True)

    launched = {"v": False}
    monkeypatch.setattr(m, "cmd_launch", lambda argv: launched.__setitem__("v", True) or 0)
    monkeypatch.setattr(m, "_exec_worktree_manager",
                        lambda mgr, project: pytest.fail("should not exec manager"))
    monkeypatch.setattr(m, "cmd_manager_install_trigger",
                        lambda project: pytest.fail("picker present: no install trigger"))
    rc = m.main([])
    assert rc == 0
    assert launched["v"] is True


def test_bare_shows_install_trigger_when_picker_retired(monkeypatch):
    """No Manager AND the bundled Picker retired (6c) → the install trigger,
    threaded with the active project, and NOT the Picker."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_resolve_active_project", lambda proj: ("demo", None))
    monkeypatch.setattr(m, "_is_headless_project", lambda: False)
    monkeypatch.setattr(m.cfg, "active_project", lambda: "demo")
    monkeypatch.setattr(m, "_worktree_manager_path", lambda: None)
    monkeypatch.setattr(m, "_bundled_picker_available", lambda: False)
    monkeypatch.setattr(m, "cmd_launch",
                        lambda argv: pytest.fail("picker retired: must not launch"))

    trig = {"project": "unset"}
    monkeypatch.setattr(m, "cmd_manager_install_trigger",
                        lambda project: trig.__setitem__("project", project) or 0)
    rc = m.main([])
    assert rc == 0
    assert trig["project"] == "demo"


def test_bare_no_project_prefers_manager_without_project_flag(monkeypatch):
    """With no resolvable project, a bare invocation still prefers the Manager
    (its multi-project front door) and passes no --project."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_resolve_active_project", lambda proj: (None, None))
    monkeypatch.setattr(m.cfg, "active_project", lambda: None)
    monkeypatch.setattr(m, "_usable_worktree_manager", lambda: "/usr/bin/worktree-manager")

    seam = {"mgr": None, "project": "unset"}
    monkeypatch.setattr(
        m, "_exec_worktree_manager",
        lambda mgr, project: seam.update(mgr=mgr, project=project) or 0)
    monkeypatch.setattr(m, "cmd_help_unrouted",
                        lambda **k: pytest.fail("should not balk when manager present"))
    rc = m.main([])
    assert rc == 0
    assert seam == {"mgr": "/usr/bin/worktree-manager", "project": None}


def test_bare_no_project_install_trigger_when_picker_retired(monkeypatch):
    """No project, no Manager, Picker retired → the install trigger (not the
    project-resolution balk)."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_resolve_active_project", lambda proj: (None, None))
    monkeypatch.setattr(m.cfg, "active_project", lambda: None)
    monkeypatch.setattr(m, "_worktree_manager_path", lambda: None)
    monkeypatch.setattr(m, "_bundled_picker_available", lambda: False)
    monkeypatch.setattr(m, "cmd_help_unrouted",
                        lambda **k: pytest.fail("picker retired: show install trigger"))
    trig = {"project": "unset"}
    monkeypatch.setattr(m, "cmd_manager_install_trigger",
                        lambda project: trig.__setitem__("project", project) or 0)
    rc = m.main([])
    assert rc == 0
    assert trig["project"] is None


def test_install_trigger_shows_source_and_platform_command(monkeypatch, capsys):
    """The install trigger prints the verifiable source URL and the correct
    per-platform install command, and never auto-runs anything."""
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    rc = m.cmd_manager_install_trigger("demo")
    err = capsys.readouterr().err
    assert rc == 0
    assert m._WORKTREE_MANAGER_REPO_URL in err
    assert "bootstrap.sh" in err and "curl -fsSL" in err
    assert "bootstrap.ps1" not in err  # posix must not show the Windows one-liner

    monkeypatch.setattr(m.platform, "system", lambda: "Windows")
    rc = m.cmd_manager_install_trigger("demo")
    err = capsys.readouterr().err
    assert rc == 0
    assert "bootstrap.ps1" in err and "irm " in err
    assert "bootstrap.sh" not in err


def test_bundled_picker_available_detects_package():
    """While picker_tui ships, the fallback resolves to the bundled Picker."""
    assert m._bundled_picker_available() is True


# ── _usable_worktree_manager health gate (DQ8: never dead-end bare launch) ────

def _fake_run(returncode):
    import subprocess as _sp

    def run(cmd, **kw):
        return _sp.CompletedProcess(cmd, returncode, stdout="worktree-manager 0.1.0\n", stderr="")
    return run


def test_usable_manager_returns_path_when_healthy(monkeypatch):
    """A Manager that answers `--version` with exit 0 is preferred."""
    monkeypatch.setattr(m, "_worktree_manager_path", lambda: "/usr/bin/worktree-manager")
    monkeypatch.setattr(m.subprocess, "run", _fake_run(0))
    assert m._usable_worktree_manager() == "/usr/bin/worktree-manager"


def test_usable_manager_none_when_absent(monkeypatch):
    """No binstub on PATH → None, without probing."""
    monkeypatch.setattr(m, "_worktree_manager_path", lambda: None)
    monkeypatch.setattr(m.subprocess, "run",
                        lambda *a, **k: pytest.fail("must not probe an absent manager"))
    assert m._usable_worktree_manager() is None


def test_usable_manager_rejects_broken_binstub(monkeypatch):
    """A stale/broken binstub (non-zero `--version`) is treated as absent so the
    seam can fall back -- the exact book2 failure (a pre-versioned stub that
    demands WORKTREE_PROJECT and errors on every call)."""
    monkeypatch.setattr(m, "_worktree_manager_path", lambda: "/usr/bin/worktree-manager")
    monkeypatch.setattr(m.subprocess, "run", _fake_run(1))
    assert m._usable_worktree_manager() is None


def test_usable_manager_rejects_unrunnable_binstub(monkeypatch):
    """A binstub that cannot even be spawned is treated as absent, not a crash."""
    monkeypatch.setattr(m, "_worktree_manager_path", lambda: "/usr/bin/worktree-manager")

    def boom(cmd, **kw):
        raise OSError("cannot exec")

    monkeypatch.setattr(m.subprocess, "run", boom)
    assert m._usable_worktree_manager() is None


def test_bare_falls_back_to_picker_when_manager_broken(monkeypatch):
    """End-to-end: a broken Manager on PATH must NOT dead-end bare launch --
    the seam falls back to the bundled Picker (DQ8 invariant)."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_resolve_active_project", lambda proj: ("demo", None))
    monkeypatch.setattr(m, "_is_headless_project", lambda: False)
    monkeypatch.setattr(m.cfg, "active_project", lambda: "demo")
    monkeypatch.setattr(m, "_worktree_manager_path", lambda: "/usr/bin/worktree-manager")
    monkeypatch.setattr(m.subprocess, "run", _fake_run(1))  # broken stub
    monkeypatch.setattr(m, "_bundled_picker_available", lambda: True)
    monkeypatch.setattr(m, "_exec_worktree_manager",
                        lambda mgr, project: pytest.fail("broken manager must not be exec'd"))
    launched = {"v": False}
    monkeypatch.setattr(m, "cmd_launch", lambda argv: launched.__setitem__("v", True) or 0)
    rc = m.main([])
    assert rc == 0
    assert launched["v"] is True


def test_bare_headless_ignores_manager(monkeypatch):
    """Headless projects are never interactive: the seam does not apply even
    when the Manager is on PATH."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_resolve_active_project", lambda proj: ("ext", None))
    monkeypatch.setattr(m, "_is_headless_project", lambda: True)
    monkeypatch.setattr(m, "_worktree_manager_path", lambda: "/usr/bin/worktree-manager")
    monkeypatch.setattr(m, "_exec_worktree_manager",
                        lambda mgr, project: pytest.fail("headless must not exec manager"))
    monkeypatch.setattr(m, "cmd_worktree_dispatch", lambda argv: 0)
    monkeypatch.setattr(m.cfg, "project_name", lambda: "ext")
    rc = m.main([])
    assert rc == 0


# ---------------------------------------------------------------------------
# pr-* family aliases / namespace (Phase 4)
# ---------------------------------------------------------------------------

def test_pr_create_alias_in_command_map():
    # pr-create resolves to the same handler as create-pr.
    assert m.COMMAND_MAP["pr-create"] is m.COMMAND_MAP["create-pr"]


def test_pr_create_parser_alias():
    # The parser accepts `pr-create` (argparse alias of create-pr).
    args = m.build_parser().parse_args(["pr-create", "--title", "x"])
    assert args.command in ("pr-create", "create-pr")


def test_pr_status_no_live_flag():
    args = m.build_parser().parse_args(["pr-status", "--no-live"])
    assert args.no_live is True

    args2 = m.build_parser().parse_args(["pr-status"])
    assert args2.no_live is False


def test_pr_namespace_routes_create_to_create_pr():
    assert m._PR_NAMESPACE["create"] == "create-pr"


def test_run_verb_does_not_shadow_subcommand_dest():
    # Regression: the `run` verb's REMAINDER positional must NOT reuse the
    # dest `command` -- that is the subparsers dest holding the subcommand name.
    # A collision made `args.command` a list and crashed dispatch.
    args = m.build_parser().parse_args(["run", "copilot-extensions", "create", "--json"])
    assert args.command == "run"
    assert args.inner_command == ["copilot-extensions", "create", "--json"]


def test_run_verb_single_string_form():
    args = m.build_parser().parse_args(["run", "copilot-extensions create --json"])
    assert args.command == "run"
    assert args.inner_command == ["copilot-extensions create --json"]


def test_run_registered_in_command_map():
    assert m.COMMAND_MAP["run"] is m.cmd_run
    assert m._WORKTREE_VERBS.get("run") == "run"


def test_claimant_liveness_parser_and_registration():
    args = m.build_parser().parse_args(
        ["claimant-liveness", "lambda-core/aperture-labs/wt-A#s1", "--json"])
    assert args.command == "claimant-liveness"
    assert args.owner_ref == "lambda-core/aperture-labs/wt-A#s1"
    assert args.json is True
    assert m.COMMAND_MAP["claimant-liveness"] is m.cmd_claimant_liveness
    assert m._WORKTREE_VERBS.get("claimant-liveness") == "claimant-liveness"


def test_claimant_liveness_json_output(monkeypatch, capfd):
    import argparse

    monkeypatch.setattr(m.claimant_mod, "local_claimant_alive",
                        lambda ref: False)
    rc = m.cmd_claimant_liveness(argparse.Namespace(
        owner_ref="borealis/aperture-labs/wt-A", json=True))
    assert rc == 0
    import json as _json
    out = _json.loads(capfd.readouterr().out)
    assert out["alive"] is False
    assert out["owner_ref"] == "borealis/aperture-labs/wt-A"


def test_pr_research_dispatch_json(monkeypatch, capsys):
    # #225: pr-research reads live provider settings and prints the derived
    # policy matrix (read-only), via the config + provider seams.
    import json as _json

    from agent_worktrees import config as cfg
    from agent_worktrees import pr_contract as pc
    from agent_worktrees import providers as prov

    conf = cfg.Config(
        srcroot="/s", machine="m", platform="linux", repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor="/a", worktree_root="/w", default_branch="main",
            pr=cfg.PRConfig(enabled=True, provider="github"),
        )},
    )
    monkeypatch.setattr("agent_worktrees.config.load_config", lambda *a, **k: conf)

    class _P:
        def get_repo_policy(self, repo, *, default_branch="", api_base="", token=None):
            return pc.RepoPolicy(supported=True, allow_squash=True,
                                 allow_auto_merge=True,
                                 required_approving_reviews=1)

    monkeypatch.setattr(prov, "get_provider", lambda name: _P())
    monkeypatch.setattr(prov, "account_token_for_slug", lambda slug, prcfg: "tok")

    rc = m.cmd_pr_research_dispatch(["ThomasMichon/copilot-extensions", "--json"])
    assert rc == 0
    out = _json.loads(capsys.readouterr().out.strip())
    assert out["supported"] is True
    assert out["suggested_matrix"]["merge_strategy"] == "squash"
    assert out["suggested_matrix"]["prefer_auto_merge"] is True
    assert out["suggested_matrix"]["review_blocking"] is True


def test_pr_research_dispatch_unsupported_provider(monkeypatch, capsys):
    import json as _json

    from agent_worktrees import config as cfg
    from agent_worktrees import pr_contract as pc
    from agent_worktrees import providers as prov

    conf = cfg.Config(
        srcroot="/s", machine="m", platform="linux", repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor="/a", worktree_root="/w", default_branch="main",
            pr=cfg.PRConfig(enabled=True, provider="gitea"),
        )},
    )
    monkeypatch.setattr("agent_worktrees.config.load_config", lambda *a, **k: conf)

    class _P:
        def get_repo_policy(self, repo, *, default_branch="", api_base="", token=None):
            return pc.RepoPolicy(supported=False, error="unsupported here")

    monkeypatch.setattr(prov, "get_provider", lambda name: _P())
    monkeypatch.setattr(prov, "account_token_for_slug", lambda slug, prcfg: None)

    rc = m.cmd_pr_research_dispatch(["o/r", "--json"])
    assert rc == 1
    out = _json.loads(capsys.readouterr().out.strip())
    assert out["supported"] is False
    assert out["suggested_matrix"] == {}
