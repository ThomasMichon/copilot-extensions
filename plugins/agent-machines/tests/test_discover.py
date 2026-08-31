from __future__ import annotations

import pytest
import yaml

from agent_machines import discover
from agent_machines import reconcile, validator
from agent_machines.manifest import ManifestError

from ._helpers import base_package, enable_plugin, write_package


def _registry(srcroot, **repos):
    return {"schema_version": 1, "srcroot": {"windows": str(srcroot), "linux": str(srcroot),
            "wsl": str(srcroot)}, "repos": repos}


def _projects(*names):
    return {"schema_version": 2, "projects": {n: {"config_dir": f"~/.{n}"} for n in names}}


def _bind_knowledge(tmp_path, project, knowledge):
    config_dir = tmp_path / f".{project}"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump({"knowledge_repo": knowledge}),
        encoding="utf-8",
    )
    return {"config_dir": str(config_dir)}


def _mark_external_state(repo):
    config = repo / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("stateless: true\n", encoding="utf-8")


def test_discover_finds_gated_packages(tmp_path, monkeypatch):
    srcroot = tmp_path / "Src"
    repo = srcroot / "acme"
    write_package(repo, "defaults.yaml", base_package(gate=["box-1"]))
    enable_plugin(repo)

    reg = _registry(srcroot, acme={"class": "worktree"})
    found = discover.discover(machine="box-1", registry=reg, projects=_projects("acme"))
    assert len(found) == 1
    assert found[0].name == "acme"
    assert found[0].enabled is True
    assert found[0].packages[0].name == "acme/copilot-defaults"


def test_discover_respects_gate(tmp_path):
    srcroot = tmp_path / "Src"
    repo = srcroot / "acme"
    write_package(repo, "defaults.yaml", base_package(gate=["box-1"]))
    reg = _registry(srcroot, acme={"class": "worktree"})
    assert discover.discover(machine="other-box", registry=reg, projects=_projects("acme")) == []


def test_discover_combines_all_and_matching_machine_packages(tmp_path):
    srcroot = tmp_path / "Src"
    repo = srcroot / "acme"
    write_package(repo, "shared.yaml", base_package(name="acme/shared", gate=["*"]))
    write_package(
        repo,
        "specific.yaml",
        base_package(name="acme/specific", gate=["*"]),
        machine="Box-1",
    )
    write_package(
        repo,
        "other.yaml",
        base_package(name="acme/other", gate=["*"]),
        machine="box-2",
    )
    reg = _registry(srcroot, acme={"class": "worktree"})
    found = discover.discover(machine="box-1", registry=reg, projects=_projects("acme"))
    assert [pkg.name for pkg in found[0].packages] == ["acme/shared", "acme/specific"]


def test_machine_directory_rejects_contradictory_explicit_gate(tmp_path):
    srcroot = tmp_path / "Src"
    repo = srcroot / "acme"
    write_package(
        repo,
        "specific.yaml",
        base_package(name="acme/specific", gate=["box-2"]),
        machine="box-1",
    )
    reg = _registry(srcroot, acme={"class": "worktree"})
    with pytest.raises(ManifestError, match="gate excludes its containing machine"):
        discover.discover(machine="box-1", registry=reg, projects=_projects("acme"))


def test_legacy_layout_is_fallback_when_canonical_root_absent(tmp_path):
    srcroot = tmp_path / "Src"
    repo = srcroot / "acme"
    write_package(
        repo,
        "legacy.yaml",
        base_package(name="acme/legacy", gate=["*"]),
        legacy=True,
    )
    reg = _registry(srcroot, acme={"class": "worktree"})
    found = discover.discover(machine="box-1", registry=reg, projects=_projects("acme"))
    assert [pkg.name for pkg in found[0].packages] == ["acme/legacy"]


def test_canonical_root_suppresses_legacy_layout(tmp_path):
    srcroot = tmp_path / "Src"
    repo = srcroot / "acme"
    write_package(
        repo,
        "legacy.yaml",
        base_package(name="acme/legacy", gate=["*"]),
        legacy=True,
    )
    write_package(repo, "current.yaml", base_package(name="acme/current", gate=["*"]))
    reg = _registry(srcroot, acme={"class": "worktree"})
    found = discover.discover(machine="box-1", registry=reg, projects=_projects("acme"))
    assert [pkg.name for pkg in found[0].packages] == ["acme/current"]


def test_flat_package_under_canonical_root_fails_closed(tmp_path):
    repo = tmp_path / "acme"
    path = repo / ".agent-machines" / "defaults.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("schema_version: 1\npackage: acme/defaults\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="packages belong directly under all/"):
        discover.packages_in_repo(repo, "acme", "box-1")


def test_nested_package_under_all_fails_closed(tmp_path):
    repo = tmp_path / "acme"
    path = repo / ".agent-machines" / "all" / "nested" / "defaults.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("schema_version: 1\npackage: acme/defaults\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="must be direct children"):
        discover.packages_in_repo(repo, "acme", "box-1")


