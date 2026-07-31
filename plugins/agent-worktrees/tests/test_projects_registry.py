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
