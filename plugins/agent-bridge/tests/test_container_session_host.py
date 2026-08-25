"""Trusted-container Session Host transport tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_bridge.session_host.container_transport import (
    ContainerTransport,
    build_container_spawner,
)


class _FakeManager:
    def __init__(self):
        self.connected = []
        self.commands = []

    async def ensure_connected(self, name, source, forwards):
        self.connected.append((name, source.get_ssh_config(), forwards))

    async def exec_command(
        self,
        name,
        command,
        *,
        timeout=60.0,
        input_bytes=None,
    ):
        self.commands.append((name, command, timeout, input_bytes))
        return SimpleNamespace(exit_code=0, stdout="", stderr="")


@pytest.mark.asyncio
async def test_container_transport_stages_bundle_over_ssh_stdin(tmp_path):
    bundle = tmp_path / "session-host.pyz"
    bundle.write_bytes(b"\x00session-host-bundle\xff")
    manager = _FakeManager()
    transport = ContainerTransport(
        "odsp-web-1",
        {
            "host_alias": "agent-container-odsp-web-1",
            "user": "vscode",
            "identity_file": "C:/keys/id_ed25519",
            "config_file": "C:/ssh/odsp-web-1.config",
        },
        state_command=["agent-containers", "session-host-state", "odsp-web-1"],
        reverse_forwards=["9857:127.0.0.1:61234"],
        manager=manager,
    )

    await transport.push_file(
        str(bundle),
        "/tmp/agent-bridge/session-host.pyz",
    )

    assert manager.connected[0][0] == "container:odsp-web-1"
    _name, command, _timeout, payload = manager.commands[0]
    assert "cat >" in command
    assert "mv -f" in command
    assert payload == b"\x00session-host-bundle\xff"
    assert transport.reverse_forwards() == ["9857:127.0.0.1:61234"]
    assert transport.endpoint_extra()["container"] == "odsp-web-1"


def test_build_container_spawner_uses_generic_remote_spawner():
    target = {
        "name": "odsp-web-1",
        "ssh": {"host_alias": "agent-container-odsp-web-1"},
        "provider_command": ["python", "-m", "agent_containers"],
    }
    spawner = build_container_spawner(
        target,
        prepared={
            **target,
            "state_command": [
                "python", "-m", "agent_containers",
                "session-host-state", "odsp-web-1",
            ],
            "reverse_forwards": ["9857:127.0.0.1:61234"],
        },
    )

    assert spawner.boundary == "container"
    assert spawner._transport.reverse_forwards() == [
        "9857:127.0.0.1:61234",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("running", "container_id", "expected"),
    [
        (True, "a1b2c3d4e5f6000011112222", True),
        (False, "a1b2c3d4e5f6000011112222", False),
        (True, "ffffffffffff000011112222", False),
    ],
)
async def test_container_state_requires_running_matching_identity(
    monkeypatch,
    running,
    container_id,
    expected,
):
    async def fake_provider(command, *, timeout):
        return 0, (
            b'{"name":"odsp-web-1","state":"running",'
            b'"running":' + (b"true" if running else b"false") + b','
            b'"container_id":"' + container_id.encode() + b'"}'
        ), b""

    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport._run_provider",
        fake_provider,
    )
    transport = ContainerTransport(
        "odsp-web-1",
        {"host_alias": "agent-container-odsp-web-1-a1b2c3d4e5f6"},
        state_command=["agent-containers", "session-host-state", "odsp-web-1"],
    )

    assert await transport.is_running() is expected
