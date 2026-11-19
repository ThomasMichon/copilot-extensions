"""Driver-exec container members answer the portable receiving-policy check (#13366)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from repository_policy import AgentHostingError

from agent_bridge.provider_hosting import query_provider_policy
from agent_bridge.transport import SpawnTarget

INSTANCE = "0123456789ab"
PROVIDER = ["/payload/bin/agent-containers"]
ALLOWED = {
    "version": 1, "allowed": True, "repository": "/workspaces/example",
    "expose_agent": None, "sources": [], "denied_by": [],
}


def _target(**venue: Any) -> SpawnTarget:
    # The venue agent-containers resolves for a driver-exec member: no container block.
    return SpawnTarget(
        type="command", user="vscode", cwd=".",
        spawn_command=[*PROVIDER, "exec", "--stdio", "example-1"],
        venue={
            "provider": "agent-containers", "target_id": "container:example-1",
            "instance_id": INSTANCE, "fleet": "example", "driver": "example-driver",
            "workspace_folder": "/workspaces/example", "transport": "driver-exec",
            "capabilities": {"session_host": False}, "provider_command": PROVIDER,
            **venue,
        },
    )


Call = tuple[list[str], dict[str, Any]]


def _fake_provider(
    monkeypatch: pytest.MonkeyPatch, calls: list[Call], exec_result: tuple[int, bytes, bytes],
) -> None:
    async def run(argv: list[str], **kwargs: Any) -> tuple[int, bytes, bytes]:
        calls.append((argv, kwargs))
        assert argv[:4] == [*PROVIDER, "runtime-proxy", "--fleet", "example"]
        if argv[4] == "inspect":
            assert argv[5:] == [INSTANCE]
            return 0, json.dumps([{"Id": INSTANCE, "State": {"Running": True}}]).encode(), b""
        return exec_result

    monkeypatch.setattr("agent_bridge.session_host.container_transport._run_provider", run)


@pytest.mark.asyncio
async def test_venue_declaration_runs_portable_check_without_guest_worktrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Call] = []
    _fake_provider(monkeypatch, calls, (0, json.dumps(ALLOWED).encode(), b""))

    result = await query_provider_policy(
        _target(portable_receiving_policy={"version": 1}), operation="launch",
    )

    assert result["allowed"] is True
    assert [argv[4] for argv, _ in calls] == ["inspect", "exec"]
    argv, kwargs = calls[1]
    assert argv[4:11] == ["exec", "-i", "-u", "vscode", INSTANCE, "bash", "-lc"]
    assert "cd -- /workspaces/example" in argv[-1] and "python3 -I -S" in argv[-1]
    assert kwargs["input_bytes"]
    assert not any("agent-worktrees" in " ".join(argv) for argv, _ in calls)


@pytest.mark.asyncio
async def test_undeclared_driver_venue_keeps_legacy_guest_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Call] = []
    _fake_provider(monkeypatch, calls, (1, b"", b""))

    with pytest.raises(AgentHostingError, match="no portable receiving handoff"):
        await query_provider_policy(_target(), operation="launch")
    argv, kwargs = calls[-1]
    assert "agent-policy check" in argv[-1] and "input_bytes" not in kwargs
