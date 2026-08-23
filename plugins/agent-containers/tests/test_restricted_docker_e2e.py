"""Opt-in real-Docker smoke test for the restricted transport boundary."""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest

from agent_containers.config import FleetConfig
from agent_containers.fleet import _image_run
from agent_containers.lifecycle import (
    DockerContainerInfo,
    inspect_state,
    restricted_policy_errors,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_CONTAINERS_E2E") != "1",
    reason="set AGENT_CONTAINERS_E2E=1 to run real-Docker smoke",
)


def test_restricted_container_is_writable_only_on_bounded_tmpfs():
    name = f"agent-containers-e2e-{uuid.uuid4().hex[:8]}"
    fleet = FleetConfig(
        image="node:22-bookworm-slim",
        security_profile="restricted",
        exec_user="node",
        workspace_folder="/workspace",
        acp_command="cat",
        network="none",
        memory="1g",
        cpus=1,
        pids_limit=64,
        workspace_size="128m",
        home_size="64m",
    )
    try:
        _image_run(
            "restricted-e2e",
            fleet,
            name,
            workspace_folder="/workspace",
            exec_user="node",
        )
        assert inspect_state(name) == "running"

        info = DockerContainerInfo(
            name=name,
            container_id=name,
            image=fleet.image,
            state="running",
            status="Up",
            fleet="restricted-e2e",
            security_profile="restricted",
            security_policy=fleet.security_policy_fingerprint("/workspace", "node"),
            security_image_id=None,
        )
        assert restricted_policy_errors(
            info,
            fleet,
            workspace_folder="/workspace",
            exec_user="node",
        ) == []

        writable = subprocess.run(
            [
                "docker", "exec", "-u", "node", name, "bash", "-lc",
                'touch /workspace/work && touch "$HOME/home" && '
                'test -w /workspace && test -w "$HOME"',
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert writable.returncode == 0, writable.stderr

        executable = subprocess.run(
            [
                "docker", "exec", "-u", "node", name, "bash", "-lc",
                "cp /bin/true /workspace/tool && chmod 700 /workspace/tool "
                "&& /workspace/tool",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert executable.returncode == 0, executable.stderr

        rootfs = subprocess.run(
            [
                "docker", "exec", "-u", "node", name,
                "bash", "-lc", "touch /etc/escape",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert rootfs.returncode != 0
        assert "Read-only file system" in rootfs.stderr
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            timeout=30,
        )
