"""Actual canonical commits drive bounded, token-free membership observations."""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agent_bridge import __main__ as cli
from agent_bridge import owner_membership as membership
from agent_bridge.app_routes import configure_serving_app
from agent_bridge.client import BridgeClient
from agent_bridge.models import ServiceConfig
from agent_bridge.owner_membership_cli import MembershipStream
from agent_bridge.owner_membership_contract import OwnerRead, validate
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION, OWNER_MEMBERSHIP_PROTOCOL_VERSION
from agent_bridge.relationship_authority import NAMESPACE, _subject, confirm, native_identity
from agent_bridge.routes.owner_membership import stream
from agent_bridge.session_observation_contract import ObservationError, decode, encode, position
from test_remote_acp_sessions import remote_manager as remote_manager
from test_remote_relationships import managed as managed

OWNER = "fixture-host/demo/parent-worktree"
SECRET = "protected-relationship-token-value-00000000000"


def create(db, sid="child", owner=OWNER, status="starting"):
    target = {
        "caller_owner_ref": owner,
        "project": "demo",
        "worktree_id": "child-worktree",
        "cwd": "/private/checkout",
        "env": {"PASSWORD": SECRET},
        "remote_acp": {"url": "https://private-account.invalid/private"},
    }
    db.create_session(
        sid,
        "fixture",
        None,
        "/private/checkout",
        "local",
        status,
        1.0,
        target_json=json.dumps(target),
    )


@pytest.fixture
def owner(session_manager, tmp_db):
    create(tmp_db)
    session_manager.start_session = AsyncMock(side_effect=AssertionError("observer started actor"))
    session_manager.resume_session = AsyncMock(
        side_effect=AssertionError("observer resumed actor")
    )
    session_manager._sessions["child"] = SimpleNamespace(subscriber_count=7)
    return session_manager, tmp_db, membership.source_for(session_manager, OWNER)


def test_actual_session_commits_publish_without_polling_or_duplicate_progress(owner):
    manager, db, source = owner
    initial = source.frame(OWNER, None)
    assert initial["completeness"] == "partial" and initial["retained"]["complete"] is False
    assert initial["members"][0]["link_qualification"] == "reported"
    assert initial["members"][0]["current_answerability"] == "unknown"
    db.update_session_status("child", "idle", 2.0)
    delta = source.frame(OWNER, initial["position"])
    assert delta["changes"][0]["member"]["state"] == "idle"
    assert delta["changes"][0]["evidence_kind"] == "observed-change"
    db.update_session_status("child", "idle", 3.0)
    assert source.frame(OWNER, delta["position"])["changes"] == []
    db.update_session_status("child", "ended", 4.0)
    retired = source.frame(OWNER, delta["position"])
    assert retired["changes"][-1]["kind"] == "session-retired"
    assert retired["changes"][-1]["grants_claim_release"] is False
    assert source.frame(OWNER, None)["members"] == []
    manager.start_session.assert_not_called()
    manager.resume_session.assert_not_called()
    assert manager._sessions["child"].subscriber_count == 7


def test_rejected_and_rolled_back_mutations_do_not_publish(owner):
    _manager, db, source = owner
    before = source.frame(OWNER, None)["position"]
    with pytest.raises(sqlite3.IntegrityError):
        create(db)
    with db._write_lock:
        conn = db._get_conn()
        conn.execute("UPDATE sessions SET status='failed' WHERE id='child'")
        conn.rollback()
    assert db.get_session("child")["status"] == "starting"
    assert source.frame(OWNER, before)["changes"] == []


def test_adopted_writable_connection_preserves_membership_commits(tmp_path):
    from agent_bridge.db import Database
    from agent_bridge.db_adoption import open_existing_writable

    original = Database(tmp_path / "adoption.db")
    create(original)
    original.close()
    passive = Database(tmp_path / "adoption.db", read_only=True)
    adopted = open_existing_writable(passive)
    try:
        manager = SimpleNamespace(db=adopted, _generation_id="adopted-fixture-generation")
        source = membership.source_for(manager, OWNER)
        before = source.frame(OWNER, None)["position"]
        adopted.update_session_status("child", "idle", 2.0)
        changes = source.frame(OWNER, before)["changes"]
        assert len(changes) == 1 and changes[0]["member"]["state"] == "idle"
        assert passive.read_only and passive.get_session("child")["status"] == "idle"
        assert adopted._writer_thread is None and passive._writer_thread is None
    finally:
        adopted.close()
        passive.close()


