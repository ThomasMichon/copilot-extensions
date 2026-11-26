"""Exact managed stop preserves the conversation without touching another holder."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from agent_bridge import managed_exact_stop as exact
from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.session_status import SessionStatus
from agent_bridge.session_manager import Session, SessionBusyError, SessionManager
from agent_bridge.transport import SpawnTarget

pytestmark = pytest.mark.contract("agent_bridge.managed_exact_stop", "agent_bridge.session_lifecycle")


def _row(manager: SessionManager, session: Session) -> dict[str, Any]:
    row = manager.db.get_session(session.session_id)
    assert row is not None
    return row


@pytest.fixture
def held(
    tmp_path: Path, tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
) -> tuple[SessionManager, Session, Any]:
    target = SpawnTarget(type="local", cwd=str(tmp_path), project="example", worktree_id="wt-example")
    tmp_db.create_session(
        "managed-original", "original", None, str(tmp_path), "local", "idle", time.time(),
        target_json=target.to_json(),
    )
    tmp_db.update_session_acp_id("managed-original", "native-original")
    manager = SessionManager(tmp_db)
    session = manager.get_session("managed-original")
    assert session is not None
    session.status = SessionStatus.IDLE
    tmp_db.update_session_status(session.session_id, "idle", time.time())
    session.event_log = EventLog(db=tmp_db, session_id=session.session_id)
    client = Mock()
    client.acp_session_id = "native-original"
    client._prompt_in_flight = False
    client._connection = SimpleNamespace(close=AsyncMock())
    client._prompt_complete = True
    client._prompt_error = None
    client._out_of_turn_open = False
    client._on_event = None
    client._host_mode = True
    client.session_host_client = None
    client._host_closer = None
    client.has_active_background_tasks = False
    client.active_background_tasks = []
    client.shutdown = AsyncMock()
    client.cancel_prompt = AsyncMock()
    from agent_bridge.managed_stop_state import begin_prompt, record_completion

    begin_prompt(client)
    record_completion(client)
    session.client = client
    monkeypatch.setattr(manager, "_detach_host", AsyncMock())
    monkeypatch.setattr(manager, "_stop_relays", AsyncMock())
    monkeypatch.setattr(
        "agent_bridge.session_lifecycle._cleanup_worktree",
        AsyncMock(side_effect=AssertionError("exact stop attempted workspace cleanup")),
    )
    return manager, session, client


@pytest.mark.asyncio
async def test_exact_stop_preserves_native_workspace_history_and_pending_queue(
    held: tuple[SessionManager, Session, Any],
) -> None:
    manager, session, client = held
    before_target = session.target.to_json()
    assert session.event_log is not None
    session.event_log.append("fixture_history", {"text": "retained"})
    queued = manager.db.enqueue_prompt(session.session_id, "original pending prompt", time.time())
    await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    row = manager.db.get_session(session.session_id)
    assert row is not None
    assert row["status"] == "stopped" and row["acp_session_id"] == "native-original"
    assert row["target_json"] == before_target
    assert manager.get_session(session.session_id) is session
    assert session.target.worktree_id == "wt-example"
    assert session.acp_session_id == "native-original"
    assert manager.db.count_pending_prompts(session.session_id) == 1
    assert manager.db.list_pending_prompts(session.session_id)[0]["id"] == queued
    assert row["cold_recovery_stop_token"] and not row["background_recovery_enabled"]
    client.shutdown.assert_awaited_once()
    client.cancel_prompt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "native", "persisted-native", "client-native", "client-unknown", "object", "ended", "remote",
])
async def test_exact_stop_rechecks_holder_after_lifecycle_wait_without_writes(
    held: tuple[SessionManager, Session, Any], change: str,
) -> None:
    manager, session, client = held
    async with session._lifecycle_lock:
        pending = asyncio.create_task(
            manager.stop_session(session.session_id, expected_native_session_id="native-original"),
        )
        await asyncio.sleep(0)
        assert not pending.done()
        assert _row(manager, session)["cold_recovery_stop_token"] is None
        if change == "native":
            session.acp_session_id = "native-newer"
        elif change == "persisted-native":
            manager.db.update_session_acp_id(session.session_id, "native-newer")
        elif change == "client-native":
            client.acp_session_id = "native-newer"
        elif change == "client-unknown":
            client.acp_session_id = None
        elif change == "object":
            manager._sessions[session.session_id] = Session(session.session_id, "newer", session.target)
        elif change == "ended":
            session.status = SessionStatus.ENDED
        else:
            session.target.type = "remote-acp"
    with pytest.raises(ValueError, match="expected_native_mismatch|native_stop_owner_unsupported"):
        await pending
    assert _row(manager, session)["cold_recovery_stop_token"] is None
    assert session.background_recovery_enabled
    client.shutdown.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cancel", "shutdown"])
async def test_exact_stop_retains_unknown_teardown_without_confirming_stopped(
    held: tuple[SessionManager, Session, Any], operation: str,
) -> None:
    manager, session, client = held
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(exact.router)
    if operation == "cancel":
        session.status = SessionStatus.RUNNING
        client._prompt_in_flight = True
        client._connection = SimpleNamespace(close=AsyncMock())
        entered = asyncio.Event()

        async def turn() -> None:
            entered.set()
            await asyncio.Event().wait()

        session._prompt_task = asyncio.create_task(turn())
        await entered.wait()
        client.cancel_prompt.side_effect = RuntimeError("cancel outcome unknown")
    else:
        client.shutdown.side_effect = RuntimeError("shutdown outcome unknown")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as api:
            result = await api.post(
                f"/api/v1/sessions/{session.session_id}/stop/exact",
                json={"native_session_id": "native-original"},
            )
        assert result.status_code == 502
        assert result.json()["detail"]["code"] == "native_stop_unconfirmed"
        assert session.client is client
        assert session.status != SessionStatus.STOPPED
        assert _row(manager, session)["status"] != "stopped"
        assert _row(manager, session)["cold_recovery_stop_token"]
        if operation == "cancel":
            client.shutdown.assert_not_awaited()
    finally:
        if session._prompt_task is not None and not session._prompt_task.done():
            session._prompt_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await session._prompt_task


@pytest.mark.asyncio
async def test_background_tasks_are_rechecked_after_wait(
    held: tuple[SessionManager, Session, Any],
) -> None:
    manager, session, client = held
    async with session._turn_start_lock:
        pending = asyncio.create_task(
            manager.stop_session(session.session_id, expected_native_session_id="native-original"),
        )
        await asyncio.sleep(0)
        client.has_active_background_tasks = True
        client.active_background_tasks = ["retained-work"]
    with pytest.raises(SessionBusyError):
        await pending
    assert _row(manager, session)["cold_recovery_stop_token"] is None
    client.shutdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_stop_rechecks_client_after_host_detach(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, session, client = held

    async def detach(*_: Any, **__: Any) -> None:
        client.acp_session_id = "native-newer"

    monkeypatch.setattr(manager, "_detach_host", detach)
    with pytest.raises(RuntimeError, match="native_stop_unconfirmed"):
        await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    client.cancel_prompt.assert_not_awaited()
    client.shutdown.assert_not_awaited()
    assert session.client is client
    assert _row(manager, session)["status"] != "stopped"


@pytest.mark.asyncio
async def test_exact_stop_excludes_prompt_drain_during_cancel(
    held: tuple[SessionManager, Session, Any],
) -> None:
    manager, session, client = held
    session.status = SessionStatus.RUNNING
    client._prompt_in_flight = True
    client._connection = SimpleNamespace(close=AsyncMock())
    client._prompt_lock = asyncio.Lock()
    client._prompt_complete = True
    client._prompt_error = None
    entered, drain = asyncio.Event(), asyncio.Event()

    async def turn() -> None:
        entered.set()
        await drain.wait()
        async with session._turn_start_lock:
            raise AssertionError("stop admitted another queued turn")

    session._prompt_task = asyncio.create_task(turn())
    await entered.wait()

    async def cancel() -> None:
        assert session._turn_start_lock.locked() and session._lifecycle_lock.locked()
        client._prompt_in_flight = False
        drain.set()
        await asyncio.sleep(0)

    client.cancel_prompt.side_effect = cancel
    await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    assert session._prompt_task.cancelled()
    client.cancel_prompt.assert_awaited_once()
    client.shutdown.assert_awaited_once()
    assert session.status == SessionStatus.STOPPED


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["original", "managed-or", "native-original", "missing"])
async def test_exact_stop_never_expands_a_name_prefix_or_native_alias(
    held: tuple[SessionManager, Session, Any], reference: str,
) -> None:
    manager, session, client = held
    with pytest.raises(KeyError):
        await manager.stop_session(reference, expected_native_session_id="native-original")
    assert _row(manager, session)["cold_recovery_stop_token"] is None
    client.shutdown.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"force": True}, {"reap_host": True}, {"cancel_turn": False},
    {"allow_background_recovery": True}, {"_automatic_reap": True},
])
async def test_exact_stop_cannot_select_legacy_effects(
    held: tuple[SessionManager, Session, Any], options: dict[str, Any],
) -> None:
    manager, session, client = held
    with pytest.raises(ValueError, match="native_stop_options_unsupported"):
        await manager.stop_session(
            session.session_id, expected_native_session_id="native-original", **options,
        )
    client.shutdown.assert_not_awaited()
    assert _row(manager, session)["cold_recovery_stop_token"] is None


@pytest.mark.asyncio
async def test_repeated_exact_stop_does_not_repeat_teardown(
    held: tuple[SessionManager, Session, Any],
) -> None:
    manager, session, client = held
    await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    before = manager.db.get_session(session.session_id)
    with pytest.raises(ValueError, match="native_stop_settlement_unavailable"):
        await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    assert manager.db.get_session(session.session_id) == before
    client.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_cold_stopped_session_does_not_claim_native_quiescence(
    held: tuple[SessionManager, Session, Any],
) -> None:
    manager, session, client = held
    session.status = SessionStatus.STOPPED
    session.client = None
    assert session.background_recovery_enabled
    before = manager.db.get_session(session.session_id)
    with pytest.raises(ValueError, match="native_stop_settlement_unavailable"):
        await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    row = manager.db.get_session(session.session_id)
    assert row is not None
    assert row == before and session.background_recovery_enabled
    assert session.status == SessionStatus.STOPPED and session.acp_session_id == "native-original"
    client.shutdown.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("body,status", [
    ({"native_session_id": "native-original"}, 200),
    ({"native_session_id": "native-newer"}, 409),
    ({}, 422), ({"native_session_id": True}, 422),
    ({"native_session_id": ""}, 422), ({"native_session_id": "../native"}, 422),
    ({"native_session_id": "native-original\n"}, 422),
    ({"native_session_id": "native-original", "force": True}, 422),
])
async def test_actual_http_stop_reaches_exact_owner(
    held: tuple[SessionManager, Session, Any], body: dict[str, Any], status: int,
) -> None:
    manager, session, client = held
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(exact.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as api:
        result = await api.post(f"/api/v1/sessions/{session.session_id}/stop/exact", json=body)
    assert result.status_code == status, result.text
    if status == 200:
        assert result.json() == {
            "session_id": session.session_id, "native_session_id": "native-original",
            "status": "stopped", "host_reclaimed": False,
        }
        client.shutdown.assert_awaited_once()
    else:
        client.shutdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_http_identity_is_not_success(
    held: tuple[SessionManager, Session, Any],
) -> None:
    manager, _, client = held
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(exact.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as api:
        result = await api.post(
            "/api/v1/sessions/absent/stop/exact", json={"native_session_id": "native-original"},
        )
    assert result.status_code == 404
    client.shutdown.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["busy", "unconfirmed", "partial"])
async def test_http_does_not_confirm_or_repeat_an_unsuccessful_stop(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    manager, session, client = held
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(exact.router)
    if failure == "busy":
        client.has_active_background_tasks = True
        client.active_background_tasks = ["retained-work"]
        operation = AsyncMock(wraps=manager.stop_session)
    else:
        operation = AsyncMock(
            side_effect=RuntimeError("teardown outcome unknown") if failure == "partial" else None,
        )
    monkeypatch.setattr(manager, "stop_session", operation)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as api:
        result = await api.post(
            f"/api/v1/sessions/{session.session_id}/stop/exact",
            json={"native_session_id": "native-original"},
        )
    assert result.status_code == (409 if failure == "busy" else 502)
    operation.assert_awaited_once_with(session.session_id, expected_native_session_id="native-original")
    client.shutdown.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connection", "host", "process"])
async def test_exact_native_shutdown_retains_unconfirmed_handles(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from agent_bridge.acp_client import AcpClient

    manager, session, _ = held
    client: Any = AcpClient()
    client._acp_session_id = "native-original"
    session.client = client
    client._host_mode = failure != "process"
    connection = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close unknown")))
    closer = AsyncMock(side_effect=RuntimeError("Host close unknown"))
    proc = SimpleNamespace(
        pid=424242, returncode=None, terminate=Mock(), kill=Mock(),
        wait=AsyncMock(side_effect=TimeoutError),
    )
    if failure == "connection":
        client._connection = connection
    elif failure == "host":
        client._host_closer = closer
    else:
        client._process = proc
        monkeypatch.setattr("agent_bridge.acp_client.terminate_windows_tree", AsyncMock())
        monkeypatch.setattr("agent_bridge.acp_client.safe_killpg", Mock(return_value=False))
    from agent_bridge.managed_stop_state import begin_prompt, record_completion

    if client._connection is None:
        client._connection = SimpleNamespace(close=AsyncMock())
    client._prompt_complete = True
    begin_prompt(client)
    record_completion(client)
    with pytest.raises(RuntimeError, match="native_stop_unconfirmed"):
        await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    assert session.client is client
    assert _row(manager, session)["status"] == "idle"
    if failure == "connection":
        assert client._connection is connection
    elif failure == "host":
        assert client._host_closer is closer
    else:
        assert client._process is proc
        assert proc.wait.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("running,settles", [(False, True), (True, True), (True, False)])
async def test_exact_native_cancel_settles_before_frontend_detach(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch,
    running: bool, settles: bool,
) -> None:
    from agent_bridge.acp_client import AcpClient

    manager, session, _ = held
    client: Any = AcpClient()
    client._acp_session_id = "native-original"
    client._host_mode = True
    session.client = client
    order: list[str] = []
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def prompt(**_: Any) -> Any:
        started.set()
        await cancelled.wait()
        order.append("settled")
        return SimpleNamespace(stop_reason="cancelled")

    async def cancel(**_: Any) -> None:
        assert "detach" not in order
        order.append("cancel")
        if settles:
            cancelled.set()

    async def detach(current: Session, *, preserve_host: bool = False) -> None:
        assert current is session and preserve_host
        if running:
            assert order == ["cancel", "settled"]
        order.append("detach")

    client._connection = SimpleNamespace(prompt=prompt, cancel=cancel, close=AsyncMock())
    monkeypatch.setattr(manager, "_detach_host", detach)
    monkeypatch.setattr(manager, "_host_reapable", Mock(return_value=True))
    if running:
        session.status = SessionStatus.RUNNING
        session._prompt_task = asyncio.create_task(client.send_prompt("synthetic original turn"))
        await started.wait()
    else:
        cancelled.set()
        manager.db.create_turn(session.session_id, 0, "observed completion", time.time())
        await manager._run_prompt(session, 0, "observed completion")
        order.clear()
    try:
        if not settles:
            with pytest.raises(RuntimeError, match="native_stop_unconfirmed"):
                await manager.stop_session(session.session_id, expected_native_session_id="native-original")
            assert order == ["cancel"]
            assert session.client is client
            assert _row(manager, session)["status"] == "idle"
        else:
            await manager.stop_session(session.session_id, expected_native_session_id="native-original")
            assert order == (["cancel", "settled", "detach"] if running else ["detach"])
            assert session.status == SessionStatus.STOPPED
    finally:
        if session._prompt_task is not None and not session._prompt_task.done():
            session._prompt_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await session._prompt_task


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["cancel", "close"])
async def test_exact_stop_preserves_background_work_started_during_teardown(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    manager, session, client = held
    terminate = AsyncMock()
    monkeypatch.setattr("agent_bridge.acp_client._terminate_process_tree", terminate)

    def become_busy() -> None:
        client.has_active_background_tasks = True
        client.active_background_tasks = ["retained-late-work"]

    async def close() -> None:
        if stage == "close":
            become_busy()

    client._connection = SimpleNamespace(close=close)
    if stage == "cancel":
        client._prompt_in_flight = True
        client._prompt_lock = asyncio.Lock()
        client._prompt_complete = True
        client._prompt_error = None

        async def cancel() -> None:
            client._prompt_in_flight = False
            become_busy()

        client.cancel_prompt.side_effect = cancel
    else:
        client._host_mode = False
        client._process = SimpleNamespace(returncode=None)
    with pytest.raises(RuntimeError, match="native_stop_unconfirmed"):
        await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    assert session.client is client
    assert _row(manager, session)["status"] == "idle"
    terminate.assert_not_awaited()
    client.shutdown.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["cancel", "close"])
async def test_exact_stop_bounds_blocked_transport_operations(
    held: tuple[SessionManager, Session, Any], phase: str,
) -> None:
    manager, session, client = held

    async def blocked() -> None:
        await asyncio.Event().wait()

    client._connection = SimpleNamespace(close=AsyncMock(side_effect=blocked if phase == "close" else None))
    if phase == "cancel":
        session.status = SessionStatus.RUNNING
        client._prompt_in_flight = True
        client.cancel_prompt.side_effect = blocked
    with pytest.raises(RuntimeError, match="native_stop_unconfirmed"):
        await asyncio.wait_for(
            manager.stop_session(session.session_id, expected_native_session_id="native-original"),
            timeout=12.0,
        )
    assert session.client is client
    assert _row(manager, session)["status"] == "idle"
    assert not session._turn_start_lock.locked() and not session._lifecycle_lock.locked()
    client.shutdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_stop_rejects_empty_native_prompt_failure(
    held: tuple[SessionManager, Session, Any],
) -> None:
    from agent_bridge.acp_client import AcpClient

    manager, session, _ = held
    client: Any = AcpClient()
    client._acp_session_id = "native-original"
    client._host_mode = True
    session.client = client
    session.status = SessionStatus.RUNNING
    started = asyncio.Event()
    outcome: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

    async def prompt(**_: Any) -> Any:
        started.set()
        return await outcome

    async def cancel(**_: Any) -> None:
        outcome.set_exception(TimeoutError())

    close = AsyncMock()
    client._connection = SimpleNamespace(prompt=prompt, cancel=cancel, close=close)
    session._prompt_task = asyncio.create_task(client.send_prompt("synthetic failing turn"))
    await started.wait()
    try:
        with pytest.raises(RuntimeError, match="native_stop_unconfirmed"):
            await manager.stop_session(session.session_id, expected_native_session_id="native-original")
        assert client._prompt_error == ""
        assert session.client is client and _row(manager, session)["status"] == "idle"
        close.assert_not_awaited()
    finally:
        if not session._prompt_task.done():
            session._prompt_task.cancel()
        await asyncio.gather(session._prompt_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_exact_stop_refuses_adopted_running_turn_before_effects(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.acp_client import AcpClient

    manager, session, _ = held
    client: Any = AcpClient()
    client._acp_session_id = "native-original"
    client._host_mode = True
    session.client = client
    session.status = SessionStatus.RUNNING
    shutdown = AsyncMock()
    detach = AsyncMock()
    monkeypatch.setattr(client, "shutdown", shutdown)
    monkeypatch.setattr(manager, "_detach_host", detach)
    assert session._prompt_task is None and not client._prompt_in_flight
    with pytest.raises(ValueError, match="native_stop_settlement_unavailable"):
        await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    assert _row(manager, session)["cold_recovery_stop_token"] is None
    assert session.background_recovery_enabled
    shutdown.assert_not_awaited()
    detach.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("holder", ["adopted-idle", "cached-stopped", "unobserved-complete"])
async def test_exact_stop_requires_native_evidence_before_effects(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch, holder: str,
) -> None:
    from agent_bridge.acp_client import AcpClient

    manager, session, _ = held
    client: Any = AcpClient()
    client._acp_session_id = "native-original"
    client._host_mode = True
    client._connection = SimpleNamespace(close=AsyncMock())
    session.client = client
    if holder == "cached-stopped":
        session.status = SessionStatus.STOPPED
        session.client = None
    elif holder == "unobserved-complete":
        client._prompt_complete = True
        client._prompt_error = None
    before = _row(manager, session)
    shutdown, detach = AsyncMock(), AsyncMock()
    monkeypatch.setattr(client, "shutdown", shutdown)
    monkeypatch.setattr(manager, "_detach_host", detach)
    with pytest.raises(ValueError, match="native_stop_settlement_unavailable"):
        await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    assert _row(manager, session) == before
    assert session.background_recovery_enabled
    shutdown.assert_not_awaited()
    detach.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfirmed_stop_fences_real_runner_queue_tail(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.acp_client import AcpClient

    manager, session, _ = held
    client: Any = AcpClient()
    client._acp_session_id = "native-original"
    client._host_mode = True
    client._host_transport_alive = True
    session.client = client
    session.status = SessionStatus.RUNNING
    session.turn_count = 1
    manager.db.create_turn(session.session_id, 0, "original", time.time())
    queued = manager.db.enqueue_prompt(session.session_id, "retained followup", time.time())
    before = manager.db.list_pending_prompts(session.session_id)
    started, cancelled = asyncio.Event(), asyncio.Event()
    received: list[str] = []

    async def prompt(**kwargs: Any) -> Any:
        received.append(str(kwargs["prompt"]))
        started.set()
        if len(received) == 1:
            await cancelled.wait()
        return SimpleNamespace(stop_reason="cancelled")

    async def cancel(**_: Any) -> None:
        cancelled.set()
        async with client._prompt_lock:
            client._background_tasks["late"] = {"status": "running"}

    client._connection = SimpleNamespace(prompt=prompt, cancel=cancel, close=AsyncMock())
    monkeypatch.setattr(manager, "_notify_host_reapable", AsyncMock())
    original = asyncio.create_task(manager._run_prompt(session, 0, "original"))
    session._prompt_task = original
    await started.wait()
    try:
        with pytest.raises(RuntimeError, match="native_stop_unconfirmed"):
            await manager.stop_session(session.session_id, expected_native_session_id="native-original")
        await asyncio.wait_for(asyncio.shield(original), timeout=2.0)
        assert manager.db.list_pending_prompts(session.session_id) == before
        assert manager.db.list_pending_prompts(session.session_id)[0]["id"] == queued
        assert session.turn_count == 1 and len(received) == 1
        assert session.client is client
    finally:
        tasks = {task for task in (original, session._prompt_task) if task is not None}
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["submit", "send", "kick", "drain"])
async def test_exact_intent_fences_future_admission_without_touching_queue(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    manager, session, client = held
    manager.db.request_cold_recovery_stop(session.session_id, exact_stop=True)
    queued = manager.db.enqueue_prompt(session.session_id, "retained", time.time())
    before = manager.db.list_pending_prompts(session.session_id)
    session.status = SessionStatus.STOPPED
    resume = AsyncMock(side_effect=AssertionError("implicit resume crossed exact stop"))
    handoff = AsyncMock(side_effect=AssertionError("handoff crossed exact stop"))
    monkeypatch.setattr(manager, "resume_session", resume)
    monkeypatch.setattr(manager, "handoff_session", handoff)
    if operation in ("submit", "send"):
        method = manager.submit_prompt if operation == "submit" else manager.submit_or_queue_prompt
        with pytest.raises(ValueError, match="exact_stop_requires_resume:"):
            await method(session.session_id, "must not run")
    elif operation == "kick":
        await manager._kick_pending_drain(session)
    else:
        session.status = SessionStatus.IDLE
        await manager._drain_pending_prompts(session)
    assert manager.db.list_pending_prompts(session.session_id) == before
    assert before[0]["id"] == queued and session.turn_count == 0
    assert not _row(manager, session)["background_recovery_enabled"]
    resume.assert_not_awaited()
    handoff.assert_not_awaited()
    client.shutdown.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("superseded", [
    "none", "lost-flag", "stop-token", "notice", "late-notice", "policy-refusal",
])
async def test_explicit_recovery_can_drain_and_clear_its_exact_stop_token(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch, superseded: str,
) -> None:
    from repository_policy import AgentHostingError

    from agent_bridge import hosting_policy
    from agent_bridge.cold_outages import ColdRecoveryStoppedError
    from agent_bridge.managed_stop_state import prompt_fenced

    manager, session, client = held
    session.target.project = None
    session.target.worktree_id = None
    session.target.explicit_cwd = True
    manager.db.update_session_target(session.session_id, session.target.to_json())
    queued = manager.db.enqueue_prompt(session.session_id, "retained", time.time())
    client.shutdown.side_effect = RuntimeError("native teardown unconfirmed")
    with pytest.raises(RuntimeError, match="native_stop_unconfirmed"):
        await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    token = _row(manager, session)["cold_recovery_stop_token"]
    assert token and session._stop_requested
    client.is_running = False
    client.pid = None
    client.send_prompt = AsyncMock(return_value={"stop_reason": "end_turn"})
    monkeypatch.setattr(manager, "_notify_host_reapable", AsyncMock())
    monkeypatch.setattr(manager, "_try_reattach_live_host", AsyncMock(return_value=False))
    monkeypatch.setattr(manager, "_refresh_provider_target", AsyncMock())
    monkeypatch.setattr(hosting_policy, "marketplace_id", lambda: "")
    path_check = Mock(wraps=hosting_policy.require_path_hosting)
    monkeypatch.setattr(hosting_policy, "require_path_hosting", path_check)
    original_policy = hosting_policy.require_target_hosting

    async def policy(*args: Any, **kwargs: Any) -> None:
        await original_policy(*args, **kwargs)
        if superseded == "stop-token":
            manager.db.request_cold_recovery_stop(session.session_id, exact_stop=True)
        elif superseded == "notice":
            session._stop_notices += 1
        elif superseded == "policy-refusal":
            raise AgentHostingError("fixture policy refused")

    monkeypatch.setattr(hosting_policy, "require_target_hosting", policy)

    async def attach(*args: Any, **kwargs: Any) -> tuple[Any, str]:
        client.is_running = True
        client._connection = SimpleNamespace(close=AsyncMock())
        if superseded == "late-notice":
            session._stop_notices += 1
        return client, "native-original"

    transport = AsyncMock(side_effect=attach)
    monkeypatch.setattr(manager, "_resume_via_new_remote_host", transport)
    assert prompt_fenced(manager, session.session_id)
    if superseded == "lost-flag":
        session._stop_requested = False
        with pytest.raises(ColdRecoveryStoppedError):
            await hosting_policy.require_session_hosting(
                manager, session, operation="implicit resume",
            )
    with pytest.raises(ValueError, match="exact_stop_requires_resume:"):
        await manager.submit_or_queue_prompt(session.session_id, "must not run")
    if superseded not in ("none", "lost-flag"):
        error = AgentHostingError if superseded == "policy-refusal" else ColdRecoveryStoppedError
        try:
            with pytest.raises(error):
                await manager.resume_session(session.session_id)
        finally:
            if session._prompt_task is not None:
                await asyncio.gather(session._prompt_task, return_exceptions=True)
        assert session._stop_requested and prompt_fenced(manager, session.session_id)
        assert manager.db.list_pending_prompts(session.session_id)[0]["id"] == queued
        if superseded == "late-notice":
            assert path_check.call_count >= 2
            transport.assert_awaited_once()
        else:
            assert path_check.call_count == 1
            transport.assert_not_awaited()
        return
    assert await manager.resume_session(session.session_id) is session
    assert session._prompt_task is not None
    await session._prompt_task
    assert not session._stop_requested and not prompt_fenced(manager, session.session_id)
    assert path_check.call_count >= 2
    transport.assert_awaited_once()
    assert _row(manager, session)["cold_recovery_stop_token"] is None
    assert manager.db.list_pending_prompts(session.session_id) == []
    turn = manager.db.get_turn(session.session_id, 0)
    assert turn is not None and turn["prompt"] == "retained"
    assert not manager.db.remove_pending_prompt(session.session_id, queued)
    client.send_prompt.assert_awaited_once_with("retained")


@pytest.mark.asyncio
async def test_real_native_completion_evidence_is_invalidated_by_later_running(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.acp_client import AcpClient
    from agent_bridge.managed_stop_state import settlement_available

    manager, session, _ = held
    events: list[str] = []
    client: Any = AcpClient(on_event=lambda name, data: events.append(name))
    client._acp_session_id = "native-original"
    client._host_mode = True
    client._connection = SimpleNamespace(
        prompt=AsyncMock(return_value=SimpleNamespace(stop_reason="end_turn")), close=AsyncMock(),
    )
    session.client = client
    monkeypatch.setattr(manager, "_notify_host_reapable", AsyncMock())
    manager.db.create_turn(session.session_id, 0, "observed", time.time())
    assert not settlement_available(client)
    await manager._run_prompt(session, 0, "observed")
    assert settlement_available(client)
    callback = client._on_event
    manager.db.create_turn(session.session_id, 1, "second", time.time())
    await manager._run_prompt(session, 1, "second")
    assert client._on_event is callback and settlement_available(client)
    events.clear()
    client._emit("session_state_changed", {"status": "running"})
    client._emit("session_state_changed", {"status": "idle"})
    assert events == ["session_state_changed", "session_state_changed"]
    assert not settlement_available(client)
    with pytest.raises(ValueError, match="native_stop_settlement_unavailable"):
        await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    assert _row(manager, session)["cold_recovery_stop_token"] is None


@pytest.mark.asyncio
async def test_exact_stop_refuses_unconfirmed_host_detach_before_effects(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_remote_driver.session_host.client import SessionHostClient

    manager, session, client = held
    writer: Any = SimpleNamespace(
        write=Mock(side_effect=BrokenPipeError("detach transport failed")), drain=AsyncMock(),
    )
    host = SessionHostClient(asyncio.StreamReader(), writer)
    client.session_host_client = host
    monkeypatch.delattr(manager, "_detach_host")
    before = _row(manager, session)
    with pytest.raises(ValueError, match="native_stop_detach_unavailable"):
        await manager.stop_session(session.session_id, expected_native_session_id="native-original")
    assert _row(manager, session) == before and session.client is client
    writer.write.assert_not_called()
    client._connection.close.assert_not_awaited()
    client.shutdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_stop_does_not_deadlock_reattach_resume_prompt(
    held: tuple[SessionManager, Session, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, session, client = held
    entered, submit, turn_acquired = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class ObservedLock(asyncio.Lock):
        async def acquire(self) -> Literal[True]:
            await super().acquire()
            turn_acquired.set()
            return True

    session._turn_start_lock = ObservedLock()
    submitted = AsyncMock(return_value=1)
    monkeypatch.setattr(manager, "_submit_prompt_locked", submitted)

    async def reattach(rec: Any, current: Session, **kwargs: Any) -> bool:
        assert current is session and kwargs["send_resume"]
        assert session._lifecycle_lock.locked()
        entered.set()
        await submit.wait()
        await manager.submit_prompt(rec.session_id, "Resume")
        return True

    monkeypatch.setattr(manager, "_reattach_one", reattach)
    rec = SimpleNamespace(session_id=session.session_id, boundary="local", resume_on_reattach=True)
    recovery = asyncio.create_task(manager._reattach_or_note_failure(
        rec, session, keep=SessionStatus.IDLE, codespace_availability={}, now=time.time(),
    ))
    await entered.wait()
    stop = asyncio.create_task(manager.stop_session(
        session.session_id, expected_native_session_id="native-original",
    ))
    try:
        await turn_acquired.wait()
        submit.set()
        stopped, recovered = await asyncio.wait_for(
            asyncio.gather(stop, recovery, return_exceptions=True), timeout=12.0,
        )
        assert isinstance(stopped, ValueError) and str(stopped) == "native_stop_lifecycle_busy"
        assert recovered is True
        submitted.assert_awaited_once()
        assert _row(manager, session)["cold_recovery_stop_token"] is None
        assert not session._turn_start_lock.locked() and not session._lifecycle_lock.locked()
        client.shutdown.assert_not_awaited()
    finally:
        for task in (stop, recovery):
            if not task.done():
                task.cancel()
        await asyncio.gather(stop, recovery, return_exceptions=True)


@pytest.mark.parametrize("status", [SessionStatus.IDLE, SessionStatus.RUNNING])
def test_failed_exact_stop_cannot_confirm_cooperative_stop(
    held: tuple[SessionManager, Session, Any], status: SessionStatus,
) -> None:
    from fastapi.testclient import TestClient

    from agent_bridge.client import BridgeClientError
    from agent_bridge.client_session_stop import SessionStopClientMixin
    from agent_bridge.routes.prompts import router as prompts
    from agent_bridge.session_stop import run_stop

    manager, session, client = held
    session.status = status
    manager.db.update_session_status(session.session_id, status.value, time.time())
    client.shutdown.side_effect = RuntimeError("native teardown unconfirmed")
    client.send_prompt = AsyncMock(side_effect=AssertionError("stop notice reached the model"))
    queued = manager.db.enqueue_prompt(session.session_id, "retained original", time.time())
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(exact.router)
    app.include_router(prompts, prefix="/api/v1/sessions")
    requests: list[str] = []
    with TestClient(app) as api:
        failed = api.post(
            f"/api/v1/sessions/{session.session_id}/stop/exact",
            json={"native_session_id": "native-original"},
        )
        assert failed.status_code == 502 and session.status == status

        class Client(SessionStopClientMixin):
            def daemon_supports(self, min_version: int) -> bool:
                return True

            def get_session(self, session_id: str) -> dict[str, Any]:
                return {"status": session.status.value, "turn_count": session.turn_count}

            def _request(
                self, method: str, path: str, body: dict[str, Any] | None = None, *,
                params: dict[str, Any] | list[tuple[str, Any]] | None = None,
                request_timeout: float | None = None, allow_retry: bool = True,
            ) -> dict[str, Any] | None:
                requests.append(path)
                response = api.request(method, path, json=body, params=params)
                value = response.json()
                assert isinstance(value, dict)
                if response.status_code >= 400:
                    raise BridgeClientError(response.status_code, value.get("detail", value))
                return value

        with pytest.raises(BridgeClientError, match="exact_stop_requires_resume"):
            run_stop(Client(), session.session_id, grace=1.0, confirm_timeout=0.0)
    assert requests == [f"/api/v1/sessions/{session.session_id}/turns"]
    assert manager.db.list_pending_prompts(session.session_id)[0]["id"] == queued
    assert session.status == status and session.turn_count == 0
    client.send_prompt.assert_not_awaited()
