"""A routed frontend must not recover a session held by an older live frontend."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

from agent_procutil import no_window_flags
from fastapi import FastAPI
import pytest
from zdd import diagnostics, routing

from agent_bridge.db import Database
from agent_bridge.app_supersession import release_own_host_claims
from agent_bridge.passive_maintenance import passive_maintenance_ready, refresh_inherited_sessions
from agent_bridge.session_host.host_index import HostIndex, HostRecord
from agent_bridge.session_host.host_inventory import HostInventoryError
from agent_bridge.session_manager import SessionManager


@pytest.mark.parametrize("change", ["removed", "legacy", "partial"])
def test_stale_index_cannot_grant_inherited_claim(tmp_path: Path, change: str) -> None:
    path = tmp_path / "index.json"
    index = HostIndex(path)
    index.register(HostRecord("worker", 12000, 42, 43))
    data = json.loads(path.read_bytes())
    if change == "removed":
        del data["hosts"]["worker"]
    elif change == "legacy":
        del data["hosts"]["worker"]["owner_generation"]
        del data["hosts"]["worker"]["owner_pid"]
    else:
        data["hosts"]["worker"]["owner_generation"] = "previous"
    path.write_text(json.dumps(data))
    with pytest.raises(KeyError if change == "removed" else HostInventoryError):
        index.claim("worker", generation="successor", owner_pid=os.getpid(),
                    pid_alive=lambda _: False, require_owner_fields=True)
    assert json.loads(path.read_bytes()) == data


async def probe(root: Path, database: Path, sid: str, port: int, previous: routing.Endpoint) -> None:
    os.environ["AGENT_BRIDGE_CONFIG_DIR"] = str(root)
    endpoint = routing.Endpoint("127.0.0.1", port, os.getpid(),
                                process_start_time=diagnostics.process_start_time(os.getpid()))
    routing.routing_table_path(root).write_text(json.dumps({
        "active": endpoint.to_dict(), "previous": previous.to_dict(),
    }))
    db = Database(str(database))
    try:
        manager = SessionManager(db, read_only_rehydrate=True)
        inherited = manager.get_session(sid)
        assert inherited is not None
        inherited.awaiting_maintenance_refresh = True
        app = FastAPI()
        app.state.passive, app.state.bound_port, app.state.session_manager = True, port, manager
        # Observe both maintenance decisions without replacing the real owner's transport.
        reattach = AsyncMock(return_value=True)
        resync = AsyncMock(return_value=None)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(manager, "_reattach_one", reattach)
            patch.setattr(manager, "resync_session", resync)
            ready = await passive_maintenance_ready(app)
            await manager.recover_disconnected_hosts()
            await manager.reconcile_wedged_running()
        record = manager._host_index.get(sid)
        print(json.dumps({"endpoint": endpoint.to_dict(), "route_ready": ready,
                          "record_owner": record.owner_pid, "pending": inherited.awaiting_maintenance_refresh,
                          "reattach": reattach.await_count, "resync": resync.await_count}))
    finally:
        db.close()


@pytest.mark.asyncio
async def test_live_first_owner_outlasts_middle_generation(
    session_manager: SessionManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_cold_drain import cleanup, live_session, messages, until

    root = tmp_path / "routing"
    root.mkdir()
    try:
        held, transcript = await live_session(session_manager, tmp_path, monkeypatch)
        await session_manager.submit_prompt(held.session_id, "original work -- never replay")
        await until(lambda: any(m.get("method") == "session/prompt" for m in messages(transcript)))
        before = transcript.read_bytes()
        first = routing.Endpoint("127.0.0.1", 12001, os.getpid(),
                                 process_start_time=diagnostics.process_start_time(os.getpid()))
        previous = first.to_dict()
        outcomes = []
        for port in (12002, 12003):
            completed = await asyncio.to_thread(
                subprocess.run, [sys.executable, __file__, str(root), str(session_manager.db.db_path),
                                 held.session_id, str(port), json.dumps(previous)],
                check=True, text=True, capture_output=True, timeout=30, creationflags=no_window_flags(),
            )
            result = json.loads(completed.stdout.strip().splitlines()[-1])
            assert result["record_owner"] == first.pid
            outcomes.append(result)
            previous = result["endpoint"]
        assert transcript.read_bytes() == before and held.client is not None and held.client.is_running
        assert held._prompt_task is not None and not held._prompt_task.done()
        assert [x["route_ready"] for x in outcomes] == [False, True]
        assert [(x["reattach"], x["resync"]) for x in outcomes] == [(0, 0), (0, 0)], outcomes
        assert all(x["pending"] for x in outcomes)

        # A actually detaches, then releases. The successor adopts this same child.
        sid = held.session_id
        successor = SessionManager(session_manager.db, read_only_rehydrate=True)
        inherited = successor.get_session(sid)
        assert inherited is not None
        inherited.awaiting_maintenance_refresh = True
        initial = session_manager._host_index.get(sid)
        await session_manager.stop_session(sid, cancel_turn=False, allow_background_recovery=True)
        refresh_inherited_sessions(successor)
        assert inherited.awaiting_maintenance_refresh
        assert release_own_host_claims(session_manager) == [sid]
        refresh_inherited_sessions(successor)
        assert not inherited.awaiting_maintenance_refresh
        try:
            assert await successor.recover_disconnected_hosts() == 1
            current = successor._host_index.get(sid)
            assert current.owner_generation == successor._generation_id
            assert (current.host_pid, current.child_pid) == (initial.host_pid, initial.child_pid)
            assert inherited.client is not None and inherited.client.is_running
            assert inherited.acp_session_id == held.acp_session_id
            after = transcript.read_bytes()
            assert after.startswith(before)
            assert [json.loads(line).get("method") for line in after[len(before):].splitlines()] == ["initialize"]
        finally:
            if inherited.client is not None:
                await inherited.client.shutdown()
    finally:
        await cleanup(session_manager)


if __name__ == "__main__":
    previous_endpoint = routing.Endpoint.from_dict(json.loads(sys.argv[5]))
    assert previous_endpoint is not None
    asyncio.run(probe(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]), previous_endpoint))
