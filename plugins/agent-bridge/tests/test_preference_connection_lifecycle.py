"""An attested refusal uses the actual guarded connection rollback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_bridge.connect import ConnectError, ConnectTracker
from agent_bridge.db import Database
from agent_bridge.transport import SpawnTarget
from agent_bridge.preference_attestation import LAUNCH_TOKEN, target_digest
from agent_bridge.container_preference_launch import container_child_argv
from agent_bridge.session_manager import SessionManager
from agent_bridge.session_preferences import SOURCE_ENV


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "target", "purpose"])
async def test_attested_refusal_rolls_back_before_session_creation(monkeypatch, tmp_path, failure):
    db = Database(tmp_path / "sessions.db")
    manager = SessionManager(db, session_host_state_dir=str(tmp_path / "hosts"))
    target = SpawnTarget(
        type="command", cwd="/workspace", env={SOURCE_ENV: "target-settings"},
        venue={"instance_id": "instance"},
    )
    command = container_child_argv(
        {"name": "example", "user": "runner"},
        {
            "name": "example", "user": "runner", "execution_instance": "instance",
            "preference_wrapper": {"version": 1, "launcher": LAUNCH_TOKEN},
            "acp_command": f"{LAUNCH_TOKEN} copilot --acp --stdio",
        }, [],
    )
    receipt = {
        "version": 2,
        "authority": {
            "mode": "defaults",
            "target": target_digest({"user": "runner", "instance": "instance"}),
        },
    }
    if failure == "missing":
        receipt = None
    elif failure == "target":
        receipt["authority"]["target"] = "0" * 64
    else:
        receipt["authority"]["mode"] = "selection"
    sock = SimpleNamespace(
        attach=AsyncMock(return_value=SimpleNamespace(preference_receipt=receipt)),
        terminate=AsyncMock(), close=AsyncMock(),
    )
    streams = SimpleNamespace(
        aclose=AsyncMock(), reader=object(), writer=object(),
        on_transport_lost=None, on_child_exit=None,
    )
    spawned = SimpleNamespace(
        nonce="fixture", local_port=1, child_pid=123, boundary="container",
        forward=object(), relay=[object()], aclose=AsyncMock(),
    )
    spawner = SimpleNamespace(
        boundary="container", spawn=AsyncMock(return_value=spawned),
        abort_spawned=AsyncMock(return_value=True),
    )
    acp_client = SimpleNamespace(
        mark_transport_lost=lambda *args: None, mark_host_child_exited=lambda *args: None,
        start_streams=AsyncMock(), new_session=AsyncMock(), load_session=AsyncMock(),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.client.SessionHostClient.connect",
        AsyncMock(return_value=sock),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.acp_adapter.open_acp_streams",
        AsyncMock(return_value=streams),
    )
    monkeypatch.setattr("agent_bridge.session_manager.AcpClient", MagicMock(return_value=acp_client))
    result = {}
    try:
        with pytest.raises(ConnectError):
            await manager._connect_via_session_host(
                target, tracker=ConnectTracker(lambda *args: None, session_id="new"),
                session_id="new", on_acp_event=lambda *args: None,
                permission_callback=None, spawner=spawner, remote_child_argv=command,
                remote_cwd="/workspace", parity_fault_result=result,
            )
        acp_client.start_streams.assert_not_awaited()
        acp_client.new_session.assert_not_awaited()
        acp_client.load_session.assert_not_awaited()
        sock.terminate.assert_awaited_once()
        streams.aclose.assert_awaited_once()
        sock.close.assert_awaited_once()
        spawner.abort_spawned.assert_awaited_once_with(spawned, "new")
        spawned.aclose.assert_awaited_once()
        assert "new" not in manager._forwards and "new" not in manager._relays
        assert result["host_process_removed"] and result["remote_authority_removed"]
    finally:
        db.close()
