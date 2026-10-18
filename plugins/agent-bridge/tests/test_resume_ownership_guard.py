"""Tests for the atomic ownership guard on the worktree-resume verb (#2879).

A worktree held by a *fresh* live interactive CLI must not be resumed as an
owned ACP session (that would spawn a second copilot child and the two would
contend). ``reclaim=true`` cannot bypass a live holder; a stale/expired live
row permits an exact native resume but never replacement history.
"""

from __future__ import annotations

import time
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.db import LIVE_SESSION_STALE_SECONDS, Database
from agent_bridge.routes import worktrees
from agent_bridge.models import SessionStatus
from agent_bridge.session_manager import SessionManager
from agent_bridge.transport import SpawnTarget


@pytest.fixture
def tmp_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def client(tmp_db: Database, tmp_path: Path, monkeypatch) -> TestClient:
    app = FastAPI()
    app.state.db = tmp_db
    manager = SessionManager(tmp_db)
    app.state.session_manager = manager
    path = str(tmp_path / "workspace")
    config = SimpleNamespace(project="example-project", host=None)
    app.state.resolver = SimpleNamespace(
        agents={"example": config},
        resolve=Mock(return_value=SpawnTarget(type="local", cwd=path, project=config.project)),
    )

    async def find(worktree_id, resolver, *, cache=None):
        return "example", worktrees._WorktreeEntry(
            id=worktree_id, agent_name="example", machine="example",
            path=path, branch="worktree/example", status="active",
        )

    async def owner(agent, config, resolver, args):
        worktree_id = args[args.index("--worktree-id") + 1] if "--worktree-id" in args else args[args.index("--worktree") + 1]
        if args[0] == "head-session":
            result = dict(
                tracked=True, worktree_id=worktree_id, head_session="native-original",
                state="active", head_revision=1, pending_handoffs=[],
            )
        elif args[0] == "record":
            result = {"ok": True, "worktree": dict(
                id=worktree_id, path=path, repo=config.project, status="active",
                last_session_id="native-original", bound_agent=None,
                execution_leg_opaque=False, session_backend_opaque=False,
            )}
        elif args[0] == "list-sessions":
            result = {"head_session": "native-original", "sessions": []}
        elif args[0] == "embody":
            result = dict(
                ok=True, dry_run=True, worktree_id=worktree_id, work_dir=path,
                resume_session="native-original", native_admission_version=1,
            )
        else:
            raise AssertionError(args)
        return json.dumps(result)

    async def load(session_id, **kwargs):
        session = manager.get_session(session_id)
        session.status = SessionStatus.IDLE
        return session

    monkeypatch.setattr(worktrees, "_find_cached_worktree_entry", find)
    monkeypatch.setattr(worktrees, "_run_for_agent", owner)
    monkeypatch.setattr("agent_bridge.worktree_native_resume._apply_bound_charter", lambda target, *args: target)
    manager.resume_session = AsyncMock(side_effect=load)
    manager.start_session = AsyncMock(side_effect=AssertionError("must not replace history"))
    app.include_router(worktrees.router)
    return TestClient(app)


def _register_live(db: Database, sid: str, worktree_id: str, now: float) -> None:
    db.register_live_session(
        sid, machine="anomalous-potato", cwd=None, worktree_id=worktree_id,
        repo=None, branch=None, pid=None, role=None, now=now,
    )


def test_resume_refused_when_fresh_live_cli_holds_worktree(
    client: TestClient, tmp_db: Database
) -> None:
    _register_live(tmp_db, "cli-1", "wt-a", time.time())
    r = client.post("/api/v1/worktrees/wt-a/resume")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["reason"] == "live_holder"
    client.app.state.session_manager.resume_session.assert_not_called()


def test_reclaim_cannot_bypass_live_holder(
    client: TestClient, tmp_db: Database
) -> None:
    _register_live(tmp_db, "cli-1", "wt-a", time.time())
    r = client.post("/api/v1/worktrees/wt-a/resume?reclaim=true")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "live_holder"
    client.app.state.session_manager.resume_session.assert_not_called()


def test_stale_live_row_does_not_block_resume(
    client: TestClient, tmp_db: Database
) -> None:
    old = time.time() - LIVE_SESSION_STALE_SECONDS - 10
    _register_live(tmp_db, "cli-dead", "wt-a", old)
    r = client.post("/api/v1/worktrees/wt-a/resume")
    assert r.status_code == 200
    assert r.json()["acp_session_id"] == "native-original"


def test_no_live_row_allows_resume(client: TestClient) -> None:
    r = client.post("/api/v1/worktrees/wt-empty/resume")
    assert r.status_code == 200
    assert r.json()["acp_session_id"] == "native-original"