def test_active_membership_hook_preserves_runtime_full_sync_and_rejection(owner):
    from test_runtime_custody import close, enrolled

    _manager, db, source = owner
    installation, producer, admission = enrolled(db)
    before = source.frame(OWNER, None)["position"]
    synchronous = db._get_conn().execute("PRAGMA synchronous").fetchone()[0]
    target = json.dumps({
        "caller_owner_ref": OWNER, "project": "demo", "worktree_id": "admitted-child",
    })
    db.create_session(
        "admitted-child", "fixture", None, ".", "local", "idle", 2.0,
        target_json=target, runtime_admission=admission,
    )
    changes = source.frame(OWNER, before)["changes"]
    assert len(changes) == 1 and changes[0]["member"]["bridge_session_id"] == "admitted-child"
    assert db._get_conn().execute("PRAGMA synchronous").fetchone()[0] == synchronous
    close(db, installation, producer)
    before_refused = source.frame(OWNER, None)["position"]
    with pytest.raises(sqlite3.IntegrityError, match="admission_closed"):
        db.create_session(
            "refused-child", "fixture", None, ".", "local", "idle", 3.0,
            target_json=target, runtime_admission=admission,
        )
    assert db.get_session("refused-child") is None
    assert source.frame(OWNER, before_refused)["changes"] == []
    assert db._get_conn().execute("PRAGMA synchronous").fetchone()[0] == synchronous


@pytest.mark.asyncio
async def test_actual_managed_retirement_publishes_with_active_membership(managed):
    from agent_bridge import remote_relationships
    from test_remote_relationships import OWNER as remote_owner, create as create_remote

    manager, calls, _claims, _operations = managed
    created = await create_remote(manager)
    session = manager.get_session(created.session_id)
    source = membership.source_for(manager, remote_owner)
    before = source.frame(remote_owner, None)["position"]
    calls_before = list(calls)
    await remote_relationships.end(manager, session)
    changes = source.frame(remote_owner, before)["changes"]
    assert any(item["kind"] == "confirmed-relationship-retired" for item in changes)
    assert "coalesced-observed-changes" in source.frame(remote_owner, None)["limitations"]
    assert all(item["grants_claim_release"] is False for item in changes)
    assert manager.db.get_relationship(session.session_id)["phase"] == "closed"
    assert manager.get_session(session.session_id) is None
    assert calls == calls_before


def test_immutable_capture_and_record_disappearance_never_mint_retirement(owner):
    _manager, db, source = owner
    initial = source.frame(OWNER, None)
    initial["members"][0]["state"] = "caller-mutated"
    assert source.frame(OWNER, None)["members"][0]["state"] == "starting"
    before = initial["position"]
    db.delete_session("child")
    removed = source.frame(OWNER, before)["changes"][0]
    assert removed["kind"] == "record-disappeared"
    assert removed["operation"] == "tombstone" and removed["grants_claim_release"] is False


def test_generation_retention_and_source_fences(owner, monkeypatch):
    manager, db, source = owner
    before = source.frame(OWNER, None)["position"]
    monkeypatch.setattr(membership, "MAX_EVENTS", 2)
    for index in range(6):
        db.update_session_status("child", "idle" if index % 2 else "running", index + 2.0)
        source.frame(OWNER, None)
    with pytest.raises(ObservationError, match="retained range"):
        source.frame(OWNER, before)
    current = source.frame(OWNER, None)
    assert current["retained"]["floor"] > 1
    other_owner = {**current["source"], "owner_ref": "fixture-host/demo/foreign"}
    with pytest.raises(ObservationError):
        source.frame(OWNER, position(other_owner, current["retained"]["head"]))
    manager._generation_id = "replacement-generation"
    replacement = membership.source_for(manager, OWNER)
    assert source.closed and replacement is not source
    with pytest.raises(ObservationError, match="different source incarnation"):
        replacement.frame(OWNER, current["position"])


