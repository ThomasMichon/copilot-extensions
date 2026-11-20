"""Cold host registration is a candidate, not an attached inspection client."""

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent_bridge.routes.sessions import router as sessions
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.worktree_native_resume import router as targets
from test_worktree_metadata import metadata_boundary as metadata_boundary
from test_worktree_native_resume import boundary as boundary


@pytest.mark.parametrize("source", ["record", "missing", "unknown-version"])
def test_host_candidate_does_not_authorize_session_status(
    metadata_boundary: Any, tmp_path: Path, source: str,
) -> None:
    b = metadata_boundary
    app = FastAPI()
    app.include_router(targets)
    app.include_router(sessions)
    for key, value in vars(b.request.app.state).items():
        setattr(app.state, key, value)
    index = tmp_path / "hosts.json"
    sid = "fixture-host"
    if source == "record":
        # A living PID in the original slot, but no host is listening. Never
        # probe arbitrary system PIDs or use this as a route to the agent.
        b.manager._host_index.register(HostRecord(sid, 1, os.getpid(), os.getpid()))
    elif source == "missing":
        index.unlink()
    else:
        index.write_text('{"version":2,"hosts":{}}', encoding="utf-8")
    before = index.read_bytes() if index.exists() else None
    with TestClient(app) as client:
        listing = client.get("/api/v1/targets")
        assert listing.status_code == 200
        result = listing.json()
        hosts = [item for item in result["targets"] if item["kind"] == "session_host"]
        if source == "record":
            assert [item["handle"] for item in hosts] == [sid]
            assert result["sources"]["session_host"]["availability"] == "available"
        else:
            assert not hosts
            assert result["sources"]["session_host"]["availability"] == "unavailable"
        status = client.get(f"/api/v1/sessions/{sid}/status")
        assert status.status_code == 404 and status.json() == {"detail": f"Session {sid} not found"}
    assert (index.read_bytes() if index.exists() else None) == before
    assert not b.calls and not b.manager.list_sessions() and not b.db.list_live_sessions(include_dead=True)
    assert not b.db.execute_read("SELECT * FROM turns")
    b.manager.start_session.assert_not_called()
    b.manager.resume_session.assert_not_called()
