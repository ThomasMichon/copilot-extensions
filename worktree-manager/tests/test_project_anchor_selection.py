from pathlib import Path

import pytest
import yaml

from worktree_manager import harness_state as state


def write_registries(home, repos, projects):
    root = home / ".agent-worktrees"
    root.mkdir()
    (root / "repos.yaml").write_text(yaml.safe_dump({"repos": repos}), encoding="utf-8")
    (root / "projects.yaml").write_text(yaml.safe_dump({"projects": projects}), encoding="utf-8")
    return root


@pytest.mark.parametrize("platform,repo_entry,project_entry", [
    ("windows", {"windows": "selected", "linux": "other"}, {"anchor": "override"}),
    ("linux", {"windows": "fallback", "wsl": "guest"}, {"anchor": "override"}),
    ("linux", {"linux": "selected", "windows": "other"}, {}),
    ("macos", {"linux": "fallback", "wsl": "guest"}, {}),
    ("windows", {}, {"anchor": "override"}),
    ("windows", {"windows": ""}, {"anchor": "override"}),
])
def test_selected_anchor_matches_real_builder_precedence(
    tmp_path, monkeypatch, platform, repo_entry, project_entry,
):
    repo_entry = {key: str(tmp_path / value) if value else value for key, value in repo_entry.items()}
    project_entry = {key: str(tmp_path / value) for key, value in project_entry.items()}
    root = write_registries(tmp_path, {"selected": repo_entry}, {"selected": project_entry})
    monkeypatch.setattr(state, "_platform_key", lambda: platform)
    expected = state.build_projects(tmp_path)[0].anchor
    reads = []

    def reader(path):
        reads.append(path)
        return state._read_yaml(path, strict=True)

    def forbidden(*a, **kw):
        pytest.fail("selected-entry resolution materialized unrelated state")

    for name in ("build_projects", "build_repos", "pr_model", "repo_enabled_plugins", "project_roster"):
        monkeypatch.setattr(state, name, forbidden)
    monkeypatch.setattr(state.subprocess, "run", forbidden)
    selected = state.project_anchor_selection("selected", tmp_path, reader=reader)
    assert selected.anchor == expected
    assert reads == [root / "repos.yaml", root / "projects.yaml"]
    assert selected.dependency_paths == tuple(reads)


def test_repo_without_project_is_not_a_supplier(tmp_path):
    root = write_registries(tmp_path, {"only-repo": {"windows": "repo"}}, {})
    selected = state.project_anchor_selection("only-repo", tmp_path)
    assert selected.anchor is None
    assert selected.dependency_paths == (root / "repos.yaml", root / "projects.yaml")


def test_knowledge_reader_can_select_repo_without_adopting_it(tmp_path, monkeypatch):
    anchor = str(tmp_path / "knowledge")
    root = write_registries(
        tmp_path, {"knowledge": {"class": "knowledge", "windows": anchor}}, {},
    )
    monkeypatch.setattr(state, "_platform_key", lambda: "windows")
    before = (root / "projects.yaml").read_bytes()
    selected = state.project_anchor_selection("knowledge", tmp_path, require_project=False)
    assert selected.anchor == anchor
    assert (root / "projects.yaml").read_bytes() == before
    assert state.project_anchor_selection("knowledge", tmp_path).anchor is None
    assert state.project_anchor_selection("absent", tmp_path, require_project=False).anchor is None


def test_absent_registries_are_dependencies_without_creation(tmp_path):
    selected = state.project_anchor_selection("absent", tmp_path)
    assert selected.anchor is None
    assert len(selected.dependency_paths) == 2
    assert all(not path.exists() for path in selected.dependency_paths)


def test_explicit_registry_roots_do_not_read_project_configs(tmp_path, monkeypatch):
    root = write_registries(tmp_path, {}, {"selected": {"anchor": "override"}})
    roots = state.ProfileInputRoots(registry=root, projects={})
    reads = []
    read_text = Path.read_text

    def observed(path, *args, **kwargs):
        reads.append(path)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", observed)
    selected = state.project_anchor_selection("selected", input_roots=roots)
    assert selected.anchor == "override"
    assert reads == list(selected.dependency_paths)


def test_malformed_selected_registry_does_not_become_absent(tmp_path):
    root = write_registries(tmp_path, {}, {})
    (root / "projects.yaml").write_text("projects: []\n")
    with pytest.raises(ValueError, match="mapping"):
        state.project_anchor_selection("selected", tmp_path)


@pytest.mark.parametrize("kind", ["repos", "projects"])
@pytest.mark.parametrize("value", [[], "", False, 0])
def test_falsey_selected_entries_are_rejected(tmp_path, kind, value):
    repos = {"selected": {"windows": "registered"}}
    projects = {"selected": {"anchor": "override"}}
    (repos if kind == "repos" else projects)["selected"] = value
    write_registries(tmp_path, repos, projects)
    with pytest.raises(ValueError, match="entries must be mappings"):
        state.project_anchor_selection("selected", tmp_path)


@pytest.mark.parametrize("repos,projects,expected", [
    ({}, {}, None),
    ({"selected": None}, {"selected": {"anchor": "override"}}, "override"),
    ({"selected": {"windows": "registered"}}, {"selected": None}, "registered"),
    ({}, {"selected": None}, None),
    ({"selected": None}, {"selected": None}, None),
])
def test_selected_absent_and_null_entries_remain_compatible(tmp_path, monkeypatch, repos, projects, expected):
    write_registries(tmp_path, repos, projects)
    monkeypatch.setattr(state, "_platform_key", lambda: "windows")
    assert state.project_anchor_selection("selected", tmp_path).anchor == expected
