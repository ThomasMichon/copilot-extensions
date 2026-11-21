import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_bridge import native_receipt_records as owner
from agent_bridge.native_send_admission import NativeSendPrompt, submit
from test_native_send_admission import native_send as native_send


@pytest.mark.asyncio
async def test_alias_refused_before_record_or_admission(
    native_send: tuple[Any, Any, NativeSendPrompt], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, session, original = native_send
    req = original.model_copy(update={"no_resume": True})
    store = owner.NativeReceiptRecords.create(tmp_path / "records")
    monkeypatch.setattr(manager, "get_session", lambda _: session)
    with pytest.raises(ValueError, match="canonical_bridge_session_required"):
        await store.submit_once(manager, req.expected_native_session_id, req)
    with pytest.raises(KeyError, match="no retained"):
        store.inspect(req.expected_native_session_id, req)
    manager._run_prompt.assert_not_awaited()
    assert session.turn_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", [False, True])
async def test_retained_native_receipt_never_readmits(
    native_send: tuple[Any, Any, NativeSendPrompt], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lost: bool,
) -> None:
    manager, session, original = native_send
    sid = session.session_id
    req = original.model_copy(update={"no_resume": True})
    store = owner.NativeReceiptRecords.create(tmp_path / "records")

    async def transport(*args: Any) -> dict[str, Any]:
        r = await submit(*args)
        if lost:
            raise ConnectionError("response lost after original admission")
        return r

    monkeypatch.setattr(owner, "submit", transport)
    if lost:
        with pytest.raises(ConnectionError):
            await store.submit_once(manager, sid, req)
    else:
        assert await store.submit_once(manager, sid, req) is not None
    await session._prompt_task
    manager.db.flush()
    session_count = session.turn_count
    assert session_count == 1
    reopened = owner.NativeReceiptRecords(store.path)
    monkeypatch.setattr(owner, "submit", AsyncMock(side_effect=AssertionError("duplicate admission")))
    with monkeypatch.context() as unavailable:
        unavailable.setattr(manager, "get_session", lambda _: None)
        if lost:
            assert reopened.inspect(sid, req) is None
            with pytest.raises(KeyError, match="native_session_unavailable"):
                await reopened.submit_once(manager, sid, req)
            assert reopened.inspect(sid, req) is None
        else:
            assert await reopened.submit_once(manager, sid, req) == reopened.inspect(sid, req)
    positive = await reopened.reconcile(manager, sid, req)
    assert positive == {"version": 1, "session_id": sid, "native_session_id": req.expected_native_session_id,
                        "invocation_key": req.invocation_key, "turn_index": 0, "queued": False, "admission": "accepted"}
    assert await reopened.submit_once(manager, sid, req) == positive
    for changed in (req.model_copy(update={"prompt": "new intent"}), req.model_copy(update={"caller_id": "other"}),
                    req.model_copy(update={"expected_native_session_id": "other-native"})):
        with pytest.raises(ValueError, match="conflict"):
            reopened.inspect(sid, changed)
    assert session.turn_count == session_count
    manager._run_prompt.assert_awaited_once()
    manager.resume_session.assert_not_awaited()
    manager.handoff_session.assert_not_awaited()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM native_v1").fetchone() == (1,)
