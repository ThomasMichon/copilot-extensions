from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent_bridge.native_receipt_records import NativeReceiptRecords
from agent_bridge.native_send_admission import NativeSendPrompt
from agent_bridge.routes.sessions import router
from agent_bridge.session_status import SessionStatus
from test_native_send_admission import native_send as native_send


@pytest.mark.asyncio
async def test_public_receipt_after_holder_unavailable(
    native_send: tuple[Any, Any, NativeSendPrompt], tmp_path: Path,
) -> None:
    manager, session, original = native_send
    app = FastAPI()
    app.state.session_manager = manager
    app.state.native_receipt_records = NativeReceiptRecords.create(tmp_path / "records")
    app.include_router(router)
    path = f"/api/v1/sessions/{session.session_id}/turns/native-bound/send"
    body = original.model_copy(update={"no_resume": True}).model_dump(mode="json")
    with TestClient(app) as client:
        session.status = SessionStatus.RUNNING
        busy = client.post(path, json=body)
        assert busy.status_code == 409 and "not idle" in busy.text
        assert session.turn_count == 0
        session.status = SessionStatus.IDLE
        first = client.post(path, json=body)
        assert first.status_code == 200
        assert session.turn_count == 1
        manager._submit_prompt_locked = AsyncMock(side_effect=AssertionError("second admission"))
        manager._sessions.clear()
        observed = client.post(path + "/receipt", json=body)
        assert observed.status_code == 200, observed.text
        assert observed.json() == first.json()
        assert client.post(path, json=body).json() == first.json()
    manager._submit_prompt_locked.assert_not_awaited()
    manager.resume_session.assert_not_awaited()
    manager.handoff_session.assert_not_awaited()


@pytest.mark.parametrize("historical", [False, True])
def test_journal_only_receipt_with_independent_store(
    native_send: tuple[Any, Any, NativeSendPrompt], tmp_path: Path, historical: bool,
) -> None:
    manager, session, original = native_send
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(router)
    records = NativeReceiptRecords.create(tmp_path / "records")
    if not historical:
        app.state.native_receipt_records = records
    path = f"/api/v1/sessions/{session.session_id}/turns/native-bound/send"
    body = original.model_copy(update={"no_resume": historical}).model_dump(mode="json")
    with TestClient(app) as client:
        admitted = client.post(path, json=body)
        assert admitted.status_code == 200
        app.state.native_receipt_records = records
        manager._submit_prompt_locked = AsyncMock(side_effect=AssertionError("unexpected send"))
        observed = client.post(path + "/receipt", json=body)
        assert observed.status_code == 200 and observed.json() == admitted.json()
    manager._submit_prompt_locked.assert_not_awaited()
    assert session.turn_count == 1
