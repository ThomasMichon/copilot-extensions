"""Lean projects.yaml registry (schema v2) -- single Python owner of the write.

projects.yaml is the adoption/launch registry; it defers to ``repos.yaml`` (the
single owning store) for identity/location facts (anchor, machines_yaml,
default_branch), which every consumer resolves from the repo registry by the
project *name*. These tests cover the v1->v2 migrator, the lean write, its
preserve-existing semantics, and the ``register-project-entry`` subcommand.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from agent_worktrees import __main__ as m
from agent_worktrees import config_migrations, installer

# ---------------------------------------------------------------------------
# v1 -> v2 migrator
# ---------------------------------------------------------------------------

def test_migrator_strips_registry_owned_fields():
    doc = {
        "schema_version": 1,
        "projects": {
            "dotfiles": {
                "anchor": "D:/Src/dotfiles",
                "machines_yaml": "D:/Src/dotfiles/machines.yaml",
                "default_branch": "main",
                "config_dir": "~/.dotfiles",
                "expose_agent": True,
                "base_repo": True,
                "display_name": "Dotfiles",
                "wsl": {"state": "bootstrap"},
            }
        },
    }
    out = config_migrations._projects_v1_to_v2(doc)
    entry = out["projects"]["dotfiles"]
    # Registry-owned identity/location fields are gone.
    assert "anchor" not in entry
    assert "machines_yaml" not in entry
    assert "default_branch" not in entry
    # Lean adoption-runtime facts are preserved.
    assert entry["config_dir"] == "~/.dotfiles"
    assert entry["expose_agent"] is True
    assert entry["base_repo"] is True
    assert entry["display_name"] == "Dotfiles"
    assert entry["wsl"] == {"state": "bootstrap"}


def test_projects_schema_registered_at_v2():
    # The schema id must exist even if the vendored lib is unavailable.
    assert config_migrations.SCHEMA_PROJECTS == "agent-worktrees/projects"


# ---------------------------------------------------------------------------
# register_project -- lean write + preserve-existing
# ---------------------------------------------------------------------------

def _patch_registry_path(monkeypatch, tmp_path: Path) -> Path:
    target = tmp_path / "projects.yaml"
    monkeypatch.setattr(installer, "projects_yaml_path", lambda: target)
    monkeypatch.setattr(installer.output, "ok", lambda *_a, **_k: None)
    return target


def test_register_project_writes_lean(monkeypatch, tmp_path: Path):
    target = _patch_registry_path(monkeypatch, tmp_path)
    installer.register_project(
        "myproj", repo_dir="D:/Src/myproj", expose_agent=True,
    )
    text = target.read_text(encoding="utf-8")
    assert "schema_version: 2" in text

    data = yaml.safe_load(text)
    entry = data["projects"]["myproj"]
    # Identity/location facts are not persisted (resolved from repos.yaml).
    assert "anchor" not in entry
    assert "machines_yaml" not in entry
    assert "default_branch" not in entry
    assert entry["config_dir"] == "~/.myproj"
    assert entry["expose_agent"] is True
    assert "registered_at" in entry


def test_register_project_preserves_existing_on_contextless_reregister(
    monkeypatch, tmp_path: Path
):
    target = _patch_registry_path(monkeypatch, tmp_path)
    # A pre-migration FAT entry with adoption-runtime facts set.
    target.write_text(
        "schema_version: 1\n"
        "projects:\n"
        "  myproj:\n"
        "    anchor: D:/Src/myproj\n"
        "    default_branch: main\n"
        "    expose_agent: false\n"
        "    base_repo: true\n"
        "    elevated: true\n"
        "    display_name: MyProj\n"
        "    wsl:\n"
        "      state: adopted\n"
        "      distro: Ubuntu\n",
        encoding="utf-8",
    )
    # Re-register with NO explicit context (marketplace payload update path).
    installer.register_project("myproj")

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    entry = data["projects"]["myproj"]
    # Identity/location facts dropped.
    assert "anchor" not in entry
    assert "default_branch" not in entry
    # Adoption-runtime facts preserved (not clobbered by a context-less update).
    assert entry["expose_agent"] is False
    assert entry["base_repo"] is True
    assert entry["elevated"] is True
    assert entry["display_name"] == "MyProj"
    assert entry["wsl"]["distro"] == "Ubuntu"


def test_register_project_records_display_name(monkeypatch, tmp_path: Path):
    target = _patch_registry_path(monkeypatch, tmp_path)
    installer.register_project("spo-core", display_name="SPO.Core")
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["projects"]["spo-core"]["display_name"] == "SPO.Core"


# ---------------------------------------------------------------------------
# register-project-entry subcommand
# ---------------------------------------------------------------------------

def _entry_args(**over) -> argparse.Namespace:
    base = dict(
        project="myproj", repo_dir=None, display_name=None, expose_agent=None,
        base_repo=None, elevated=None, wsl_state=None, wsl_distro=None,
        wsl_path=None,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_subcommand_uses_positional_project_not_flag():
    """Regression: the project must be POSITIONAL. main() pre-pops a global
    --project/-p selector before argparse, so a --project flag here would be
    swallowed and the installer registration would silently no-op."""
    ns = m.build_parser().parse_args(["register-project-entry", "myproj"])
    assert ns.project == "myproj"
    # And the extractor that caused the collision leaves a positional alone.
    remaining, project = m._extract_project_flag(["register-project-entry", "myproj"])
    assert project is None
    assert remaining == ["register-project-entry", "myproj"]


def test_subcommand_resolves_expose_agent_from_repos(monkeypatch, tmp_path: Path):
    target = _patch_registry_path(monkeypatch, tmp_path)

    class _Entry:
        agent = False

    monkeypatch.setattr(m, "inst", installer)
    monkeypatch.setattr(
        "agent_worktrees.repos.find_repo",
        lambda name: _Entry() if name == "myproj" else None,
    )
    rc = m.cmd_register_project_entry(_entry_args())
    assert rc == 0
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    # repos.yaml agent=False -> reference-only, even though not forced on the CLI.
    assert data["projects"]["myproj"]["expose_agent"] is False


def test_subcommand_force_no_expose_agent(monkeypatch, tmp_path: Path):
    target = _patch_registry_path(monkeypatch, tmp_path)
    monkeypatch.setattr(m, "inst", installer)
    # Explicit override wins and repos lookup is not consulted.
    rc = m.cmd_register_project_entry(_entry_args(expose_agent=False))
    assert rc == 0
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["projects"]["myproj"]["expose_agent"] is False


def test_subcommand_registers_repo_identity_before_project(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    order: list[str] = []
    monkeypatch.setattr("agent_worktrees.repos.find_repo", lambda _name: None)
    monkeypatch.setattr(
        "agent_worktrees.repos.add_repo",
        lambda *args, **kwargs: order.append("repo"),
    )
    monkeypatch.setattr(
        m.subprocess,
        "run",
        lambda *args, **kwargs: argparse.Namespace(returncode=1, stdout=""),
    )
    monkeypatch.setattr(
        installer,
        "register_project",
        lambda *args, **kwargs: order.append("project"),
    )

    rc = m.cmd_register_project_entry(_entry_args(repo_dir=str(repo)))

    assert rc == 0
    assert order == ["repo", "project"]


def test_subcommand_preserves_hidden_repo_with_repo_dir(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    seen: dict[str, object] = {}

    class _Entry:
        repo_class = "worktree"
        agent = False

    monkeypatch.setattr(
        "agent_worktrees.repos.find_repo",
        lambda _name: _Entry(),
    )
    monkeypatch.setattr(
        "agent_worktrees.repos.add_repo",
        lambda *args, **kwargs: seen.update(repo_agent=kwargs["agent"]),
    )
    monkeypatch.setattr(
        m.subprocess,
        "run",
        lambda *args, **kwargs: argparse.Namespace(returncode=1, stdout=""),
    )
    monkeypatch.setattr(
        installer,
        "register_project",
        lambda *args, **kwargs: seen.update(
            project_exposure=kwargs["expose_agent"]
        ),
    )

    rc = m.cmd_register_project_entry(_entry_args(repo_dir=str(repo)))

    assert rc == 0
    assert seen == {"repo_agent": False, "project_exposure": False}


# ---------------------------------------------------------------------------
# Reserved-name guard -- the runtime is not a project
# ---------------------------------------------------------------------------

def test_register_refuses_reserved_runtime_name(monkeypatch, tmp_path: Path):
    """``agent-worktrees`` is the runtime's own install dir (it carries a global
    config.yaml), so the installer's ``~/.<cwd>/config.yaml`` project inference
    false-positives when run from a dir named ``agent-worktrees``. The single
    registry writer must refuse it, or projects.yaml grows a bogus launchable
    project (the "Agent Worktrees" Terminal profile backed by no repo)."""
    monkeypatch.setattr(installer.output, "skipped", lambda *_a, **_k: None)
    target = _patch_registry_path(monkeypatch, tmp_path)
    installer.register_project("agent-worktrees", expose_agent=True)
    # No file written / no entry created.
    if target.exists():
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        assert "agent-worktrees" not in (data.get("projects") or {})


def test_windows_register_refuses_reserved_name_case_insensitively(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    monkeypatch.setattr(installer.output, "skipped", lambda *_a, **_k: None)
    target = _patch_registry_path(monkeypatch, tmp_path)

    installer.register_project("Agent-Worktrees", expose_agent=True)

    assert not target.exists()


def test_windows_register_rejects_casefolded_project_collision(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    target = _patch_registry_path(monkeypatch, tmp_path)
    installer.register_project("demo", expose_agent=True)

    with pytest.raises(
        installer.BinstubOwnershipError,
        match="collides with 'demo'",
    ):
        installer.register_project("Demo", expose_agent=True)

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert set(data["projects"]) == {"demo"}


def test_prune_reserved_projects_self_heals(monkeypatch, tmp_path: Path):
    """A machine that a prior buggy install already polluted must self-heal:
    prune_reserved_projects drops the reserved entry, leaving real projects."""
    monkeypatch.setattr(installer.output, "changed", lambda *_a, **_k: None)
    target = _patch_registry_path(monkeypatch, tmp_path)
    target.write_text(
        "schema_version: 2\n"
        "projects:\n"
        "  agent-worktrees:\n"
        "    config_dir: ~/.agent-worktrees\n"
        "    expose_agent: true\n"
        "  dotfiles:\n"
        "    config_dir: ~/.dotfiles\n",
        encoding="utf-8",
    )
    removed = installer.prune_reserved_projects()
    assert removed == ["agent-worktrees"]
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert "agent-worktrees" not in data["projects"]
    assert "dotfiles" in data["projects"]


def test_prune_reserved_projects_noop_when_clean(monkeypatch, tmp_path: Path):
    target = _patch_registry_path(monkeypatch, tmp_path)
    target.write_text(
        "schema_version: 2\nprojects:\n  dotfiles:\n    config_dir: ~/.dotfiles\n",
        encoding="utf-8",
    )
    before = target.read_text(encoding="utf-8")
    assert installer.prune_reserved_projects() == []
    # Untouched when there is nothing reserved to prune.
    assert target.read_text(encoding="utf-8") == before
