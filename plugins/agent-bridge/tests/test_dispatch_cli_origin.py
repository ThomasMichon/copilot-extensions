"""CLI birth attribution is transactional, conversation-bound and never backfilled."""

from __future__ import annotations

from copy import deepcopy
import json
import sqlite3
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.acquisition_contract import AcquisitionRefused, AcquisitionRequest, cli_driver
from agent_bridge.acquisition_store import AcquisitionStore
from agent_bridge.db import Database
from agent_bridge.dispatch_cli_origin import inspect
from agent_bridge.routes.live_sessions import router
from test_acquisition_runtime import req, rig as _rig
from test_dispatch_session_origin import origin

rig = _rig
pytestmark = pytest.mark.contract("agent_bridge.dispatch_cli_session_origin")


def register(db: Database, sid: str, *, driver: str | None = None, pid: int = 100) -> str:
    return db.register_live_session(
        sid, machine="worker", cwd="/workspace", worktree_id="worktree-a",
        repo="example/project", branch="dev", pid=pid, role="worker",
        process_started_at=float(pid), driven_by=driver, now=time.time(),
    )


def launching(db: Database, **changes) -> tuple[AcquisitionRequest, AcquisitionStore]:
    request = req(**{"mode": "cli", "dispatch_origin": origin(), **changes})
    store = AcquisitionStore(db)
    store.journal.claim(request)
    store.gate(request, None, None)
    store.stage(
        request, "launching", acquired="created", creation_authorized=True,
        session_id=None, native_session_id=None,
    )
    db.create_cli_mode_reservation(request.worktree_id, now=time.time())
    return request, store


@pytest.mark.asyncio
async def test_connected_cli_creation_persists_before_delivery_and_replays(rig):
    request = req(mode="cli", dispatch_origin=origin())
    result = await rig.runtime.acquire(request)
    assert result.acquired == "created"
    assert inspect(rig.db.get_live_session(result.native_session_id)) == origin()
    assert await rig.runtime.acquire(request) == result
    data = rig.db.execute_read(
        "SELECT data_json FROM acquisition_effects WHERE delivery_id=?", (request.delivery_id,),
    )[0]["data_json"]
    assert json.loads(data)["_request"]["dispatch_origin"] == origin()
    changed = deepcopy(origin())
    changed["cohort"]["pool"]["registration_id"] = "new-pool"
    with pytest.raises(AcquisitionRefused):
        await rig.runtime.acquire(req(mode="cli", dispatch_origin=changed))
    assert len([args for args, _ in rig.calls if args[0] == "embody" and "--dry-run" not in args]) == 1


@pytest.mark.asyncio
async def test_unconfirmed_first_save_keeps_pending_effect_without_prompt_or_relaunch(rig, monkeypatch):
    from agent_bridge import dispatch_cli_origin

    monkeypatch.setattr(dispatch_cli_origin, "first_save", lambda *args, **kwargs: None)
    with pytest.raises(AcquisitionRefused, match="original assignment") as refused:
        await rig.runtime.acquire(req(mode="cli", dispatch_origin=origin()))
    assert refused.value.code == "cli_origin_unconfirmed"
    assert rig.db.execute_read("SELECT state FROM acquisition_attempts")[0]["state"] == "pending"
    assert not rig.db.execute_read("SELECT 1 FROM live_messages")
    assert len(rig.db.list_live_sessions()) == 1


def test_original_snapshot_survives_restart_heartbeat_alias_and_mutable_targeting(tmp_db):
    request, store = launching(tmp_db)
    assert register(tmp_db, "provisional", driver=cli_driver(request)) == "live"
    assert inspect(tmp_db.get_live_session("provisional")) == origin()
    store.release_gate(request)
    assert register(tmp_db, "native", driver="another-task") == "live"
    assert tmp_db.resolve_live_session_id("provisional") == "native"
    assert inspect(tmp_db.get_live_session("native")) == origin()
    assert register(tmp_db, "provisional", driver="later-driver") == "live"
    path = tmp_db.db_path
    tmp_db.close()
    cold = Database(path)
    try:
        assert inspect(cold.get_live_session("native")) == origin()
        app = FastAPI()
        app.state.db = cold
        app.include_router(router)
        with TestClient(app) as client:
            response = client.get("/api/v1/live-sessions/native")
            assert response.status_code == 200, response.text
            assert response.json()["dispatch_origin"] == origin()
            rows = client.get("/api/v1/live-sessions").json()["live_sessions"]
            assert len(rows) == 1 and rows[0]["dispatch_origin"] == origin()
    finally:
        cold.close()


