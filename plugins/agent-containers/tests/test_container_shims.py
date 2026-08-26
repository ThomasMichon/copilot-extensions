"""Trusted-container launch-auth shim tests."""

from __future__ import annotations

import os
import subprocess
import sys

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
    fields = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
        if "=" in line
    )
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
