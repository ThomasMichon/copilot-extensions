"""Trusted-container launch-auth shim tests."""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from agent_containers import container_shims


def test_git_credential_environment_is_launch_scoped_and_authoritative():
    assert container_shims.git_credential_environment() == {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "/usr/local/bin/ado-auth-helper",
        "GIT_TERMINAL_PROMPT": "0",
    }


def test_relay_client_serves_github_from_launch_token(tmp_path):
    client = tmp_path / "credential-relay-client.py"
    client.write_text(container_shims.RELAY_CLIENT, encoding="utf-8")
    token = "test-" + "github-token"
    result = subprocess.run(
        [sys.executable, str(client), "ado", "get"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        env={**os.environ, "GH_TOKEN": token},
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert fields["username"] == "x-access-token"
    assert fields["password"] == token


def test_deploy_includes_git_credential_helper_by_default(monkeypatch):
    written = []
    monkeypatch.setattr(
        container_shims,
        "_docker_write",
        lambda container, path, content, mode="755": written.append(path),
    )

    container_shims.deploy("repo-1")

    assert container_shims.RELAY_CLIENT_PATH in written
    assert container_shims.AZURE_HELPER_PATH in written
    assert container_shims.ADO_HELPER_PATH in written


def test_ensure_agent_worktrees_is_noop_when_already_ready(monkeypatch):
    monkeypatch.setattr(container_shims, "_agent_worktrees_ready", lambda *a, **k: True)
    monkeypatch.setattr(
        container_shims, "_agent_worktrees_payload_ready", lambda *a, **k: True
    )
    monkeypatch.setattr(container_shims, "_container_home", lambda *a, **k: "/home/vscode")
    monkeypatch.setattr(
        container_shims,
        "_agent_worktrees_payload_paths",
        lambda home: ("/home/vscode/.agent-worktrees/payload-src", "/payload-root"),
    )
    monkeypatch.setattr(
        container_shims,
        "_deploy_agent_worktrees_wrapper",
        lambda container, payload_root=None: None,
    )

    container_shims.ensure_agent_worktrees("repo-1", user="vscode")


def test_ensure_agent_worktrees_persists_local_payload_and_external_uv_sources(
    monkeypatch, tmp_path
):
    repo_root = tmp_path / "repo"
    payload_root = repo_root / "plugins" / "agent-worktrees"
    libs_root = repo_root / "libs"
    (payload_root / "scripts").mkdir(parents=True)
    (payload_root / "scripts" / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (payload_root / "pyproject.toml").write_text(
        """
[project]
name = "agent-worktrees"

[tool.uv.sources]
agent-lazy-cli-dispatch = { path = "../../libs/lazy-cli-dispatch", editable = true }
agent-procutil = { path = "libs/agent-procutil" }
""".strip(),
        encoding="utf-8",
    )
    (payload_root / "libs" / "agent-procutil").mkdir(parents=True)
    (libs_root / "lazy-cli-dispatch" / "src").mkdir(parents=True)

    ready = iter([False, True])
    exec_calls: list[tuple[str, str]] = []
    cp_calls: list[tuple[Path, str]] = []
    wrappers: list[tuple[str, str | None]] = []

    monkeypatch.setattr(container_shims, "_agent_worktrees_payload_root", lambda: payload_root)
    monkeypatch.setattr(container_shims, "_agent_worktrees_ready", lambda *a, **k: next(ready))
    monkeypatch.setattr(container_shims, "_container_home", lambda *a, **k: "/home/vscode")
    monkeypatch.setattr(container_shims, "_docker_exists", lambda *a, **k: False)
    monkeypatch.setattr(container_shims, "_host_uv_index", lambda: "https://feed/simple/")
    monkeypatch.setattr(
        container_shims,
        "_docker_exec",
        lambda container, command, *, user, env=None, timeout=30.0: (
            exec_calls.append((user, command))
            or types.SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(
        container_shims,
        "_docker_cp",
        lambda source, container, target_dir, *, timeout=120.0: cp_calls.append(
            (source, target_dir)
        ),
    )
    monkeypatch.setattr(
        container_shims,
        "_deploy_agent_worktrees_wrapper",
        lambda container, payload_root=None: wrappers.append((container, payload_root)),
    )

    container_shims.ensure_agent_worktrees("repo-1", user="vscode")

    assert wrappers == [
        ("repo-1", "/home/vscode/.agent-worktrees/payload-src/plugins/agent-worktrees")
    ]
    payload_targets = {source: target_dir for source, target_dir in cp_calls}
    assert payload_targets[payload_root] == "/home/vscode/.agent-worktrees/payload-src/plugins"
    assert payload_targets[libs_root] == "/home/vscode/.agent-worktrees/payload-src"
    install = next(
        command for user, command in exec_calls if "bash scripts/install.sh provision" in command
    )
    assert "cd '/home/vscode/.agent-worktrees/payload-src/plugins/agent-worktrees'" in install
    assert "--install-dir '/home/vscode/.agent-worktrees'" in install
    assert any(
        command.startswith("rm -rf '/home/vscode/.agent-worktrees/payload-src'")
        for _, command in exec_calls
    )
    assert any(".payload-sync-complete" in command for _, command in exec_calls)


def test_ensure_agent_worktrees_surfaces_install_failures(monkeypatch):
    exec_calls = []

    monkeypatch.setattr(
        container_shims,
        "_agent_worktrees_payload_root",
        lambda: Path("/host/plugins/agent-worktrees"),
    )
    monkeypatch.setattr(
        container_shims,
        "_agent_worktrees_copy_sources",
        lambda payload_root: (Path("/host"), [Path("/host/plugins/agent-worktrees")]),
    )
    monkeypatch.setattr(container_shims, "_agent_worktrees_ready", lambda *a, **k: False)
    monkeypatch.setattr(container_shims, "_container_home", lambda *a, **k: "/home/vscode")
    monkeypatch.setattr(container_shims, "_docker_exists", lambda *a, **k: False)
    monkeypatch.setattr(container_shims, "_docker_cp", lambda *a, **k: None)
    monkeypatch.setattr(container_shims, "_deploy_agent_worktrees_wrapper", lambda *a, **k: None)

    monkeypatch.setattr(container_shims, "_host_uv_index", lambda: "https://feed/simple/")

    def fake_exec(container, command, *, user, env=None, timeout=30.0):
        exec_calls.append(command)
        if "bash scripts/install.sh provision" in command:
            return types.SimpleNamespace(returncode=23, stdout="", stderr="network down")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(container_shims, "_docker_exec", fake_exec)

    with pytest.raises(RuntimeError, match="could not provision agent-worktrees"):
        container_shims.ensure_agent_worktrees("repo-1", user="vscode")

    assert any(
        command.startswith("rm -rf '/home/vscode/.agent-worktrees/payload-src'")
        for command in exec_calls
    )


def test_ensure_agent_worktrees_repairs_old_ready_install_without_reinstall(monkeypatch):
    ready = iter([True, True])
    exec_calls: list[str] = []
    wrappers: list[tuple[str, str | None]] = []

    monkeypatch.setattr(container_shims, "_agent_worktrees_ready", lambda *a, **k: next(ready))
    monkeypatch.setattr(container_shims, "_agent_worktrees_payload_ready", lambda *a, **k: False)
    monkeypatch.setattr(container_shims, "_container_home", lambda *a, **k: "/home/vscode")
    monkeypatch.setattr(container_shims, "_docker_exists", lambda *a, **k: True)
    monkeypatch.setattr(
        container_shims,
        "_agent_worktrees_payload_paths",
        lambda home: ("/home/vscode/.agent-worktrees/payload-src", "/payload-root"),
    )
    monkeypatch.setattr(
        container_shims,
        "_sync_agent_worktrees_payload",
        lambda *a, **k: "/payload-root",
    )
    monkeypatch.setattr(
        container_shims,
        "_deploy_agent_worktrees_wrapper",
        lambda container, payload_root=None: wrappers.append((container, payload_root)),
    )
    monkeypatch.setattr(
        container_shims,
        "_docker_exec",
        lambda container, command, *, user, env=None, timeout=30.0: (
            exec_calls.append(command)
            or types.SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )

    container_shims.ensure_agent_worktrees("repo-1", user="vscode")

    assert wrappers == [("repo-1", "/payload-root")]
    assert not any("bash scripts/install.sh provision" in command for command in exec_calls)


def test_ensure_agent_worktrees_workspace_registered_runs_idempotent_register(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def fake_exec(container, command, *, user, env=None, timeout=30.0):
        calls.append((container, user, command))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(container_shims, "_docker_exec", fake_exec)

    container_shims.ensure_agent_worktrees_workspace_registered(
        "repo-1",
        user="vscode",
        workspace_folder="/workspaces/example-web",
    )

    assert calls == [
        (
            "repo-1",
            "vscode",
            "set -euo pipefail; cd '/workspaces/example-web'; "
            "'/usr/local/bin/agent-worktrees' register 'example-web' --repo-dir '/workspaces/example-web'",
        )
    ]