def test_optional_publication_failure_never_undoes_canonical_commit(owner, monkeypatch):
    manager, db, source = owner

    def failed(*_args, **_kwargs):
        raise RuntimeError("private diagnostic must not become a wire payload")

    monkeypatch.setattr(membership.reader, "snapshot", failed)
    db.update_session_status("child", "idle", 2.0)
    assert db.get_session("child")["status"] == "idle"
    with pytest.raises(ObservationError) as failed_capture:
        source.frame(OWNER, None)
    assert failed_capture.value.code == "publication-failed"
    assert "private diagnostic" not in str(failed_capture.value)
    with pytest.raises(ObservationError):
        membership.source_for(manager, OWNER)


def register(db, *, pid=123, started=1.0, now=2.0):
    return db.register_live_session(
        "represented",
        machine="fixture-host",
        cwd="/private/terminal",
        worktree_id="interactive-child",
        repo="example/repo",
        branch=None,
        pid=pid,
        role=None,
        now=now,
        process_started_at=started,
        venue=json.dumps(
            {
                "supervisor_ref": OWNER,
                "target": "private-account",
                "credential": SECRET,
                "path": "/private/terminal",
            }
        ),
    )


def test_represented_registration_expiry_takeover_and_late_deregister(owner):
    _manager, db, source = owner
    before = source.frame(OWNER, None)["position"]
    assert register(db) == "live"
    new = source.frame(OWNER, before)
    represented = new["changes"][-1]["member"]
    assert represented["registry"] == "represented"
    assert represented["link_qualification"] == "reported"
    assert db.deregister_live_session("represented", pid=123, process_started_at=1.0)
    removed = source.frame(OWNER, new["position"])
    assert removed["changes"][-1]["kind"] == "registration-disappeared"
    assert register(db, pid=456, started=2.0, now=3.0) == "live"
    current = source.frame(OWNER, None)
    assert not db.deregister_live_session("represented", pid=123, process_started_at=1.0)
    assert source.frame(OWNER, current["position"])["changes"] == []
    db.expire_live_sessions_for_worktree(
        "interactive-child",
        now=4.0,
        expected_session_id="represented",
    )
    changed = source.frame(OWNER, current["position"])["changes"][-1]
    assert changed["kind"] == "registration-taken-over"
    assert changed["grants_claim_release"] is False


def claim(db, *, phase="bound"):
    row = db.get_relationship("remote")
    owner = row["owner"]
    producer = {
        "version": 2,
        "lifetime": "retained-relationship",
        "namespace": NAMESPACE,
        "creator": owner,
        "request_id": row["id"],
        "recovery": row["recovery"],
        "token": "t" * 43,
        "phase": phase,
        "identity": native_identity("remote-native"),
    }
    reference = _subject(producer)
    if phase == "closed":
        producer["closeout"] = {
            "available": True,
            "exists": True,
            "relationship_verification": {
                "version": 1,
                "subject": reference,
                "retired": True,
                "quiescent": True,
            },
        }
    result = {
        "ok": True,
        "owner": owner,
        "creator": owner,
        "request_id": row["id"],
        "token": producer["token"],
        "phase": phase,
        "producer": producer,
        "ref": reference,
    }
    return confirm(
        result,
        owner=owner,
        request_id=row["id"],
        recovery=row["recovery"],
        identity=native_identity("remote-native"),
        closed=phase == "closed",
    )