def test_legacy_duplicate_package_names_preserve_compatibility(tmp_path):
    repo = tmp_path / "acme"
    write_package(
        repo,
        "one.yaml",
        base_package(name="acme/duplicate", gate=["*"]),
        legacy=True,
    )
    write_package(
        repo,
        "two.yaml",
        base_package(name="acme/duplicate", gate=["*"]),
        legacy=True,
    )
    assert len(discover.packages_in_repo(repo, "acme", "box-1")) == 2


def test_duplicate_package_names_across_scopes_fail(tmp_path):
    repo = tmp_path / "acme"
    write_package(repo, "shared.yaml", base_package(name="acme/duplicate", gate=["*"]))
    write_package(
        repo,
        "specific.yaml",
        base_package(name="acme/duplicate", gate=["*"]),
        machine="box-1",
    )
    with pytest.raises(ManifestError, match="independent complete packages"):
        discover.packages_in_repo(repo, "acme", "box-1")


def test_discover_only_considers_adopted_projects(tmp_path):
    # A repo carrying packages but NOT registered as a project is ignored.
    srcroot = tmp_path / "Src"
    repo = srcroot / "acme"
    write_package(repo, "defaults.yaml", base_package(gate=["*"]))
    reg = _registry(srcroot, acme={"class": "worktree"})
    assert discover.discover(machine="box-1", registry=reg, projects=_projects("other")) == []
    assert len(discover.discover(machine="box-1", registry=reg, projects=_projects("acme"))) == 1


def test_discover_grafts_bound_supplemental_repo(tmp_path):
    srcroot = tmp_path / "Src"
    harness = srcroot / "harness"
    knowledge = srcroot / "knowledge"
    write_package(harness, "harness.yaml", base_package(name="harness/base", gate=["*"]))
    _mark_external_state(harness)
    write_package(
        knowledge,
        "knowledge.yaml",
        base_package(name="knowledge/preferences", gate=["*"]),
    )
    reg = _registry(
        srcroot,
        harness={"class": "worktree"},
        knowledge={"class": "worktree"},
    )
    projects = {
        "projects": {
            "harness": _bind_knowledge(tmp_path, "harness", "knowledge"),
        }
    }

    found = discover.discover(machine="box-1", registry=reg, projects=projects)

    assert [repo.name for repo in found] == ["harness", "knowledge"]
    assert [pkg.source_repo for repo in found for pkg in repo.packages] == [
        "harness",
        "knowledge",
    ]


def test_bound_supplemental_repo_is_deduplicated_when_adopted(tmp_path):
    srcroot = tmp_path / "Src"
    harness = srcroot / "harness"
    knowledge = srcroot / "knowledge"
    write_package(harness, "harness.yaml", base_package(name="harness/base", gate=["*"]))
    _mark_external_state(harness)
    write_package(
        knowledge,
        "knowledge.yaml",
        base_package(name="knowledge/preferences", gate=["*"]),
    )
    reg = _registry(
        srcroot,
        harness={"class": "worktree"},
        knowledge={"class": "worktree"},
    )
    projects = {
        "projects": {
            "harness": _bind_knowledge(tmp_path, "harness", "knowledge"),
            "KNOWLEDGE": {"config_dir": str(tmp_path / ".knowledge")},
        }
    }

    found = discover.discover(machine="box-1", registry=reg, projects=projects)

    assert [repo.name for repo in found] == ["harness", "knowledge"]


def test_normal_project_ignores_inactive_knowledge_pointer(tmp_path):
    srcroot = tmp_path / "Src"
    project = srcroot / "project"
    write_package(project, "project.yaml", base_package(name="project/base", gate=["*"]))
    reg = _registry(srcroot, project={"class": "worktree"})
    projects = {
        "projects": {
            "project": _bind_knowledge(tmp_path, "project", "missing"),
        }
    }

    found = discover.discover(machine="box-1", registry=reg, projects=projects)

    assert [repo.name for repo in found] == ["project"]


def test_stateless_project_uses_global_knowledge_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    global_config = tmp_path / ".agent-worktrees" / "config.yaml"
    global_config.parent.mkdir()
    global_config.write_text("knowledge_repo: knowledge\n", encoding="utf-8")
    srcroot = tmp_path / "Src"
    harness = srcroot / "harness"
    knowledge = srcroot / "knowledge"
    write_package(harness, "harness.yaml", base_package(name="harness/base", gate=["*"]))
    _mark_external_state(harness)
    write_package(
        knowledge,
        "knowledge.yaml",
        base_package(name="knowledge/preferences", gate=["*"]),
    )
    reg = _registry(
        srcroot,
        harness={"class": "worktree"},
        knowledge={"class": "worktree"},
    )
    projects = {"projects": {"harness": {"config_dir": str(tmp_path / ".harness")}}}

    found = discover.discover(machine="box-1", registry=reg, projects=projects)

    assert [repo.name for repo in found] == ["harness", "knowledge"]


