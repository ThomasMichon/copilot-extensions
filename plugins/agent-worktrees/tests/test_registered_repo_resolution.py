"""Authoritative repository resolution refuses fallback and malformed state."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agent_procutil import no_window_kwargs
from agent_worktrees import repos, repos_cli


@pytest.fixture
def registry_path(tmp_path, monkeypatch):
    path = tmp_path / "repos.yaml"
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: path)
    monkeypatch.setattr(repos, "_current_platform", lambda: "linux")
    return path


def test_resolves_only_registered_platform_root(registry_path, tmp_path):
    root = tmp_path / "canonical"
    root.mkdir()
    repos.write_registry(repos.ReposRegistry(repos={
        "tools": repos.RepoEntry("tools", repo_class="worktree", paths={"linux": str(root)}),
    }))
    entry, resolved = repos.resolve_registered_repo("tools")
    assert entry.name == "tools"
    assert resolved == root


def test_srcroot_candidate_is_not_registration(registry_path, tmp_path):
    (tmp_path / "tools").mkdir()
    repos.write_registry(repos.ReposRegistry(srcroot={"linux": str(tmp_path)}))
    with pytest.raises(repos.RepositoryResolutionError, match="not registered"):
        repos.resolve_registered_repo("tools")


@pytest.mark.parametrize("body,reason", [
    ("repos: [invalid]", "must be a mapping"),
    ("repos:\n  tools: null\n", "must be a mapping"),
    ("repos:\n  tools: {linux: 123}\n", "invalid linux path"),
    ("repos:\n  tools: {class: typo}\n", "invalid class"),
    ("repos:\n  tools: {remote: []}\n", "invalid remote"),
    ("repos:\n  tools: {remote: null}\n", "invalid remote"),
    ("repos:\n  tools: {remote: 123}\n", "invalid remote"),
    ("repos:\n  tools: {}\n  tools: {}\n", "duplicate"),
    ("repos: [", "could not be read"),
    ("[not, a, mapping]", "must be a mapping"),
])
def test_invalid_registry_cannot_be_successful_resolution(registry_path, body, reason):
    registry_path.write_text(body, encoding="utf-8")
    with pytest.raises(repos.RepositoryResolutionError, match=reason):
        repos.resolve_registered_repo("tools")


def test_missing_registered_checkout_names_acquisition_remedy(registry_path, tmp_path):
    repos.write_registry(repos.ReposRegistry(repos={
        "tools": repos.RepoEntry("tools", paths={"linux": str(tmp_path / "missing")}),
    }))
    with pytest.raises(repos.RepositoryResolutionError, match="clone/adoption"):
        repos.resolve_registered_repo("tools")


def test_invalid_registered_path_is_an_explicit_resolution_error(registry_path, tmp_path):
    raw = str(tmp_path) + "\0bad"
    registry_path.write_text(
        f"repos:\n  tools:\n    linux: {json.dumps(raw)}\n", encoding="utf-8",
    )
    with pytest.raises(repos.RepositoryResolutionError, match="unavailable"):
        repos.resolve_registered_repo("tools")


def test_registered_only_cli_reports_explicit_capability(registry_path, tmp_path, capfd):
    repos.write_registry(repos.ReposRegistry(repos={
        "tools": repos.RepoEntry("tools", paths={"linux": str(tmp_path)}),
    }))
    assert repos_cli.cmd_repos_dispatch(["find", "tools", "--registered-only", "--json"]) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["registration"] == "ready"
    assert result["path"] == str(tmp_path)
    assert result["class"] == "reference"


@pytest.mark.parametrize("remote", [[], None, 123])
def test_registered_only_cli_refuses_malformed_remote(registry_path, tmp_path, capfd, remote):
    registry_path.write_text(
        "repos:\n  tools:\n    class: worktree\n"
        f"    linux: {json.dumps(str(tmp_path))}\n    remote: {json.dumps(remote)}\n",
        encoding="utf-8",
    )
    before = registry_path.read_bytes()
    assert repos_cli.cmd_repos_dispatch(["find", "tools", "--registered-only", "--json"]) == 1
    result = json.loads(capfd.readouterr().out)
    assert "invalid remote" in result["error"]
    assert result.get("registration") != "ready"
    assert registry_path.read_bytes() == before


def test_registered_only_native_cli_first_touch(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "tools"
    root.mkdir()
    registry = home / ".agent-worktrees" / "repos.yaml"
    registry.parent.mkdir(parents=True)
    platform = repos._current_platform()
    registry.write_text(
        "repos:\n  tools:\n    class: worktree\n"
        f"    {platform}: {json.dumps(str(root))}\n",
        encoding="utf-8",
    )
    before = registry.read_bytes()
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "repos", "find", "tools",
         "--registered-only", "--json"],
        cwd=tmp_path,
        env={**os.environ, "AGENT_HOME": str(home)},
        capture_output=True, text=True, check=False, timeout=20,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["registration"] == "ready"
    assert payload["name"] == "tools"
    assert payload["class"] == "worktree"
    assert payload["path"] == str(root)
    assert registry.read_bytes() == before


def test_registry_git_probe_uses_canonical_headless_options(monkeypatch):
    options = {"creationflags": 123}
    calls = []
    monkeypatch.setattr(repos, "no_window_kwargs", lambda: options)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "main\n", "")

    monkeypatch.setattr(repos.subprocess, "run", run)
    assert repos._git("fixture", "branch", "--show-current").stdout == "main\n"
    assert calls == [(
        ["git", "-C", "fixture", "branch", "--show-current"],
        {"capture_output": True, "text": True, "timeout": 60, **options},
    )]
