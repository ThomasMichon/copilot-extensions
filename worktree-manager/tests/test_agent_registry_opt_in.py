"""Manager registry views do not infer agent or project adoption from a path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktree_manager import harness_state, terminal_fragment


def _write(home: Path, *, agent: object = None, adopted: bool = False) -> None:
    root = home / ".agent-worktrees"
    root.mkdir(parents=True)
    source = home / "source"
    source.mkdir()
    repo: dict[str, object] = {
        "class": "worktree", **{platform: str(source) for platform in ("windows", "wsl", "linux")},
    }
    if agent is not None:
        repo["agent"] = agent
    (root / "repos.yaml").write_text(json.dumps({"repos": {"source": repo}}), encoding="utf-8")
    (root / "projects.yaml").write_text(json.dumps({
        "schema_version": 2, "projects": {"source": {"expose_agent": True}} if adopted else {},
    }), encoding="utf-8")


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


@pytest.mark.parametrize("agent", [None, False, True])
def test_registry_agent_requires_explicit_true_and_never_adopts_a_source(
    tmp_path: Path, agent: bool | None,
) -> None:
    _write(tmp_path, agent=agent)
    before = _snapshot(tmp_path)
    repo = harness_state.build_repos(tmp_path, strict=True)[0]
    assert repo.agent is (agent is True)
    assert repo.is_project is False
    assert harness_state.build_projects(tmp_path) == []
    assert terminal_fragment.collect_local_projects(current_project="source", home_dir=tmp_path) == []
    assert _snapshot(tmp_path) == before


def test_project_registration_does_not_imply_repo_agent_exposure(tmp_path: Path) -> None:
    _write(tmp_path, adopted=True)
    before = _snapshot(tmp_path)
    project = harness_state.build_projects(tmp_path)[0]
    assert project.name == "source" and project.expose_agent is True
    assert project.repo is not None and project.repo.agent is False
    inputs = terminal_fragment.collect_local_projects(current_project="source", home_dir=tmp_path)
    assert len(inputs) == 1 and inputs[0].agent_exposed is False
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("agent", ["false", "true", 1])
def test_invalid_agent_declaration_does_not_become_discovery_opt_in(
    tmp_path: Path, agent: object,
) -> None:
    _write(tmp_path, agent=agent)
    before = _snapshot(tmp_path)
    assert harness_state.build_repos(tmp_path)[0].agent is False
    with pytest.raises(ValueError, match="agent must be a Boolean"):
        harness_state.build_repos(tmp_path, strict=True)
    assert _snapshot(tmp_path) == before