def test_bound_supplemental_repo_requires_canonical_registration(tmp_path):
    srcroot = tmp_path / "Src"
    harness = srcroot / "harness"
    write_package(harness, "harness.yaml", base_package(name="harness/base", gate=["*"]))
    _mark_external_state(harness)
    reg = _registry(srcroot, harness={"class": "worktree"})
    projects = {
        "projects": {
            "harness": _bind_knowledge(tmp_path, "harness", "knowledge"),
        }
    }

    with pytest.raises(ManifestError, match="no canonical repos.yaml entry"):
        discover.discover(machine="box-1", registry=reg, projects=projects)


def test_bound_supplemental_repo_unavailable_fails_loudly(tmp_path):
    srcroot = tmp_path / "Src"
    harness = srcroot / "harness"
    write_package(harness, "harness.yaml", base_package(name="harness/base", gate=["*"]))
    _mark_external_state(harness)
    reg = _registry(
        srcroot,
        harness={"class": "worktree"},
        knowledge={"class": "worktree"},
    )
    projects = {
        "projects": {
            "harness": _bind_knowledge(tmp_path, "harness", "knowledge"),
        }
    }

    with pytest.raises(ManifestError, match="required by harness is unavailable"):
        discover.discover(machine="box-1", registry=reg, projects=projects)


def test_grafted_packages_participate_in_cross_repo_conflict_validation(tmp_path):
    srcroot = tmp_path / "Src"
    harness = srcroot / "harness"
    knowledge = srcroot / "knowledge"
    write_package(harness, "harness.yaml", base_package(name="harness/base", gate=["*"]))
    _mark_external_state(harness)
    conflicting = base_package(name="knowledge/preferences", gate=["*"])
    conflicting["manage"]["copilot.settings"]["values"]["model"] = "other"
    write_package(knowledge, "knowledge.yaml", conflicting)
    reg = _registry(
        srcroot,
        harness={"class": "worktree"},
        knowledge={"class": "worktree"},
    )
    projects = {
        "projects": {
            "harness": _bind_knowledge(tmp_path, "harness", "knowledge"),
        }
    }

    packages = [
        pkg
        for repo in discover.discover(machine="box-1", registry=reg, projects=projects)
        for pkg in repo.packages
    ]
    findings = validator.validate(reconcile.resolve_union(packages, "box-1"), "box-1")

    assert any(finding.level == "error" for finding in findings)


def test_discover_uses_explicit_path_key(tmp_path):
    repo = tmp_path / "elsewhere" / "acme"
    write_package(repo, "d.yaml", base_package(gate=["*"]))
    reg = {"repos": {"acme": {"class": "worktree", "windows": str(repo),
           "linux": str(repo), "wsl": str(repo)}}}
    found = discover.discover(machine="box-1", registry=reg, projects=_projects("acme"))
    assert len(found) == 1
    assert found[0].path == repo


def test_resolve_repo_path_expands_tilde(tmp_path, monkeypatch):
    # A ~-shorthand path in repos.yaml must be expanded, else is_dir() is False
    # in discover() and the repo is silently skipped.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    resolved = discover.resolve_repo_path(
        "acme", {"wsl": "~/src/acme"}, {}, "wsl")
    assert resolved == tmp_path / "src" / "acme"
    # srcroot fallback expands too.
    resolved_root = discover.resolve_repo_path(
        "acme", {}, {"wsl": "~/src"}, "wsl")
    assert resolved_root == tmp_path / "src" / "acme"


def test_discover_expands_tilde_path(tmp_path, monkeypatch):
    # End-to-end: a registry entry using ~ still discovers the repo's packages.
    # Pin the platform so the test is deterministic on any runner (the wsl key
    # is only consulted when current_platform() == "wsl").
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(discover, "current_platform", lambda: "wsl")
    repo = tmp_path / "src" / "acme"
    write_package(repo, "d.yaml", base_package(gate=["*"]))
    reg = {"repos": {"acme": {"class": "worktree", "wsl": "~/src/acme"}}}
    found = discover.discover(machine="box-1", registry=reg, projects=_projects("acme"))
    assert len(found) == 1
    assert found[0].path == repo


def test_discover_missing_registry_degrades(tmp_path, monkeypatch):
    # No projects.yaml -> empty read -> empty set (a la carte independence).
    monkeypatch.setattr(discover, "read_projects", lambda path=None: {})
    monkeypatch.setattr(discover, "read_registry", lambda path=None: {})
    assert discover.discover(machine="box-1") == []


def test_require_enable_filters_unenabled(tmp_path):
    srcroot = tmp_path / "Src"
    repo = srcroot / "acme"
    write_package(repo, "d.yaml", base_package(gate=["*"]))
    # no enable_plugin() call
    reg = _registry(srcroot, acme={"class": "worktree"})
    kw = {"machine": "box-1", "registry": reg, "projects": _projects("acme")}
    assert discover.discover(require_enable=True, **kw) == []
    assert len(discover.discover(**kw)) == 1
