from __future__ import annotations

from pathlib import Path

from worktree_manager import harness_state
from worktree_manager.production_picker import context, project_config


def _project(name: str, anchor: Path) -> harness_state.ProjectInfo:
    return harness_state.ProjectInfo(
        name=name,
        config_dir=None,
        expose_agent=False,
        knowledge_repo=None,
        profiles=0,
        repo=None,
        anchor=str(anchor),
    )


def test_load_config_uses_registry_default_branch_when_inrepo_config_missing(
    monkeypatch, tmp_path
):
    project_root = tmp_path / "demo"
    project_root.mkdir()

    monkeypatch.setattr(
        harness_state,
        "build_projects",
        lambda home_dir=None: [_project("demo", project_root)],
    )
    monkeypatch.setattr(
        harness_state,
        "repos_registry",
        lambda home_dir=None: {
            "repos": {"demo": {"windows": str(project_root), "default_branch": "dev"}}
        },
    )
    monkeypatch.setattr(
        harness_state,
        "project_config",
        lambda name, home_dir=None: {"machine": "host"} if name == "demo" else {},
    )
    context.set_project("demo")
    try:
        cfg = project_config.load_config()
    finally:
        context.reset()

    assert cfg.repo_name == "demo"
    assert cfg.default_repo.anchor == str(project_root)
    assert cfg.default_repo.default_branch == "dev"


def test_machines_yaml_path_uses_knowledge_repo_overlay(monkeypatch, tmp_path):
    launch_root = tmp_path / "launch"
    launch_root.mkdir()
    knowledge_root = tmp_path / "knowledge"
    overlay = knowledge_root / ".agent-worktrees" / "machines.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("machines: {}\n", encoding="utf-8")

    monkeypatch.setattr(
        harness_state,
        "build_projects",
        lambda home_dir=None: [
            _project("demo", launch_root),
            _project("knowledge", knowledge_root),
        ],
    )
    monkeypatch.setattr(
        harness_state,
        "project_config",
        lambda name, home_dir=None: {"knowledge_repo": "knowledge"} if name == "demo" else {},
    )
    context.set_project("demo")
    try:
        resolved = project_config.machines_yaml_path(launch_root)
    finally:
        context.reset()

    assert resolved == overlay


def test_machines_yaml_path_prefers_active_repo_registry(monkeypatch, tmp_path):
    launch_root = tmp_path / "launch"
    local = launch_root / ".agent-worktrees" / "machines.yaml"
    local.parent.mkdir(parents=True)
    local.write_text("machines: {}\n", encoding="utf-8")
    knowledge_root = tmp_path / "knowledge"
    overlay = knowledge_root / ".agent-worktrees" / "machines.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("machines: {shadow: {}}\n", encoding="utf-8")

    monkeypatch.setattr(
        harness_state,
        "build_projects",
        lambda home_dir=None: [
            _project("demo", launch_root),
            _project("knowledge", knowledge_root),
        ],
    )
    monkeypatch.setattr(
        harness_state,
        "project_config",
        lambda name, home_dir=None: {"knowledge_repo": "knowledge"} if name == "demo" else {},
    )
    context.set_project("demo")
    try:
        resolved = project_config.machines_yaml_path(launch_root)
    finally:
        context.reset()

    assert resolved == local


def test_load_machines_yaml_merges_canonical_and_legacy_disjoint_keys(tmp_path):
    """Regression (ThomasMichon/copilot-extensions#7914): the canonical
    in-repo ``.agent-worktrees/machines.yaml`` (container-fleet entries) must
    never make every real facility machine in the legacy root file disappear
    from the Picker's multi-machine roster -- the canonical file's own
    header documents this as strictly additive. Before the fix,
    ``_load_machines_yaml`` read only whichever file ``machines_yaml_path``
    resolved first, so a Windows Worktree Manager Picker showed only its own
    local tab, no remote machines, the instant the in-repo file existed."""
    root = tmp_path / "repo"
    canonical = root / ".agent-worktrees" / "machines.yaml"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        "machines:\n  container-fleet:\n    display_name: Container Fleet\n",
        encoding="utf-8",
    )
    legacy = root / "machines.yaml"
    legacy.write_text(
        "machines:\n  lambda-core:\n    display_name: Lambda-Core\n"
        "  borealis:\n    display_name: Borealis\n",
        encoding="utf-8",
    )
    entries = project_config.load_machines_yaml(root)
    assert set(entries) == {"container-fleet", "lambda-core", "borealis"}


def test_load_machines_yaml_canonical_wins_on_key_collision(tmp_path):
    root = tmp_path / "repo"
    canonical = root / ".agent-worktrees" / "machines.yaml"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        "machines:\n  m1:\n    display_name: Canonical\n", encoding="utf-8"
    )
    legacy = root / "machines.yaml"
    legacy.write_text(
        "machines:\n  m1:\n    display_name: Legacy\n", encoding="utf-8"
    )
    entries = project_config.load_machines_yaml(root)
    assert entries["m1"].display_name == "Canonical"
