"""One synthetic child per case: real SDK/stdio/SQLite, no model conversation."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock

from agent_procutil import no_window_kwargs
import pytest
from zdd.diagnostics import process_start_time

from agent_bridge.acp_client import AcpClient
from agent_bridge.models import SessionStatus
from agent_bridge.runtime_custody import RuntimeProducer
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget
from test_runtime_custody import installation


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["reply", "drop-reply"])
async def test_native_turn_transport_real_stdio_independent_original_readback(
    session_manager: SessionManager, spawn_target: SpawnTarget, tmp_path: Path, mode: str,
) -> None:
    db = session_manager.db
    owner = installation(db)
    epoch = db.initialize_runtime_epoch(owner)
    producer = RuntimeProducer("synthetic-frontend", os.getpid(), process_start_time(os.getpid()))
    admission = db.enroll_runtime_producer(owner, epoch.epoch, producer)
    audit = tmp_path / "received.jsonl"
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-u", str(Path(__file__).parent / "fixtures" / "native_turn_peer.py"),
        str(db.db_path), str(audit), mode,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
    )
    client = AcpClient(preference_source="target-settings", target_preferences={
        "status": "resolved", "values": {"model": "fixture-model", "reasoning_effort": "high"},
        "sources": {"model": "launch-profile", "reasoning_effort": "launch-profile"},
    })
    session = Session("synthetic", "synthetic", spawn_target)
    try:
        await asyncio.wait_for(client.start(child), 5)
        native = await asyncio.wait_for(client.new_session(spawn_target.cwd), 5)
        db.create_session("synthetic", "synthetic", None, spawn_target.cwd, "local", "idle", 1.0)
        db.update_session_acp_id("synthetic", native)
        session.client, session.acp_session_id, session.status = client, native, SessionStatus.IDLE
        session_manager._sessions[session.session_id] = session
        session_manager._notify_host_reapable = AsyncMock()
        await session_manager.enroll_native_turn_transport("synthetic", owner, admission)
        await session_manager.submit_prompt("synthetic", "original synthetic prompt")
        try:
            await asyncio.wait_for(session._prompt_task, 3)
        except asyncio.TimeoutError:
            assert mode == "drop-reply"
        received = [json.loads(line) for line in audit.read_text().splitlines()]
        row = db.get_turn("synthetic", 0)
        assert received == [{"pid": child.pid, "handoff": json.loads(row["native_handoff_json"])["id"]}]
        assert row["native_consumed_at"] is None
        if mode == "reply":
            assert row["native_accepted_at"] is not None and row["native_outcome"] is None
        else:
            assert row["native_accepted_at"] is None and row["native_outcome"] == "unknown"
        assert len(db.get_turns("synthetic")) == 1
        assert db.runtime_custody_inventory(owner)["restore_qualified"] is False
    finally:
        if child.stdin is not None:
            child.stdin.close()
        await asyncio.wait_for(child.wait(), 5)
        await client.shutdown()
        if session._prompt_task is not None and not session._prompt_task.done():
            session._prompt_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await session._prompt_task