def test_managed_relationship_phases_and_protected_receipts(owner):
    _manager, db, source = owner
    initial = source.frame(OWNER, None)
    db.create_relationship(
        "remote",
        OWNER,
        {"project": "demo", "cwd": "/private/remote", "target": {"credential": SECRET}},
        None,
        name="fixture",
        agent_name="fixture",
        caller_id=None,
        caller_session_id=None,
        target_json=json.dumps({"caller_owner_ref": OWNER}),
    )
    db.save_relationship_claim("remote", {"pending": True}, "reserved")
    db.enter_relationship_attempt("remote")
    db.save_relationship_native_id("remote", "remote-native")
    db.save_relationship_claim("remote", claim(db), "bound")
    bound = source.frame(OWNER, None)
    remote = next(item for item in bound["members"] if item["registry"] == "relationship")
    assert remote["relationship_qualification"] == "host-confirmed"
    before_retire = bound["position"]
    db.retire_relationship("remote")
    local = source.frame(OWNER, before_retire)
    assert local["changes"][-1]["kind"] == "local-admission-retired"
    assert local["changes"][-1]["operation"] == "upsert"
    db.save_relationship_claim("remote", claim(db, phase="closed"), "closed")
    closed = source.frame(OWNER, local["position"])
    assert closed["changes"][-1]["kind"] == "confirmed-relationship-retired"
    assert closed["changes"][-1]["operation"] == "tombstone"
    payload = encode(source.frame(OWNER, initial["position"])).decode()
    for forbidden in (
        SECRET,
        "t" * 43,
        "private-account",
        "/private/",
        "claim_json",
        NAMESPACE + ":l1.",
    ):
        assert forbidden not in payload


def test_unreserved_closure_is_not_confirmed_relationship_retirement(owner):
    _manager, db, source = owner
    db.create_relationship(
        "unreserved",
        OWNER,
        {"project": "demo", "cwd": "/private/fixture"},
        None,
        name="fixture",
        agent_name="fixture",
        caller_id=None,
        caller_session_id=None,
        target_json=json.dumps({"caller_owner_ref": OWNER}),
    )
    db.retire_relationship("unreserved")
    before = source.frame(OWNER, None)["position"]
    row = db.get_relationship("unreserved")
    db.save_relationship_absence(
        "unreserved",
        {
            "schema": "copilot-extensions.resource-relationship",
            "version": 1,
            "ok": True,
            "owner": OWNER,
            "request_id": row["id"],
            "absent": True,
        },
    )
    change = source.frame(OWNER, before)["changes"][-1]
    assert change["kind"] == "unreserved-relationship-closed"
    assert change["operation"] == "tombstone" and change["grants_claim_release"] is False


def test_bootstrap_limit_is_explicit_and_never_looks_complete(session_manager, tmp_db):
    for index in range(65):
        create(tmp_db, sid=f"child-{index}")
    with pytest.raises(ObservationError, match="bounded snapshot"):
        membership.source_for(session_manager, OWNER)
    assert tmp_db.get_session("child-64") is not None


def test_sequence_exhaustion_preserves_commit_but_withholds_projection(owner):
    _manager, db, source = owner
    source._sequence = membership.MAX_SEQUENCE
    db.update_session_status("child", "idle", 2.0)
    assert db.get_session("child")["status"] == "idle"
    with pytest.raises(ObservationError) as failed:
        source.frame(OWNER, None)
    assert failed.value.code == "sequence-exhausted"


@pytest.mark.parametrize(
    "pairs",
    [
        [("owner_ref", OWNER), ("contract_version", "true")],
        [("owner_ref", OWNER), ("contract_version", "2")],
        [("owner_ref", OWNER), ("contract_version", "1"), ("contract_version", "1")],
        [("owner_ref", OWNER), ("contract_version", "1"), ("caller", "trusted")],
        [("owner_ref", "federated:arbitrary-handle"), ("contract_version", "1")],
    ],
)
def test_strict_membership_request_cannot_mint_admission(pairs):
    with pytest.raises(ObservationError):
        OwnerRead.parse(pairs)


def test_wire_rejects_protected_dto_fields_and_wrong_positions(owner):
    _manager, _db, source = owner
    body = source.frame(OWNER, None)
    body["members"][0]["producer"] = {"token": SECRET}
    with pytest.raises(ObservationError, match="membership fact"):
        validate(body, OwnerRead(OWNER))
    body = source.frame(OWNER, None)
    body["position"] = position({**body["source"], "hosting_generation": "foreign"}, 0)
    with pytest.raises(ObservationError):
        validate(body, OwnerRead(OWNER))


