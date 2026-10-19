"""First-touch tuple -> normal binding writer -> native paired-workspace proof."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs
from machine_transport import detect_platform
from agent_worktrees import config as cfg, repos, state_root, worktree_creation


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=25,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result


def _checkout(root: Path, name: str, config: str) -> Path:
    remote = root / f"{name}.git"
    checkout = root / name
    _run(["git", "init", "--initial-branch=main", str(checkout)], root)
    settings = checkout / ".agent-worktrees"
    settings.mkdir()
    (settings / "config.yaml").write_text(
        "default_branch: main\nremote: origin\n" + config,
        encoding="utf-8",
    )
    (checkout / ".gitignore").write_text(
        ".github/copilot/settings.local.json\n",
        encoding="utf-8",
    )
    _run(["git", "add", "."], checkout)
    _run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "Synthetic source",
        ],
        checkout,
    )
    _run(["git", "clone", "--bare", "--quiet", str(checkout), str(remote)], root)
    _run(["git", "remote", "add", "--fetch", "origin", str(remote)], checkout)
    _run(["git", "remote", "set-head", "origin", "main"], checkout)
    return checkout


@pytest.mark.parametrize(
    "bare_knowledge", [pytest.param(False, marks=pytest.mark.guard), True]
)
@pytest.mark.parametrize("binding_mode", ["control-plane", "explicit"])
def test_first_touch_binds_declared_roles_then_creates_real_pair(
    tmp_path, monkeypatch, bare_knowledge, binding_mode
):
    home = tmp_path / "home"
    home.mkdir()
    native_root = home / ".agent-worktrees"
    native_root.mkdir()
    (native_root / "config.yaml").write_text("machine: test-node\n", encoding="utf-8")
    for key in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(key, str(home))
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setenv("AGENT_WORKTREES_HOME", str(native_root))
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(native_root / "repos.yaml"))
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(native_root / "projects.yaml"))
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_OWNER_REF", raising=False)
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    monkeypatch.setenv("WSL_DISTRO_NAME", "inherited-transport-environment")

    harness = _checkout(tmp_path, "example-harness", "stateless: true\n")
    knowledge = _checkout(
        tmp_path,
        "example-knowledge",
        "knowledge_only: true\nissues: {provider: github, repo: example/state}\n",
    )
    if bare_knowledge:
        for args in (
            ["config", "extensions.worktreeConfig", "true"],
            ["config", "--worktree", "core.bare", "true"],
            ["config", "--unset", "core.bare"],
        ):
            _run(["git", *args], knowledge)

    for name, checkout, repo_class in (
        ("example-harness", harness, "worktree"),
        ("example-knowledge", knowledge, "knowledge"),
    ):
        if binding_mode == "explicit" and repo_class == "knowledge":
            continue
        repos.add_repo(
            name, str(checkout), repo_class=repo_class, default_branch="main",
            remote=str(tmp_path / f"{name}.git"),
        )
    topology = tmp_path / "machines.yaml"
    topology.write_text(
        "control_plane:\n"
        "  project: example-harness\n"
        "  harness: example-harness\n"
        "  knowledge: example-knowledge\n"
        "machines: {}\n",
        encoding="utf-8",
    )
    native_command = Path(sys.executable).parent / (
        "agent-worktrees.exe" if os.name == "nt" else "agent-worktrees"
    )
    assert native_command.is_file()
    binding_script = (
        Path(__file__).resolve().parents[3]
        / "plugins" / "harness-knowledge" / "skills" / "binding-knowledge"
        / "scripts" / "bind_knowledge.py"
    )
    config = home / ".example-harness" / "config.yaml"
    assert not config.exists()
    selection = (
        ["--control-plane", str(topology)]
        if binding_mode == "control-plane"
        else [
            "--harness", "example-harness",
            "--knowledge", "example-knowledge",
            "--harness-path", str(harness),
            "--knowledge-path", str(knowledge),
            "--register",
        ]
    )
    result = _run(
        [
            sys.executable,
            str(binding_script),
            *selection,
            "--agent-worktrees-path", str(native_command),
            "--home", str(home),
            "--json",
        ],
        harness,
    )
    summary = json.loads(result.stdout)
    assert summary["registration"]["status"] == "ready"
    assert summary["registration"]["class"] == "knowledge"
    assert summary["state_root"]["status"] == "ready"
    if binding_mode == "control-plane":
        assert summary["control_plane"]["harness"] == "example-harness"
        assert summary["control_plane"]["knowledge"] == "example-knowledge"
    assert "knowledge_repo: example-knowledge" in config.read_text(encoding="utf-8")
    registered = repos.read_registry(strict=True)
    for name, checkout in (
        ("example-harness", harness), ("example-knowledge", knowledge),
    ):
        assert registered.repos[name].paths == {detect_platform(): str(checkout)}
        assert repos.resolve_path(name) == str(checkout)

    previous_project = cfg.active_project()
    cfg.set_active_project("example-harness")
    try:
        config = cfg.load_config(project="example-harness")
        created = worktree_creation._create_worktree_core(
            config, inherit_parent_session=False, defer_projections=True,
        )
        workspace = Path(created["worktree"]["path"])
        pair = state_root.resolve_pair(None, cwd=str(workspace)).as_dict()
    finally:
        cfg.set_active_project(previous_project)
    assert pair["paired"] is True
    assert "error" not in pair
    assert pair["self"]["role"] == "harness"
    assert pair["sibling"]["role"] == "knowledge"
    assert pair["sibling"]["kind"] == "worktree"
    companion = Path(pair["sibling"]["path"])
    assert companion.is_dir()
    common = _run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        companion,
    )
    assert Path(common.stdout.strip()).resolve() == (knowledge / ".git").resolve()
    assert not (home / ".example-knowledge" / "config.yaml").exists()
