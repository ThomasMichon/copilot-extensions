"""Original journal settlement with a synthetic actor and real private SQLite."""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_bridge.db import Database
from agent_bridge.session_status import SessionStatus
from agent_bridge.native_keyed_admission import admission_receipt, submit as submit_initial
from agent_bridge.native_send_admission import NativeSendPrompt, read_receipt, submit
from agent_bridge.native_send_settlement import settle
from test_native_keyed_admission import keyed as keyed


async def _seed(
    keyed: tuple[Any, Any, Any], tmp_db: Database,
) -> tuple[Any, Any, NativeSendPrompt]:
    manager, session, initial = keyed
    tmp_db.execute_write(
        "UPDATE sessions SET acp_session_id=? WHERE id=?",
        (session.acp_session_id, session.session_id),
    )
    await submit_initial(manager, session.session_id, initial)
    await session._prompt_task
    session.status = SessionStatus.IDLE
    manager._run_prompt.reset_mock()
    request = NativeSendPrompt(
        invocation_key="original-wake", expected_native_session_id=session.acp_session_id,
        caller_id="fixture-controller", prompt="Continue this original assignment.",
    )
    return manager, session, request


@pytest.mark.asyncio
async def test_settlement_fences_original_without_turn_or_resume(
    keyed: tuple[Any, Any, Any], tmp_db: Database,
) -> None:
    manager, session, request = await _seed(keyed, tmp_db)
    session.status = SessionStatus.STOPPED
    before = tmp_db.get_turns(session.session_id)
    receipt = await settle(manager, session.session_id, request)
    assert receipt["admission"] == "not_admitted" and receipt["fenced"] is True
    assert await settle(manager, session.session_id, request) == receipt
    assert await read_receipt(manager, session.session_id, request) is None
    with pytest.raises(ValueError, match="native_send_settled"):
        await submit(manager, session.session_id, request)
    assert tmp_db.get_turns(session.session_id) == before
    assert session.turn_count == 1 and session.status == SessionStatus.STOPPED
    manager.resume_session.assert_not_awaited()
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_accepted_original_receipt_wins_without_an_exclusion(
    keyed: tuple[Any, Any, Any], tmp_db: Database,
) -> None:
    manager, session, request = await _seed(keyed, tmp_db)
    accepted = await submit(manager, session.session_id, request)
    await session._prompt_task
    session.status = SessionStatus.STOPPED
    manager._run_prompt.reset_mock()
    assert await settle(manager, session.session_id, request) == accepted
    assert tmp_db.execute_read("SELECT * FROM native_send_settlements") == []
    assert session.turn_count == 2
    manager.resume_session.assert_not_awaited()
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("prompt", "Changed intent"), ("caller_id", "another-controller"),
    ("no_resume", True),
])
async def test_settled_key_changed_request_cannot_reuse_negative_result(
    keyed: tuple[Any, Any, Any], tmp_db: Database, field: str, value: object,
) -> None:
    manager, session, request = await _seed(keyed, tmp_db)
    await settle(manager, session.session_id, request)
    with pytest.raises(ValueError, match="native_invocation_conflict"):
        await settle(manager, session.session_id, request.model_copy(update={field: value}))
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [
    "origin", "native", "admission", "events", "event-shape", "oversized", "gap",
])
async def test_missing_or_unqualified_original_history_stays_unknown(
    keyed: tuple[Any, Any, Any], tmp_db: Database, damage: str,
) -> None:
    manager, session, request = await _seed(keyed, tmp_db)
    if damage == "origin":
        tmp_db.execute_write("DELETE FROM workspace_embodiments")
    elif damage == "native":
        tmp_db.execute_write("UPDATE sessions SET acp_session_id='other-native'")
    elif damage == "admission":
        tmp_db.execute_write("UPDATE turns SET native_admission_json=NULL")
    elif damage == "events":
        tmp_db.flush()
        tmp_db.execute_write("DELETE FROM events")
    elif damage == "event-shape":
        tmp_db.flush()
        tmp_db.execute_write("UPDATE events SET data_json='[]'")
    elif damage == "oversized":
        for index in range(1, 101):
            tmp_db.create_turn(session.session_id, index, "retained work", 1.0)
    else:
        tmp_db.execute_write("UPDATE turns SET turn_index=1")
    with pytest.raises(ValueError, match="native_send_history_unproven"):
        await settle(manager, session.session_id, request)
    assert tmp_db.execute_read("SELECT * FROM native_send_settlements") == []
    manager.resume_session.assert_not_awaited()
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_journal_tail_cannot_become_negative_evidence(
    keyed: tuple[Any, Any, Any], tmp_db: Database,
) -> None:
    manager, session, request = await _seed(keyed, tmp_db)
    await submit(manager, session.session_id, request)
    await session._prompt_task
    session.status = SessionStatus.IDLE
    tmp_db.execute_write("DELETE FROM turns WHERE turn_index=1")
    future = request.model_copy(update={"invocation_key": "later-wake", "prompt": "Later work"})
    with pytest.raises(ValueError, match="native_send_history_unproven"):
        await settle(manager, session.session_id, future)
    assert tmp_db.execute_read("SELECT * FROM native_send_settlements") == []