@pytest.mark.asyncio
@pytest.mark.parametrize("heartbeat", [False, True])
async def test_real_stream_splices_after_unrelated_owner_changes(owner, monkeypatch, heartbeat):
    from agent_bridge.routes import owner_membership as route

    manager, db, source = owner
    other_owner = "fixture-host/demo/other-owner"
    create(db, sid="other-child", owner=other_owner)
    membership.source_for(manager, other_owner)
    clock = [0.0]
    monkeypatch.setattr(route, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    waits = []

    async def advance(_owner, after):
        waits.append(after)
        if len(waits) == 1:
            db.update_session_status("other-child", "idle", 2.0)
        elif len(waits) == 2 and heartbeat:
            clock[0] = 6.0
        else:
            db.update_session_status("child", "idle", 3.0)

    monkeypatch.setattr(source, "wait", advance)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_manager=manager)),
        is_disconnected=AsyncMock(return_value=False),
    )
    frames = stream(request, OwnerRead(OWNER, True))
    try:
        initial = decode((await anext(frames)).split(b"data: ", 1)[1].strip())
        latest = initial
        following = decode((await anext(frames)).split(b"data: ", 1)[1].strip())
        if heartbeat:
            assert following["kind"] == "heartbeat"
            assert following["position"] != initial["position"]
            latest = following
            following = decode((await anext(frames)).split(b"data: ", 1)[1].strip())
        assert following["kind"] == "delta"
        assert following["base_position"] == latest["position"]
        assert [change["member"]["bridge_session_id"] for change in following["changes"]] == [
            "child"
        ]
        assert waits[1] != initial["position"], "wait on scanned, not delivered, progress"
        resumed = stream(request, OwnerRead(OWNER, after=latest["position"]))
        try:
            replay = decode((await anext(resumed)).split(b"data: ", 1)[1].strip())
            assert replay["base_position"] == latest["position"]
            assert replay["changes"] == following["changes"]
        finally:
            await resumed.aclose()
    finally:
        await frames.aclose()
    assert source._scopes[OWNER].readers == 0


@pytest.mark.asyncio
async def test_completed_readers_reclaim_inactive_scopes_and_old_resume_gaps(
    session_manager, tmp_db,
):
    owners = [f"fixture-host/demo/sequential-{index}" for index in range(20)]
    for index, selected in enumerate(owners):
        create(tmp_db, sid=f"sequential-child-{index}", owner=selected)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_manager=session_manager)),
        is_disconnected=AsyncMock(return_value=False),
    )
    original = None
    for selected in owners:
        frames = stream(request, OwnerRead(selected))
        try:
            body = decode((await anext(frames)).split(b"data: ", 1)[1].strip())
            assert body["kind"] == "snapshot"
            if original is None:
                original = body["position"]
            with pytest.raises(StopAsyncIteration):
                await anext(frames)
        finally:
            await frames.aclose()
    source = tmp_db._owner_membership_source
    assert len(source._scopes) == membership.MAX_OWNERS
    assert not source._events, "admission barriers must not mint membership changes"
    assert all(scope.readers == 0 and not scope.waiters for scope in source._scopes.values())
    assert not source.has_owner(owners[0])
    resumed = stream(request, OwnerRead(owners[0], after=original))
    try:
        body = decode((await anext(resumed)).split(b"data: ", 1)[1].strip())
        assert body["kind"] == "gap" and body["code"] == "retention-gap"
        assert body["reconcile"] == "new-snapshot"
    finally:
        await resumed.aclose()
    assert all(scope.readers == 0 for scope in source._scopes.values())
    fresh = source.frame(owners[0], None)
    assert fresh["position"] != original
    assert fresh["retained"]["floor"] > 1 and fresh["completeness"] == "partial"
    assert not fresh["changes"] and not source._events


