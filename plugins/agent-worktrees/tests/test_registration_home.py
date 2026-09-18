"""Registration writes stay within the selected harness home, not auth home."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agent_worktrees import installer, repos

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=[False, True], ids=["default-home", "agent-home"])
def registration_home(request, monkeypatch, tmp_path):
    # The suite's autouse fixture already makes Path.home() disposable.
    home = Path.home()
    credential_env = {key: os.environ.get(key) for key in ("HOME", "USERPROFILE")}
    before = {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*") if path.is_file()
    }
    state_home = tmp_path / "sandbox" if request.param else home
    if request.param:
        monkeypatch.setenv("AGENT_HOME", str(state_home))
    else:
        monkeypatch.delenv("AGENT_HOME", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(PLUGIN_ROOT))

    yield state_home

    assert {key: os.environ.get(key) for key in credential_env} == credential_env
    if request.param:
        after = {
            path.relative_to(home): path.read_bytes()
            for path in home.rglob("*") if path.is_file()
        }
        assert after == before, "registration must not write into the credential home"


def test_registry_write_honors_registration_home(registration_home, tmp_path):
    repos.add_repo(
        "example",
        str(tmp_path / "repo"),
        repo_class="worktree",
        remote="https://github.com/example/repo.git",
        default_branch="main",
    )
    installer.register_project("example")

    root = registration_home / ".agent-worktrees"
    data = yaml.safe_load((root / "repos.yaml").read_text(encoding="utf-8"))
    assert data["repos"]["example"]["class"] == "worktree"
    assert repos.read_registry().repos["example"].default_branch == "main"
    projects = yaml.safe_load((root / "projects.yaml").read_text(encoding="utf-8"))
    assert "example" in projects["projects"]


def test_binstub_write_honors_registration_home(registration_home, tmp_path):
    assert installer.deploy_binstubs(tmp_path, "example")

    local_bin = registration_home / ".local" / "bin"
    project_stubs = list(local_bin.glob("example*"))
    assert project_stubs
    for path in project_stubs:
        assert "--project" in path.read_text(encoding="utf-8")
    assert list(local_bin.glob("agent-worktrees*"))
    receipt = json.loads(
        (registration_home / ".agent-worktrees" / "binstub-receipts" / "example.json")
        .read_text(encoding="utf-8")
    )
    assert receipt["owner"]["payload_root"] == PLUGIN_ROOT.as_posix()
    assert set(receipt["stubs"]) == {path.name for path in project_stubs}


def test_cli_entry_honors_registration_home(registration_home, tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin",
         "https://github.com/example/repo.git"],
        check=True,
    )
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "register-project-entry",
         "example", "--repo-dir", str(repo), "--no-expose-agent"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    root = registration_home / ".agent-worktrees"
    registered = yaml.safe_load((root / "repos.yaml").read_text(encoding="utf-8"))
    assert registered["repos"]["example"]["remote"] == "https://github.com/example/repo.git"
    projects = yaml.safe_load((root / "projects.yaml").read_text(encoding="utf-8"))
    assert projects["projects"]["example"]["expose_agent"] is False
    assert (root / "binstub-receipts" / "example.json").is_file()
    assert list((registration_home / ".local" / "bin").glob("example*"))
