"""Carry the selected immutable venue before provider preparation."""

import json
from unittest.mock import AsyncMock

import pytest

from agent_bridge.session_host import container_transport as t


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr("agent_bridge.transport._reresolve_stale_interpreter", lambda argv: argv)
    runner = AsyncMock(return_value=(0, json.dumps({
        "name": "example", "ssh": {"host_alias": "example-pinned"},
        "remote_command": "command", "execution_instance": "instance",
    }).encode(), b""))
    monkeypatch.setattr(t, "_run_provider", runner)
    return runner


@pytest.mark.asyncio
async def test_selected_instance_is_sent_before_provider_side_effects(provider):
    result = await t.prepare_container_session_host({
        "name": "example", "instance_id": "instance", "provider_command": ["provider"],
    }, None)
    assert result["execution_instance"] == "instance"
    assert provider.call_args.args[0] == [
        "provider", "session-host-prepare", "example", "--expected-instance", "instance",
    ]


@pytest.mark.asyncio
async def test_unbound_attested_request_never_calls_provider(provider):
    with pytest.raises(RuntimeError, match="selected execution instance"):
        await t.prepare_container_session_host({
            "name": "example", "provider_command": ["provider"],
            "acp_command": "{{target_preference_launcher}} copilot",
        }, None)
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_cannot_return_a_replacement_instance(provider):
    with pytest.raises(RuntimeError, match="incomplete data"):
        await t.prepare_container_session_host({
            "name": "example", "instance_id": "different", "provider_command": ["provider"],
        }, None)


@pytest.mark.asyncio
async def test_cleanup_is_also_bound_to_prepared_instance(provider):
    assert await t.cleanup_container_session_host(
        {"name": "example", "provider_command": ["provider"]},
        {"remote_env": "/home/your_user/.agent-containers/launch/example.env",
         "execution_instance": "instance"},
    )
    assert provider.call_args.args[0][-2:] == ["--expected-instance", "instance"]
