"""Tests for the atomic ownership guard on the worktree-resume verb (#2879).

A worktree held by a *fresh* live interactive CLI must not be resumed as an
owned ACP session (that would spawn a second copilot child and the two would
contend). The guard is race-free enforcement at the bridge; ``reclaim=true``
(take-over) bypasses it, and a stale/expired live row never blocks a resume.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.db import LIVE_SESSION_STALE_SECONDS, Database
from agent_bridge.routes import worktrees


@pytest.fixture
def tmp_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def client(tmp_db: Database) -> TestClient:
    app = FastAPI()
    app.state.db = tmp_db
    # No session manager: the guard runs before any mgr access, and a bypassed
    # (reclaim) call with no session for the worktree resolves to a clean 404.
    app.state.session_manager = None
    app.include_router(worktrees.router)
    return TestClient(app)


def _register_live(db: Database, sid: str, worktree_id: str, now: float) -> None:
    db.register_live_session(
        sid, machine="lambda-core", cwd=None, worktree_id=worktree_id,
        repo=None, branch=None, pid=None, role=None, now=now,
    )


def test_resume_refused_when_fresh_live_cli_holds_worktree(
    client: TestClient, tmp_db: Database
) -> None:
    _register_live(tmp_db, "cli-1", "wt-a", time.time())
    r = client.post("/api/v1/worktrees/wt-a/resume")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["reason"] == "live_cli_holds_worktree"
    assert detail["worktree_id"] == "wt-a"
    assert detail["session_id"] == "cli-1"


def test_reclaim_bypasses_the_guard(
    client: TestClient, tmp_db: Database
) -> None:
    _register_live(tmp_db, "cli-1", "wt-a", time.time())
    # reclaim=true skips the guard; with no owned session it falls through to a
    # clean 404 (not the 409 refusal) -- proving the bypass.
    r = client.post("/api/v1/worktrees/wt-a/resume?reclaim=true")
    assert r.status_code == 404


def test_stale_live_row_does_not_block_resume(
    client: TestClient, tmp_db: Database
) -> None:
    old = time.time() - LIVE_SESSION_STALE_SECONDS - 10
    _register_live(tmp_db, "cli-dead", "wt-a", old)
    # a lapsed registration is not a holder -> guard passes, then 404 (no session)
    r = client.post("/api/v1/worktrees/wt-a/resume")
    assert r.status_code == 404


def test_no_live_row_allows_resume(client: TestClient) -> None:
    r = client.post("/api/v1/worktrees/wt-empty/resume")
    assert r.status_code == 404  # no holder, no session -> clean not-found