@pytest.mark.parametrize("stamped", [False, True])
def test_resume_into_existing_conversation_preserves_its_stamp_or_absence(tmp_db, stamped):
    if stamped:
        request, store = launching(tmp_db)
        assert register(tmp_db, "original", driver=cli_driver(request), pid=10) == "live"
        store.release_gate(request)
        store.journal.refuse(request, store.recovery(request)[0], "fixture_settled")
        tmp_db.release_cli_mode_reservation("worktree-a")
    else:
        assert register(tmp_db, "original", pid=10) == "live"
    tmp_db.execute_write("UPDATE live_sessions SET status='expired' WHERE session_id='original'")
    changed = deepcopy(origin())
    changed["cohort"]["pool"]["registration_id"] = "later-pool"
    request, store = launching(tmp_db, delivery_id="delivery-b", dispatch_origin=changed)
    assert register(tmp_db, "provisional", driver=cli_driver(request), pid=20) == "live"
    store.release_gate(request)
    assert register(tmp_db, "original", driver=cli_driver(request), pid=20) == "live"
    assert inspect(tmp_db.get_live_session("original")) == (origin() if stamped else None)
    assert tmp_db.resolve_live_session_id("provisional") == "original"


def test_resume_gate_never_stamps_provisional_or_legacy_identity(tmp_db):
    request = req(mode="cli", dispatch_origin=origin())
    store = AcquisitionStore(tmp_db)
    store.journal.claim(request)
    store.gate(request, "original", "original")
    store.stage(
        request, "launching", acquired="resumed", creation_authorized=False,
        session_id="original", native_session_id="original",
    )
    tmp_db.create_cli_mode_reservation("worktree-a", now=time.time())
    assert register(tmp_db, "provisional", driver=cli_driver(request)) == "live"
    assert register(tmp_db, "original", driver=cli_driver(request)) == "live"
    assert inspect(tmp_db.get_live_session("original")) is None


@pytest.mark.parametrize("field,value", [
    ("acquired", "resumed"), ("creation_authorized", False),
    ("native_session_id", "foreign"),
])
def test_incoherent_origin_refuses_registration_without_claiming_reservation(tmp_db, field, value):
    request, _store = launching(tmp_db)
    row = tmp_db.execute_read("SELECT data_json FROM acquisition_effects")[0]
    data = json.loads(row["data_json"])
    data[field] = value
    tmp_db.execute_write("UPDATE acquisition_effects SET data_json=?", (json.dumps(data),))
    with pytest.raises(ValueError, match="original creation"):
        register(tmp_db, "native", driver=cli_driver(request))
    assert tmp_db.get_live_session("native") is None
    assert tmp_db.get_cli_mode_reservation("worktree-a")["claimed_by_session_id"] is None


def test_foreign_driver_cannot_claim_origin_and_failed_fold_rolls_back_first_save(tmp_db):
    request, _store = launching(tmp_db)
    with pytest.raises(sqlite3.IntegrityError, match="fence"):
        register(tmp_db, "foreign", driver="foreign")
    conn = tmp_db._get_conn()
    conn.execute(
        "CREATE TRIGGER reject_claim BEFORE UPDATE ON cli_mode_reservations "
        "BEGIN SELECT RAISE(ABORT,'test rollback'); END",
    )
    with pytest.raises(sqlite3.IntegrityError, match="test rollback"):
        register(tmp_db, "native", driver=cli_driver(request))
    assert tmp_db.get_live_session("native") is None
    assert tmp_db.get_cli_mode_reservation("worktree-a")["claimed_by_session_id"] is None


def test_legacy_and_opaque_storage_remain_unattributed(tmp_db):
    tmp_db.create_cli_mode_reservation("worktree-a", now=time.time())
    assert register(tmp_db, "legacy") == "live"
    raw = '{"version":99,"future":"opaque"}'
    tmp_db.execute_write(
        "UPDATE live_sessions SET dispatch_origin_json=? WHERE session_id='legacy'", (raw,),
    )
    assert register(tmp_db, "renamed") == "live"
    assert inspect(tmp_db.get_live_session("renamed")) is None
    assert tmp_db.get_live_session("renamed")["dispatch_origin_json"] == raw


def test_schema45_upgrade_preserves_legacy_absence_and_requires_writable_preparation(tmp_path):
    path = tmp_path / "bridge.db"
    db = Database(path)
    assert register(db, "legacy") == "live"
    db.execute_write("ALTER TABLE live_sessions DROP COLUMN dispatch_origin_json")
    db.execute_write("UPDATE schema_version SET version=45")
    db.close()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="writable preparation"):
        Database(path, read_only=True)
    assert path.read_bytes() == before
    upgraded = Database(path)
    try:
        assert upgraded.get_live_session("legacy")["dispatch_origin_json"] is None
        assert upgraded.execute_read("SELECT version FROM schema_version")[0]["version"] == 46
    finally:
        upgraded.close()
    passive = Database(path, read_only=True)
    passive.close()


@pytest.mark.parametrize("read_only", [False, True])
def test_current_schema_without_origin_storage_is_not_silently_repaired(tmp_path, read_only):
    path = tmp_path / "bridge.db"
    db = Database(path)
    db.execute_write("ALTER TABLE live_sessions DROP COLUMN dispatch_origin_json")
    db.close()
    with pytest.raises(ValueError, match="writable preparation"):
        Database(path, read_only=read_only)