def test_source_binding_acquisition_race_reclaims_without_losing_reader(session_manager, tmp_db):
    owners = [OWNER] + [f"fixture-host/demo/raced-{index}" for index in range(16)]
    for index, selected in enumerate(owners):
        create(tmp_db, sid=f"raced-child-{index}", owner=selected)
    source = membership.source_for(session_manager, OWNER)
    original = source.frame(OWNER, None)["position"]
    for selected in owners[1:16]:
        membership.source_for(session_manager, selected)
    inspected, resume = threading.Event(), threading.Event()
    original_has_owner = source.has_owner

    def held_selection(selected):
        result = original_has_owner(selected)
        if selected == OWNER:
            inspected.set()
            assert resume.wait(5), "binding race was not released"
        return result

    source.has_owner = held_selection
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            acquiring = executor.submit(membership.source_for, session_manager, OWNER, acquire=True)
            try:
                assert inspected.wait(5)
                membership.source_for(session_manager, owners[16])
                assert not original_has_owner(OWNER)
            finally:
                resume.set()
            assert acquiring.result(timeout=5) is source
        assert source._scopes[OWNER].readers == 1
        with pytest.raises(ObservationError) as gap:
            source.frame(OWNER, original)
        assert gap.value.code == "retention-gap"
        assert source.frame(OWNER, None)["members"]
        assert len(source._scopes) == membership.MAX_OWNERS and not source._events
    finally:
        resume.set()
        source.has_owner = original_has_owner
        scope = source._scopes.get(OWNER)
        if scope is not None and scope.readers:
            source.release(OWNER)
    assert all(scope.readers == 0 and not scope.waiters for scope in source._scopes.values())


@pytest.mark.asyncio
async def test_active_scope_limit_and_shared_reader_cancellation_isolation(session_manager, tmp_db):
    owners = [f"fixture-host/demo/active-{index}" for index in range(17)]
    for index, selected in enumerate(owners):
        create(tmp_db, sid=f"active-child-{index}", owner=selected)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_manager=session_manager)),
        is_disconnected=AsyncMock(return_value=False),
    )
    readers = []
    positions = {}
    try:
        for selected in owners[:16]:
            frames = stream(request, OwnerRead(selected, True))
            readers.append(frames)
            body = decode((await anext(frames)).split(b"data: ", 1)[1].strip())
            assert body["kind"] == "snapshot"
            positions[selected] = body["position"]
        source = tmp_db._owner_membership_source
        shared = stream(request, OwnerRead(owners[0], True))
        readers.append(shared)
        await anext(shared)
        assert source._scopes[owners[0]].readers == 2
        await shared.aclose()
        assert source._scopes[owners[0]].readers == 1
        refused = stream(request, OwnerRead(owners[16], True))
        readers.append(refused)
        assert b'"code":"membership-overflow"' in await anext(refused)
        await refused.aclose()
        assert all(scope.readers == 1 for scope in source._scopes.values())
        await readers[0].aclose()
        admitted = stream(request, OwnerRead(owners[16], True))
        readers.append(admitted)
        assert b'"kind":"snapshot"' in await anext(admitted)
        assert not source.has_owner(owners[0])
        assert len(source._scopes) == membership.MAX_OWNERS
        retained = source.frame(owners[1], positions[owners[1]])
        assert retained["kind"] == "delta" and retained["changes"] == []
        assert retained["position"] != positions[owners[1]] and not retained["has_more"]
    finally:
        for frames in readers:
            await frames.aclose()
    assert all(scope.readers == 0 and not scope.waiters for scope in source._scopes.values())


@pytest.mark.asyncio
async def test_two_membership_observers_do_not_pin_or_stop_actors(owner):
    manager, db, source = owner
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_manager=manager)),
        is_disconnected=AsyncMock(return_value=False),
    )
    first, second = (
        stream(request, OwnerRead(OWNER, True)),
        stream(request, OwnerRead(OWNER, True)),
    )
    await anext(first)
    await anext(second)
    assert source._scopes[OWNER].readers == 2
    pending = asyncio.create_task(anext(first))
    other = asyncio.create_task(anext(second))
    try:
        for _ in range(50):
            if len(source._scopes[OWNER].waiters) == 2:
                break
            await asyncio.sleep(0)
        assert len(source._scopes[OWNER].waiters) == 2
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert len(source._scopes[OWNER].waiters) == 1
        assert source._scopes[OWNER].readers == 1
        db.update_session_status("child", "idle", 2.0)
        delivered = decode((await asyncio.wait_for(other, 1)).split(b"data: ", 1)[1].strip())
        assert delivered["kind"] == "delta"
        assert manager._sessions["child"].subscriber_count == 7
    finally:
        for task in (pending, other):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await first.aclose()
        await second.aclose()
    assert source._scopes[OWNER].waiters == []
    assert source._scopes[OWNER].readers == 0
    manager.start_session.assert_not_called()


