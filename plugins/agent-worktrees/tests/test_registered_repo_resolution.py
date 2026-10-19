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


@pytest.mark.guard
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
    inventory = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "repos", "list", "--strict", "--json"],
        cwd=tmp_path, env={**os.environ, "AGENT_HOME": str(home)},
        capture_output=True, text=True, check=False, timeout=20, **no_window_kwargs(),
    )
    assert inventory.returncode == 0, inventory.stderr
    listed = json.loads(inventory.stdout)
    assert listed["inventory_authority"] == "complete"
    assert [entry["name"] for entry in listed["repos"]] == ["tools"]
    assert registry.read_bytes() == before


@pytest.mark.guard
@pytest.mark.parametrize("body", [
    "repos: [", "repos: [invalid]", "repos:\n  invalid: null\n",
    "repos:\n  duplicate: {}\n  duplicate: {}\n",
])
def test_strict_inventory_refuses_corrupt_or_dropped_entries(registry_path, capfd, body):
    registry_path.write_text(body, encoding="utf-8")
    before = registry_path.read_bytes()
    assert repos_cli.cmd_repos_dispatch(["list", "--strict", "--json"]) == 2
    failure = json.loads(capfd.readouterr().out)
    assert failure["error"] and "inventory_authority" not in failure
    assert registry_path.read_bytes() == before
    with pytest.raises(repos.RepositoryResolutionError):
        repos.list_repos(strict=True)


@pytest.mark.guard
def test_strict_inventory_missing_is_complete_but_legacy_json_is_not_an_attestation(registry_path, capfd):
    assert repos_cli.cmd_repos_dispatch(["list", "--strict", "--json"]) == 0
    assert json.loads(capfd.readouterr().out) == {
        "version": 1, "platform": "linux", "repos": [], "inventory_authority": "complete",
    }
    assert not registry_path.exists()
    assert repos_cli.cmd_repos_dispatch(["list", "--json"]) == 0
    assert json.loads(capfd.readouterr().out) == {
        "version": 1, "platform": "linux", "repos": [],
    }


@pytest.mark.guard
def test_strict_inventory_refuses_nonregular_registry(registry_path):
    registry_path.mkdir()
    with pytest.raises(repos.RepositoryResolutionError, match="regular file"):
        repos.list_repos(strict=True)


@pytest.mark.guard
def test_strict_inventory_dangling_link_is_unavailable_not_absent(registry_path, tmp_path, capfd):
    missing = tmp_path / "missing-registry.yaml"
    try:
        registry_path.symlink_to(missing)
    except OSError as exc:
        pytest.skip(f"native symlink creation unavailable: {exc}")
    with pytest.raises(repos.RepositoryResolutionError, match="could not be read"):
        repos.list_repos(strict=True)
    assert repos_cli.cmd_repos_dispatch(["list", "--strict", "--json"]) == 2
    failure = json.loads(capfd.readouterr().out)
    assert failure["error"] and "inventory_authority" not in failure
    assert registry_path.is_symlink() and not missing.exists()


@pytest.mark.guard
@pytest.mark.parametrize("ancestor", ["absent", "dangling", "file", "loop"])
def test_native_strict_inventory_distinguishes_missing_and_unavailable_ancestors(tmp_path, ancestor):
    home = tmp_path / "home"
    home.mkdir()
    storage = home / ".agent-worktrees"
    missing = tmp_path / "missing-storage"
    if ancestor == "file":
        storage.write_text("not a directory", encoding="utf-8")
    elif ancestor in {"dangling", "loop"}:
        storage.symlink_to(storage if ancestor == "loop" else missing, target_is_directory=True)
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "repos", "list", "--strict", "--json"],
        cwd=tmp_path, env={**os.environ, "AGENT_HOME": str(home)},
        capture_output=True, text=True, check=False, timeout=20, **no_window_kwargs(),
    )
    payload = json.loads(result.stdout)
    if ancestor == "absent":
        assert result.returncode == 0, result.stderr
        assert payload == {
            "version": 1, "platform": repos._current_platform(),
            "repos": [], "inventory_authority": "complete",
        }
        assert not storage.exists()
    else:
        assert result.returncode == 2, result.stderr
        assert payload["error"] and "inventory_authority" not in payload
        assert storage.is_symlink() if ancestor != "file" else storage.read_text() == "not a directory"
    assert not missing.exists()


@pytest.mark.guard
def test_registry_git_probe_uses_canonical_headless_options(monkeypatch):
    options = {"creationflags": 123}
    calls = []
    monkeypatch.setattr(repos.git_ops, "no_window_kwargs", lambda: options)
    selectors = (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
    )
    for name in selectors:
        monkeypatch.setenv(name, "unrelated")
    monkeypatch.setenv("REGISTRY_PROBE_SENTINEL", "retained")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "main\n", "")

    monkeypatch.setattr(repos.git_ops.subprocess, "run", run)
    assert repos._git("fixture", "branch", "--show-current").stdout == "main\n"
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == ["git", "-C", "fixture", "branch", "--show-current"]
    assert {key: value for key, value in kwargs.items() if key != "env"} == {
        "cwd": None, "capture_output": True, "text": True,
        "encoding": "utf-8", "errors": "replace", "timeout": 60, **options,
    }
    env = kwargs["env"]
    assert set(env).isdisjoint(selectors)
    assert env["REGISTRY_PROBE_SENTINEL"] == "retained"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