@pytest.mark.asyncio
async def test_persistent_guard_excludes_a_delayed_older_writer(
    keyed: tuple[Any, Any, Any], tmp_db: Database,
) -> None:
    manager, session, request = await _seed(keyed, tmp_db)
    receipt = await settle(manager, session.session_id, request)
    encoded = json.dumps({
        "facts": request.facts(), "receipt": admission_receipt(session.session_id, 1, request),
    })
    with sqlite3.connect(tmp_db.db_path) as older:
        with pytest.raises(sqlite3.IntegrityError, match="native_send_settled"):
            older.execute(
                "INSERT INTO turns (session_id,turn_index,prompt,acquisition_delivery_id,"
                "native_admission_json) VALUES (?,?,?,?,?)",
                (session.session_id, 1, request.prompt, request.delivery_id(), encoded),
            )
    assert await settle(manager, session.session_id, request) == receipt
    assert len(tmp_db.get_turns(session.session_id)) == 1
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_native_holder_refuses(
    keyed: tuple[Any, Any, Any], tmp_db: Database,
) -> None:
    manager, session, request = await _seed(keyed, tmp_db)
    session.acp_session_id = "another-native"
    manager._submit_prompt_locked = AsyncMock(side_effect=AssertionError("unexpected send"))
    with pytest.raises(ValueError, match="expected_native_mismatch"):
        await settle(manager, session.session_id, request)
    assert tmp_db.execute_read("SELECT * FROM native_send_settlements") == []
    manager._submit_prompt_locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_settlement_survives_database_reopening(
    keyed: tuple[Any, Any, Any], tmp_db: Database,
) -> None:
    from agent_bridge.native_send_settlement import settle_record

    manager, session, request = await _seed(keyed, tmp_db)
    receipt = await settle(manager, session.session_id, request)
    reopened = Database(tmp_db.db_path)
    try:
        assert settle_record(reopened, session.session_id, request) == receipt
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_damaged_persistent_exclusion_is_not_silently_repaired(
    keyed: tuple[Any, Any, Any], tmp_db: Database,
) -> None:
    from agent_bridge.runtime_custody import RuntimeCustodyError

    manager, session, request = await _seed(keyed, tmp_db)
    await settle(manager, session.session_id, request)
    tmp_db.execute_write("DROP TRIGGER turns_settled_native_send_insert")
    with pytest.raises(RuntimeCustodyError, match="turns_settled_native_send_insert"):
        Database(tmp_db.db_path)
    assert tmp_db.execute_read(
        "SELECT 1 FROM sqlite_master WHERE name='turns_settled_native_send_insert'",
    ) == []
