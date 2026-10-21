"""A related worktree repository remains usable without full project adoption."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from agent_worktrees import config as cfg, installer, related, repos
from test_control_plane_binding_integration import _checkout, _run


@pytest.mark.parametrize("agent", [False, True])
def test_unadopt_preserves_related_repository_and_real_worktree_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: bool,
) -> None:
    home = tmp_path / "home"
    native = home / ".agent-worktrees"
    native.mkdir(parents=True)
    (native / "config.yaml").write_text("machine: test-node\n", encoding="utf-8")
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(key, str(home))
    monkeypatch.setenv("AGENT_WORKTREES_HOME", str(native))
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(native / "repos.yaml"))
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(native / "projects.yaml"))
    for key in (
        "AGENT_WORKTREES_PROJECT", "AGENT_WORKTREES_OWNER_REF",
        "COPILOT_AGENT_SESSION_ID", "COPILOT_EXTENSIONS_CONTEXT",
    ):
        monkeypatch.delenv(key, raising=False)

    checkout = _checkout(tmp_path, "sample", "")
    repos.add_repo(
        "sample", str(checkout), repo_class="worktree", agent=agent,
        remote=str(tmp_path / "sample.git"), default_branch="main",
    )
    retained = asdict(repos.read_registry().repos["sample"])
    installer.write_projects_registry({
        "projects": {
            "sample": {"anchor": str(checkout), "expose_agent": agent},
            "other": {"anchor": str(tmp_path / "other"), "expose_agent": False},
        },
    })
    state = home / ".sample"
    state.mkdir()
    marker = state / "retained.json"
    marker.write_text('{"preserved": true}\n', encoding="utf-8")
    manifest = checkout / ".agent-worktrees" / "related.yaml"
    manifest.write_text("repos:\n  dependency: {role: related}\n", encoding="utf-8")
    related_before = manifest.read_bytes()
    bindir = installer.local_bin()
    bindir.mkdir(parents=True)
    foreign = bindir / ("sample.ps1" if os.name == "nt" else "sample")
    foreign.write_text("foreign launcher; no ownership receipt\n", encoding="utf-8")
    launcher_before = foreign.read_bytes()

    command = [sys.executable, "-m", "agent_worktrees", "--project", "sample"]
    _run([*command, "unregister", "--keep-repo-entry", "unchanged"], checkout)

    assert asdict(repos.read_registry().repos["sample"]) == retained
    assert set(installer.read_projects_registry()["projects"]) == {"other"}
    assert marker.read_text(encoding="utf-8") == '{"preserved": true}\n'
    assert manifest.read_bytes() == related_before
    assert foreign.read_bytes() == launcher_before

    plan = related.build_resolution(
        related.RelatedEntry(name="sample", locus=related.Locus(preferred="local")),
        current_machine="test-node", repo_class="worktree",
        repo_path=str(checkout), adopted=False,
    )
    assert plan.editing_model == "worktree"
    assert any("agent-worktrees --project sample create --json" in s for s in plan.steps)
    assert not any("register sample" in s for s in plan.steps)

    created = _run(
        [*command, "create", "--json", "--no-owner", "--no-pair"], checkout,
    )
    payload = json.loads(created.stdout)
    workspace = Path(payload["worktree"]["path"])
    assert workspace.is_dir()
    assert workspace != checkout
    assert _run(["git", "branch", "--show-current"], workspace).stdout.strip().startswith(
        "worktree/"
    )
    assert set(installer.read_projects_registry()["projects"]) == {"other"}
    assert asdict(repos.read_registry().repos["sample"]) == retained
    assert marker.is_file()
    assert foreign.read_bytes() == launcher_before
    assert manifest.read_bytes() == related_before
    assert (state / "worktrees").exists()
    assert cfg.project_dir("sample") == state
