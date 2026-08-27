"""Phase 3 tests: the Manager harness-state read-model (projects/repos/plugins).

The model is exercised against a synthetic HOME so the joins + indicators are
asserted deterministically, plus a read-only smoke of the commands against the
real machine.
"""

from __future__ import annotations

import json
from pathlib import Path

from worktree_manager.harness_state import (
    build_projects,
    build_repos,
    build_state,
    pr_model,
    repo_plugin_enablement,
    user_enabled_plugins,
)
from worktree_manager.__main__ import main


def _make_home(tmp: Path) -> Path:
    # user-global Copilot settings
    copilot = tmp / ".copilot"
    copilot.mkdir(parents=True)
    (copilot / "settings.json").write_text(json.dumps({
        "enabledPlugins": {
            "agent-worktrees@copilot-extensions": True,
            "agent-bridge@copilot-extensions": True,
            "mail@dotfiles-plugins": True,
            "disabled-thing@x": False,
        },
        "extraKnownMarketplaces": {"copilot-extensions": {}},
    }))
    # repos + projects registries
    awt = tmp / ".agent-worktrees"
    awt.mkdir(parents=True)
    checkout = tmp / "src" / "dotfiles"
    (checkout / ".github" / "copilot").mkdir(parents=True)
    (checkout / ".github" / "copilot" / "settings.json").write_text(json.dumps({
        "enabledPlugins": {
            "mail@dotfiles-plugins": True,
            "teams@dotfiles-plugins": True,
            "disabled@dotfiles-plugins": False,
        },
    }))
    (checkout / ".agent-worktrees").mkdir()
    (checkout / ".agent-worktrees" / "config.yaml").write_text("pr:\n  enabled: true\n")
    win = str(checkout).replace("\\", "\\\\")
    (awt / "repos.yaml").write_text(
        "schema_version: 1\n"
        "account_map:\n  example-operator: example-operator\n"
        "repos:\n"
        "  dotfiles:\n"
        "    class: worktree\n"
        "    remote: \"https://github.com/example-operator/dotfiles.git\"\n"
        f"    windows: \"{win}\"\n"
        "    linux: \"" + str(checkout).replace("\\", "/") + "\"\n"
        "    tags: [control-plane]\n"
        "  some-lib:\n"
        "    class: singleton\n"
        "    agent: false\n"
        "    remote: \"https://example.com/some-lib.git\"\n"
    )
    (awt / "projects.yaml").write_text(
        "schema_version: 2\n"
        "projects:\n"
        "  dotfiles:\n"
        "    config_dir: \"~/.dotfiles\"\n"
        "    expose_agent: true\n"
    )
    # per-project harness config (knowledge_repo + profiles)
    proj_cfg = tmp / ".dotfiles"
    proj_cfg.mkdir()
    (proj_cfg / "config.yaml").write_text(
        "repo_name: dotfiles\n"
        "knowledge_repo: my-knowledge\n"
        "terminal_profiles:\n  - {machine: book2}\n  - {machine: dev6}\n"
    )
    return tmp


# ── read-model ──────────────────────────────────────────────────────────────

def test_user_enabled_parsing(tmp_path: Path):
    home = _make_home(tmp_path)
    enabled = user_enabled_plugins(home)
    by_name = {e.name: e for e in enabled}
    assert by_name["agent-worktrees"].marketplace == "copilot-extensions"
    assert by_name["agent-worktrees"].enabled is True
    assert by_name["disabled-thing"].enabled is False


def test_build_repos_indicators(tmp_path: Path):
    home = _make_home(tmp_path)
    repos = {r.name: r for r in build_repos(home)}
    df = repos["dotfiles"]
    assert df.klass == "worktree"
    assert df.agent is True            # default when unset
    assert df.is_project is True       # promoted (in projects.yaml)
    assert df.pr_model == "pr"         # from checkout .agent-worktrees/config.yaml
    assert df.path and Path(df.path).exists()
    lib = repos["some-lib"]
    assert lib.klass == "singleton"
    assert lib.agent is False
    assert lib.is_project is False
    # Projects sort first.
    assert build_repos(home)[0].name == "dotfiles"


def test_build_projects_joins_config_and_enablement(tmp_path: Path):
    home = _make_home(tmp_path)
    projects = build_projects(home)
    assert len(projects) == 1
    p = projects[0]
    assert p.name == "dotfiles"
    assert p.knowledge_repo == "my-knowledge"
    assert p.profiles == 2
    assert p.repo is not None and p.repo.klass == "worktree"
    assert set(e.split("@")[0] for e in p.enabled_plugins) == {"mail", "teams"}


def test_repo_plugin_enablement_uses_last_file_wins(tmp_path: Path):
    repo = tmp_path / "repo"
    claude = repo / ".claude"
    native = repo / ".github" / "copilot"
    claude.mkdir(parents=True)
    native.mkdir(parents=True)
    (claude / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"one@m": True, "two@m": True},
    }))
    (native / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"two@m": False},
    }))
    (native / "settings.local.json").write_text(json.dumps({
        "enabledPlugins": {"three@m": True},
    }))
    assert repo_plugin_enablement(str(repo)) == {
        "one@m": True,
        "two@m": False,
        "three@m": True,
    }


def test_build_state_shape(tmp_path: Path):
    home = _make_home(tmp_path)
    st = build_state(home)
    assert "agent-worktrees" in st.enabled_names()
    assert "disabled-thing" not in st.enabled_names()
    assert [r.name for r in st.repos]
    assert [p.name for p in st.projects] == ["dotfiles"]


def test_pr_model_variants(tmp_path: Path):
    root = tmp_path / "r"
    (root / ".agent-worktrees").mkdir(parents=True)
    (root / ".agent-worktrees" / "config.yaml").write_text("pr:\n  required: true\n")
    assert pr_model(str(root)) == "pr-required"
    (root / ".agent-worktrees" / "config.yaml").write_text("pr:\n  enabled: true\n")
    assert pr_model(str(root)) == "pr"
    (root / ".agent-worktrees" / "config.yaml").write_text("other: 1\n")
    assert pr_model(str(root)) == "direct"
    assert pr_model(None) == "?"


def test_missing_files_degrade_gracefully(tmp_path: Path):
    # An empty HOME: no registries, no settings — everything returns empty.
    assert build_repos(tmp_path) == []
    assert build_projects(tmp_path) == []
    st = build_state(tmp_path)
    assert st.user_enabled == () and st.repos == () and st.projects == ()


# ── command smoke (read-only, real machine) ─────────────────────────────────

def test_projects_command(capsys):
    assert main(["projects"]) == 0
    assert "Projects" in capsys.readouterr().out


def test_repos_command(capsys):
    assert main(["repos"]) == 0
    assert "Repos" in capsys.readouterr().out


def test_plugins_status_command(capsys):
    assert main(["plugins", "--status"]) == 0
    out = capsys.readouterr().out
    assert "enablement" in out.lower()
