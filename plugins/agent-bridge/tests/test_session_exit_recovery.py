"""Exit settlement and foreground recovery share lifecycle custody."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, Mock

import pytest

from agent_bridge import hosting_policy
from agent_bridge.models import SessionStatus
from agent_bridge.native_send_admission import NativeSendPrompt, submit
from agent_bridge.session_host.host_index import HostIndex, HostRecord
from agent_bridge.session_manager import SessionManager
from test_native_bound_prompt import prepare


@pytest.mark.asyncio
async def test_keyed_send_waits_for_old_client_exit_settlement(
    session_manager, tmp_db, mock_acp_client, monkeypatch,
):
    manager = session_manager
    session = prepare(manager, tmp_db, mock_acp_client)
    old = session.client
    old.is_running = False
    old.host_child_exit_code = 7
    rec = HostRecord(session_id=session.session_id, port=1234, host_pid=os.getpid(), child_pid=222)
    manager._host_index.register(rec)
    entered, release = asyncio.Event(), asyncio.Event()

    async def shutdown():
        entered.set()
        await release.wait()

    old.shutdown.side_effect = shutdown
    replacement = Mock(is_running=True, host_child_exit_code=None)
    new_rec = HostRecord(
        session_id=session.session_id, port=4321, host_pid=os.getpid(), child_pid=333,
    )

    def reap(captured, _reason, *, remove_index):
        assert captured == rec
        assert session.client is old
        assert remove_index is False

    async def reattach(retained):
        assert retained is session
        assert old.shutdown.await_count == 1
        assert retained.client is None
        retained.client = replacement
        retained.status = SessionStatus.IDLE
        manager._host_index.register(new_rec)
        tmp_db.update_session_status(session.session_id, "idle", 2)
        return True

    monkeypatch.setattr(manager, "_reap_host_record", Mock(side_effect=reap))
    monkeypatch.setattr(hosting_policy, "require_session_hosting", AsyncMock())
    manager._try_reattach_live_host = AsyncMock(side_effect=reattach)
    manager.resume_session = SessionManager.resume_session.__get__(manager)
    cleanup = asyncio.create_task(manager.recover_disconnected_hosts())
    await asyncio.wait_for(entered.wait(), 2)
    request = NativeSendPrompt(
        invocation_key="exit-race", expected_native_session_id="native-a", prompt="Close out.",
    )
    send = asyncio.create_task(submit(manager, session.session_id, request))
    try:
        await asyncio.sleep(0)
        assert not send.done()
        manager._try_reattach_live_host.assert_not_awaited()
    finally:
        release.set()
        results = await asyncio.wait_for(asyncio.gather(cleanup, send), 2)
    await session._prompt_task
    assert results[1]["native_session_id"] == "native-a"
    assert session.client is replacement
    assert HostIndex(manager._host_index._path).get(session.session_id) == new_rec
    assert tmp_db.get_session(session.session_id)["status"] != "stopped"
    manager._reap_host_record.assert_called_once()
    assert len(tmp_db.get_turns(session.session_id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_client", [False, True])
@pytest.mark.parametrize("independent_index", [False, True])
async def test_exit_settlement_rechecks_captured_custody_after_shutdown(
    session_manager, tmp_db, mock_acp_client, replace_client, independent_index,
):
    manager = session_manager
    session = prepare(manager, tmp_db, mock_acp_client)
    old = session.client
    old.is_running = False
    old.host_child_exit_code = 7
    rec = HostRecord(session_id=session.session_id, port=1234, host_pid=os.getpid(), child_pid=222)
    manager._host_index.register(rec)
    new_rec = HostRecord(
        session_id=session.session_id, port=4321, host_pid=os.getpid(), child_pid=333,
    )
    replacement = Mock(is_running=True, host_child_exit_code=None)
    writer = (
        HostIndex(manager._host_index._path)
        if independent_index else manager._host_index
    )

    async def shutdown():
        if replace_client:
            session.client = replacement
        writer.register(new_rec)

    old.shutdown.side_effect = shutdown
    manager._reap_host_record = Mock()
    assert await manager.recover_disconnected_hosts() == 0
    assert session.client is (replacement if replace_client else old)
    assert session.status == SessionStatus.IDLE
    assert tmp_db.get_session(session.session_id)["status"] == "idle"
    assert HostIndex(manager._host_index._path).get(session.session_id) == new_rec
    manager._reap_host_record.assert_not_called()
    old.shutdown.assert_awaited_once()
