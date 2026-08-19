from __future__ import annotations

from agent_machines import discover

from ._helpers import base_package, enable_plugin, write_package


def _registry(srcroot, **repos):
    return {"schema_version": 1, "srcroot": {"windows": str(srcroot), "linux": str(srcroot),
            "wsl": str(srcroot)}, "repos": repos}


def _projects(*names):
    return {"schema_version": 2, "projects": {n: {"config_dir": f"~/.{n}"} for n in names}}


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


def test_discover_only_considers_adopted_projects(tmp_path):
    # A repo carrying packages but NOT registered as a project is ignored.
    srcroot = tmp_path / "Src"
    repo = srcroot / "acme"
    write_package(repo, "defaults.yaml", base_package(gate=["*"]))
    reg = _registry(srcroot, acme={"class": "worktree"})
    assert discover.discover(machine="box-1", registry=reg, projects=_projects("other")) == []
    assert len(discover.discover(machine="box-1", registry=reg, projects=_projects("acme"))) == 1


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
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
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