def test_actual_http_and_root_cli_remain_passive_and_strict(owner, monkeypatch, capsys):
    manager, db, _source = owner
    app = configure_serving_app(
        ServiceConfig(db_path=str(db.db_path)), "fixture-token", lifespan=None
    )
    app.state.session_manager = manager
    app.state.db = db
    scheme = "Bear" + "er "
    with TestClient(app, headers={"Authorization": scheme + "fixture-token"}) as api:
        query = {"owner_ref": OWNER, "contract_version": "1"}
        response = api.get("/api/v1/owner-observations", params=query)
        assert response.status_code == 200
        body = decode(response.content.split(b"data: ", 1)[1].strip())
        validate(body, OwnerRead(OWNER))
        health = api.get("/health").json()
        assert health["protocol_version"] == HTTP_PROTOCOL_VERSION
        assert health["protocol_version"] >= OWNER_MEMBERSHIP_PROTOCOL_VERSION
        assert type(health["capabilities"][membership.CAPABILITY]) is int
        assert health["capabilities"][membership.CAPABILITY] == 1
        for extra in ({"contract_version": "true"}, {"after": "foreign"}, {"action": "write"}):
            result = api.get("/api/v1/owner-observations", params={**query, **extra})
            assert result.status_code == 422 or b'"kind":"error"' in result.content
        unknown = api.get(
            "/api/v1/owner-observations",
            params={**query, "owner_ref": "fixture-host/demo/unknown"},
        )
        assert b'"code":"source-unavailable"' in unknown.content
        client = BridgeClient("http://fixture.invalid", "synthetic-token", connect_grace=0)
        monkeypatch.setattr(
            client,
            "health",
            lambda: health,
        )
        assert client.daemon_supports(OWNER_MEMBERSHIP_PROTOCOL_VERSION)
        monkeypatch.setattr(
            "urllib.request.urlopen", lambda *_args, **_kwargs: io.BytesIO(response.content)
        )
        calls = []

        def get_client(*, ensure):
            calls.append(ensure)
            return client

        monkeypatch.setattr(cli, "_get_client", get_client)
        previous = cli._PROJECT_OVERRIDE
        try:
            cli.main(
                [
                    "--proj=demo",
                    "observe-owner",
                    "--owner-ref",
                    OWNER,
                    "--contract-version",
                    "1",
                    "--json",
                ]
            )
        finally:
            cli._set_project_override(previous)
        assert decode(capsys.readouterr().out.encode())["kind"] == "snapshot"
        assert calls == [False]
        api.headers.pop("Authorization")
        assert api.get("/api/v1/owner-observations", params=query).status_code == 401


@pytest.mark.parametrize("generation,version", [
    (51, None), (52, None), (52, True), (52, "1"), (52, 0), (52, 2),
])
def test_unsupported_owner_receiver_does_not_open_an_event_stream(
    generation, version, monkeypatch,
):
    from agent_bridge.owner_membership_cli import read

    client = BridgeClient("http://fixture.invalid", "synthetic-token", connect_grace=0)
    monkeypatch.setattr(
        client, "health",
        lambda: {
            "protocol_version": generation, "min_protocol_version": 1,
            "capabilities": {} if version is None else {membership.CAPABILITY: version},
        },
    )
    monkeypatch.setattr(
        client, "_stream_sse",
        lambda *_args, **_kwargs: pytest.fail("opened unsupported stream"),
    )
    assert client.daemon_supports(OWNER_MEMBERSHIP_PROTOCOL_VERSION) is (generation >= 52)
    with pytest.raises(ObservationError, match="does not support"):
        read(client, OwnerRead(OWNER))


def test_owner_stream_is_bounded_and_empty_eof_stays_failure():
    response = io.BytesIO(b"x" * (35 * 1024 + 1))
    reader = MembershipStream(response)
    with pytest.raises(ObservationError):
        next(reader)
    assert response.closed
