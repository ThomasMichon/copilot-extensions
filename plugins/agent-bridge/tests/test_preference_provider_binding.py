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
        {"remote_env": "/home/your_user/.agent-containers/launch/" + "0" * 32 + ".env",
         "execution_instance": "instance", "user": "runner"},
    )
    assert provider.call_args.args[0][-4:] == [
        "--expected-instance", "instance", "--expected-user", "runner",
    ]


def test_session_load_migrates_legacy_venue_id_without_changing_policy():
    from agent_bridge.session_manager import Session
    from agent_bridge.transport import SpawnTarget

    target = SpawnTarget(
        type="command", container={"name": "example", "user": "runner"},
        venue={"instance_id": "saved-instance"},
        env={"AGENT_BRIDGE_PREFERENCE_SOURCE": "target-settings"},
    )
    session = Session("saved", "saved", target)
    assert session.target.container["instance_id"] == "saved-instance"
    assert session.target.env["AGENT_BRIDGE_PREFERENCE_SOURCE"] == "target-settings"
    assert session.target.venue["instance_id"] == "saved-instance"


def test_session_load_refuses_conflicting_selected_identity():
    from agent_bridge.session_manager import Session
    from agent_bridge.transport import SpawnTarget

    with pytest.raises(ValueError, match="selected instances disagree"):
        Session("saved", "saved", SpawnTarget(
            type="command", container={"instance_id": "other"},
            venue={"instance_id": "saved"},
        ))
