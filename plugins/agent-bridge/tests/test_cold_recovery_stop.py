"""Explicit stop intent survives cold recovery and cannot be mistaken for drain."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from zdd import diagnostics, process_exit

from agent_bridge import cold_outages
from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.db_adoption import open_existing_writable
from agent_bridge.session_manager import Session, SessionBusyError, SessionManager
from agent_bridge.transport import SpawnTarget
from test_cold_drain import NATIVE, messages, until
from test_cold_recovery_drain import (
    RecoveryFixture,
    interrupted_successor as interrupted_successor,
)


def _token(manager: SessionManager, session_id: str) -> str | None:
    return manager.db.get_session(session_id)["cold_recovery_stop_token"]


def _prompts(transcript: Path) -> list[dict[str, Any]]:
    return [item for item in messages(transcript) if item.get("method") == "session/prompt"]


def _boots(transcript: Path) -> int:
    return len([item for item in messages(transcript) if "boot" in item])


@pytest.mark.asyncio
@pytest.mark.parametrize("entry,pause_at", [
    ("resume", 1), ("resync", 1), ("handoff", 1), ("resume", 2), ("resync", 2),
])
async def test_stop_during_hosting_admission_is_not_absorbed_as_manual_authority(
    interrupted_successor: RecoveryFixture, monkeypatch: pytest.MonkeyPatch,
    entry: str, pause_at: int,
) -> None:
    from agent_bridge import hosting_policy

    manager, session_id, transcript = interrupted_successor
    session = manager.get_session(session_id)
    before_boots = _boots(transcript)
    before_prompts = _prompts(transcript)
    entered, release = asyncio.Event(), asyncio.Event()
    checks = 0

    async def admit(*_args: Any, **_kwargs: Any) -> None:
        nonlocal checks
        checks += 1
        if checks == pause_at:
            entered.set()
            await release.wait()

    monkeypatch.setattr(hosting_policy, "require_target_hosting", admit)
    connect = AsyncMock(side_effect=AssertionError("superseded admission launched a native process"))
    monkeypatch.setattr(manager, "_connect_via_session_host", connect)
    operation = (
        manager.resume_session(session_id) if entry == "resume"
        else manager.resync_session(session_id) if entry == "resync"
        else manager.handoff_session(session_id, seed_text="retained task")
    )
    pending = asyncio.create_task(operation)
    stopping = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        stopping = asyncio.create_task(manager.stop_session(session_id, force=True))
        await until(lambda: _token(manager, session_id) is not None)
        token = _token(manager, session_id)
        if pause_at == 1:
            await stopping
            assert session._stop_requested is False
        release.set()
        with pytest.raises(cold_outages.ColdRecoveryStoppedError, match="stop superseded"):
            await pending
        await stopping
        assert _token(manager, session_id) == token
        assert session.background_recovery_enabled is False
        assert cold_outages.latest(manager.db, session_id)["state"] == "interrupted"
        assert _boots(transcript) == before_boots
        assert _prompts(transcript) == before_prompts
        connect.assert_not_awaited()
        assert getattr(manager, "_active_acquisitions", 0) == 0
    finally:
        release.set()
        await asyncio.gather(
            pending, *([stopping] if stopping is not None else []),
            return_exceptions=True,
        )


def test_cold_stop_migration_preserves_ambiguous_legacy_dormancy(tmp_path: Path) -> None:
    path = tmp_path / "v28.db"
    db = Database(path)
    for name, state, enabled in (
        ("ambiguous", "stopped", False),
        ("recoverable", "stopped", True),
        ("active", "idle", True),
    ):
        db.create_session(
            name, name, None, ".", "local", state, 1,
            background_recovery_enabled=enabled,
        )
    db.close()
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE sessions DROP COLUMN cold_recovery_stop_token")
        conn.execute("UPDATE schema_version SET version=28")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="current schema"):
        Database(path, read_only=True)
    assert path.read_bytes() == before
    migrated = Database(path)
    try:
        assert SCHEMA_VERSION == 32
        assert migrated.execute_read("SELECT version FROM schema_version")[0][0] == 32
        # v28 stores no stop provenance: this token preserves ambiguity, not
        # evidence that the operator, rather than cold drain, stopped the row.
        token = migrated.get_session("ambiguous")["cold_recovery_stop_token"]
        assert isinstance(token, str) and token
        assert migrated.get_session("recoverable")["cold_recovery_stop_token"] is None
        assert migrated.get_session("active")["cold_recovery_stop_token"] is None
        assert migrated.get_session("ambiguous")["background_recovery_enabled"] == 0
    finally:
        migrated.close()
    again = Database(path)
    assert again.get_session("ambiguous")["cold_recovery_stop_token"] == token
    again.close()
    readonly = Database(path, read_only=True)
    adopted = open_existing_writable(readonly)
    try:
        assert adopted.get_session("ambiguous")["cold_recovery_stop_token"] == token
    finally:
        adopted.close()
        readonly.close()


def test_cold_stop_schema_current_version_missing_column_is_ensured(tmp_path: Path) -> None:
    path = tmp_path / "current.db"
    db = Database(path)
    db.create_session(
        "ambiguous", "ambiguous", None, ".", "local", "stopped", 1,
        background_recovery_enabled=False,
    )
    db.close()
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE sessions DROP COLUMN cold_recovery_stop_token")
    with pytest.raises(ValueError, match="writable preparation"):
        Database(path, read_only=True)
    repaired = Database(path)
    try:
        assert repaired.get_session("ambiguous")["cold_recovery_stop_token"]
    finally:
        repaired.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("automatic", [False, True])
async def test_cold_stop_fences_startup_and_heartbeat_after_rehydration(
    interrupted_successor: RecoveryFixture, automatic: bool,
) -> None:
    manager, session_id, transcript = interrupted_successor
    before = cold_outages.latest(manager.db, session_id)
    await manager.stop_session(session_id, force=True)
    token = _token(manager, session_id)
    assert token
    fresh = SessionManager(manager.db)
    fresh._generation_id = "after-explicit-stop"
    assert await cold_outages.recover(fresh, automatic=automatic) == 0
    assert _token(fresh, session_id) == token
    assert cold_outages.latest(manager.db, session_id) == before
    assert _boots(transcript) == 1
    assert _prompts(transcript) == []


@pytest.mark.asyncio
async def test_cold_stop_manual_resume_preserves_native_and_clears_captured_token(
    interrupted_successor: RecoveryFixture,
) -> None:
    manager, session_id, transcript = interrupted_successor
    await manager.stop_session(session_id, force=True)
    assert _token(manager, session_id)
    session = await manager.resume_session(session_id)
    if session._prompt_task is not None:
        await session._prompt_task
    assert session.acp_session_id == NATIVE
    assert _token(manager, session_id) is None
    assert cold_outages.latest(manager.db, session_id)["state"] == "reconnected"
    assert session.background_recovery_enabled
    prompts = _prompts(transcript)
    assert len(prompts) == 1
    assert prompts[0]["params"]["sessionId"] == NATIVE
    assert prompts[0]["params"]["prompt"][0]["text"] == cold_outages.RECONNECT_PROMPT


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["automatic", "manual", "manual-old-stop"])
async def test_cold_stop_during_admission_prevents_native_launch(
    interrupted_successor: RecoveryFixture,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    manager, session_id, transcript = interrupted_successor
    if entry == "manual-old-stop":
        await manager.stop_session(session_id, force=True)
    previous = _token(manager, session_id)
    entered, release = asyncio.Event(), asyncio.Event()
    refresh = manager._refresh_provider_target

    async def paused_refresh(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        return await refresh(*args, **kwargs)

    monkeypatch.setattr(manager, "_refresh_provider_target", paused_refresh)
    recovering = asyncio.create_task(
        cold_outages.recover(manager, automatic=True)
        if entry == "automatic" else manager.resume_session(session_id)
    )
    stopping = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        stopping = asyncio.create_task(manager.stop_session(session_id, force=True))
        await until(lambda: _token(manager, session_id) != previous)
        newer = _token(manager, session_id)
        assert newer
        assert not stopping.done()
        release.set()
        if entry == "automatic":
            assert await recovering == 0
        else:
            with pytest.raises(cold_outages.ColdRecoveryStoppedError):
                await recovering
        await stopping
        assert _token(manager, session_id) == newer
        assert cold_outages.latest(manager.db, session_id)["state"] == "interrupted"
        assert _boots(transcript) == 1
        assert _prompts(transcript) == []
    finally:
        release.set()
        await asyncio.gather(
            recovering, *([stopping] if stopping is not None else []),
            return_exceptions=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("preparation", ["workspace", "instructions"])
@pytest.mark.parametrize("entry", ["automatic", "manual", "manual-old-stop"])
async def test_cold_stop_during_launch_preparation_prevents_native_spawn(
    interrupted_successor: RecoveryFixture, monkeypatch: pytest.MonkeyPatch,
    preparation: str, entry: str,
) -> None:
    from agent_bridge import local_static_preparation, transport
    from agent_bridge.session_host import spawner

    manager, session_id, transcript = interrupted_successor
    if entry == "manual-old-stop":
        await manager.stop_session(session_id, force=True)
    previous = _token(manager, session_id)
    entered, release = asyncio.Event(), asyncio.Event()
    module, name = (
        (transport, "resolve_local_launch")
        if preparation == "workspace" else (local_static_preparation, "prepare_for_launch")
    )
    original = getattr(module, name)
    spawn = spawner.LocalSpawner.spawn
    spawned = []

    async def paused(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    async def observe_spawn(*args: Any, **kwargs: Any) -> Any:
        spawned.append(True)
        return await spawn(*args, **kwargs)

    monkeypatch.setattr(module, name, paused)
    monkeypatch.setattr(spawner.LocalSpawner, "spawn", observe_spawn)
    recovering = asyncio.create_task(
        cold_outages.recover(manager, automatic=True)
        if entry == "automatic" else manager.resume_session(session_id)
    )
    stopping = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert cold_outages.latest(manager.db, session_id)["recovery_phase"] == "launching"
        stopping = asyncio.create_task(manager.stop_session(session_id, force=True))
        await until(lambda: _token(manager, session_id) != previous)
        newer = _token(manager, session_id)
        assert newer and not stopping.done()
        release.set()
        if entry == "automatic":
            assert await recovering == 0
        else:
            with pytest.raises(cold_outages.ColdRecoveryStoppedError):
                await recovering
        await stopping
        assert _token(manager, session_id) == newer
        assert cold_outages.latest(manager.db, session_id)["recovery_phase"] == "launching"
        assert cold_outages.unsettled_launches(manager.db) == [session_id]
        assert spawned == []
        assert _boots(transcript) == 1
        assert _prompts(transcript) == []
    finally:
        release.set()
        await asyncio.gather(
            recovering, *([stopping] if stopping is not None else []),
            return_exceptions=True,
        )


@pytest.mark.asyncio
async def test_cold_stop_after_native_launch_prevents_reconnect(
    interrupted_successor: RecoveryFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, session_id, transcript = interrupted_successor
    entered, release = asyncio.Event(), asyncio.Event()
    connect = manager._connect_via_session_host

    async def paused_connect(*args: Any, **kwargs: Any) -> Any:
        result = await connect(*args, **kwargs)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(manager, "_connect_via_session_host", paused_connect)
    recovering = asyncio.create_task(cold_outages.recover(manager, automatic=True))
    stopping = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        stopping = asyncio.create_task(manager.stop_session(session_id, force=True))
        await until(lambda: _token(manager, session_id) is not None)
        release.set()
        assert await recovering == 0
        await stopping
        assert cold_outages.latest(manager.db, session_id)["state"] == "interrupted"
        assert _boots(transcript) == 2
        assert _prompts(transcript) == []
    finally:
        release.set()
        await asyncio.gather(
            recovering, *([stopping] if stopping is not None else []),
            return_exceptions=True,
        )


async def _assert_unknown_reconnect_is_not_replayed(
    interrupted_successor: RecoveryFixture, monkeypatch: pytest.MonkeyPatch,
    exit_race: bool,
) -> None:
    manager, session_id, transcript = interrupted_successor
    connect = manager._connect_via_session_host

    async def connect_without_confirmation(*args: Any, **kwargs: Any) -> Any:
        client, native = await connect(*args, **kwargs)
        send = client.send_prompt

        async def without_confirmation(*args: Any, **kwargs: Any) -> dict[str, Any]:
            await send(*args, **kwargs)
            return {"response_text": "", "stop_reason": None}

        monkeypatch.setattr(client, "send_prompt", without_confirmation)
        return client, native

    monkeypatch.setattr(manager, "_connect_via_session_host", connect_without_confirmation)
    session = await manager.resume_session(session_id)
    await session._prompt_task
    assert cold_outages.latest(manager.db, session_id)["state"] == "reconnect_unknown"
    await manager.stop_session(session_id, force=True)
    token = _token(manager, session_id)
    with monkeypatch.context() as observation:
        opened = []
        if exit_race:
            saved = json.loads(cold_outages.latest(manager.db, session_id)["replacement_json"])
            replacements = {
                (value["pid"], value["start_time"]) for value in saved["execution"].values()
            }
            observe = process_exit.observe_original_exit

            def before_exit(pid: int, start_time: str) -> bool:
                return False if (pid, start_time) in replacements else observe(pid, start_time)

            def exited_during_open(pid: int, start_time: str) -> None:
                assert (pid, start_time) in replacements
                opened.append(pid)
                raise ProcessLookupError("replacement exited during reference acquisition")

            observation.setattr(process_exit, "observe_original_exit", before_exit)
            observation.setattr(process_exit, "open_process", exited_during_open)
        with pytest.raises(ValueError):
            await manager.resume_session(session_id)
        if exit_race:
            assert len(opened) == 1
    assert _token(manager, session_id) == token
    assert cold_outages.latest(manager.db, session_id)["state"] == "reconnect_unknown"
    assert len(_prompts(transcript)) == 1


@pytest.mark.asyncio
async def test_cold_stop_unknown_reconnect_is_not_replayed_by_manual_resume(
    interrupted_successor: RecoveryFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _assert_unknown_reconnect_is_not_replayed(interrupted_successor, monkeypatch, False)


@pytest.mark.asyncio
async def test_cold_stop_replacement_exit_race_preserves_unknown_reconnect(
    interrupted_successor: RecoveryFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _assert_unknown_reconnect_is_not_replayed(interrupted_successor, monkeypatch, True)


@pytest.mark.asyncio
async def test_cold_stop_provider_completion_cannot_reenable_recovery(
    interrupted_successor: RecoveryFixture,
) -> None:
    manager, session_id, _transcript = interrupted_successor
    session = manager.get_session(session_id)
    row = cold_outages.latest(manager.db, session_id)
    manager.db.create_turn(
        session_id, 0, cold_outages.RECONNECT_PROMPT, 1,
        delivery_id="cold-outage:" + row["outage_id"],
    )
    manager.db.execute_write(
        "UPDATE cold_outages SET recovery_generation=?,recovery_pid=?,recovery_start_time=? "
        "WHERE outage_id=?",
        (manager._generation_id, os.getpid(), diagnostics.process_start_time(os.getpid()),
         row["outage_id"]),
    )
    await manager.stop_session(session_id, force=True)
    token = _token(manager, session_id)
    cold_outages.provider_result(manager, session, 0, {"stop_reason": "end_turn"})
    assert _token(manager, session_id) == token
    assert not session.background_recovery_enabled
    assert manager.db.get_session(session_id)["background_recovery_enabled"] == 0
    assert cold_outages.latest(manager.db, session_id)["state"] == "reconnected"
    assert await cold_outages.recover(manager, automatic=True) == 0


@pytest.mark.asyncio
async def test_cold_stop_busy_refusal_has_no_durable_intent(
    interrupted_successor: RecoveryFixture, mock_acp_client: MagicMock,
) -> None:
    manager, session_id, _transcript = interrupted_successor
    session = manager.get_session(session_id)
    session.client = mock_acp_client
    mock_acp_client.has_active_background_tasks = True
    mock_acp_client.active_background_tasks = ["busy-worker"]
    with pytest.raises(SessionBusyError):
        await manager.stop_session(session_id)
    assert _token(manager, session_id) is None
    session.client = None


@pytest.mark.asyncio
async def test_cold_stop_redeploy_detach_does_not_cancel_recovery(
    interrupted_successor: RecoveryFixture,
) -> None:
    manager, session_id, transcript = interrupted_successor
    await manager.stop_session(session_id, force=True, cancel_turn=False)
    assert _token(manager, session_id) is None
    assert await cold_outages.recover(manager, automatic=True) == 1
    session = manager.get_session(session_id)
    await session._prompt_task
    assert len(_prompts(transcript)) == 1


@pytest.mark.asyncio
async def test_cold_stop_inherited_automatic_origin_cannot_become_manual(
    interrupted_successor: RecoveryFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, session_id, transcript = interrupted_successor
    resume = manager.resume_session

    async def foreign_task_resume(*args: Any, **kwargs: Any) -> Any:
        manager.db.request_cold_recovery_stop(session_id)
        return await asyncio.create_task(resume(*args, **kwargs))

    monkeypatch.setattr(manager, "resume_session", foreign_task_resume)
    assert await cold_outages.recover(manager, automatic=True) == 0
    assert _token(manager, session_id)
    assert _boots(transcript) == 1
    assert _prompts(transcript) == []


def test_cold_stop_token_compare_and_set_cannot_clear_a_newer_stop(tmp_db: Database) -> None:
    tmp_db.create_session("session", "session", None, ".", "local", "idle", 1)
    first = tmp_db.request_cold_recovery_stop("session")
    second = tmp_db.request_cold_recovery_stop("session")
    assert first != second
    assert not tmp_db.clear_cold_recovery_stop("session", first)
    assert not tmp_db.clear_cold_recovery_stop("session", None)
    assert tmp_db.get_session("session")["cold_recovery_stop_token"] == second
    assert tmp_db.clear_cold_recovery_stop("session", second)
    assert tmp_db.get_session("session")["cold_recovery_stop_token"] is None


@pytest.mark.parametrize("enabled", [False, True])
def test_cold_stop_two_owner_conditional_enable_preserves_dormancy(
    tmp_db: Database, enabled: bool,
) -> None:
    tmp_db.create_session("session", "session", None, ".", "local", "idle", 1)
    manager = SessionManager(tmp_db)
    session = Session("session", "session", SpawnTarget(type="local", cwd="."))
    other = Database(tmp_db.db_path)
    try:
        first = tmp_db.request_cold_recovery_stop("session")
        assert tmp_db.clear_cold_recovery_stop("session", first)
        newer = other.request_cold_recovery_stop("session")
        manager._set_background_recovery_enabled(session, enabled)
        row = tmp_db.get_session("session")
        assert row["cold_recovery_stop_token"] == newer
        assert row["background_recovery_enabled"] == 0
        assert session.background_recovery_enabled is False
        assert not tmp_db.clear_cold_recovery_stop("session", first)
        assert not tmp_db.update_session_stopped("session", 2, True)
        row = tmp_db.get_session("session")
        assert row["status"] == "stopped"
        assert row["cold_recovery_stop_token"] == newer
        assert row["background_recovery_enabled"] == 0
    finally:
        other.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_cold_stop_setter_returns_actual_enabled_state_without_stop(
    tmp_db: Database, enabled: bool,
) -> None:
    tmp_db.create_session("session", "session", None, ".", "local", "idle", 1)
    manager = SessionManager(tmp_db)
    session = Session("session", "session", SpawnTarget(type="local", cwd="."))
    manager._set_background_recovery_enabled(session, enabled)
    assert session.background_recovery_enabled is enabled
    assert bool(tmp_db.get_session("session")["background_recovery_enabled"]) is enabled


def test_cold_stop_unsettled_launches_include_orphans_and_historical_rows(tmp_db: Database) -> None:
    for outage, session, phase in (
        ("historical", "retained", "launching"),
        ("latest", "retained", "registered"),
        ("orphan", "missing-session", "launching"),
    ):
        tmp_db.execute_write(
            "INSERT INTO cold_outages "
            "(outage_id,session_id,target_json,previous_status,owner_generation,"
            "execution_json,interrupted_turns_json,state,recovery_phase,created_at) "
            "VALUES (?,?,?,'running','previous','{}','[]','reconnected',?,1)",
            (outage, session, "{}", phase),
        )
    assert cold_outages.unsettled_launches(tmp_db) == ["retained", "missing-session"]
    legacy = SimpleNamespace(execute_read=lambda *_args: [])
    assert cold_outages.unsettled_launches(legacy) == []
