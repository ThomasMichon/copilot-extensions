"""Tests for Phase 3 of the agent-bridge-unified-zdd-cutover effort:
generation-scoped claims wired into the startup reattach scan, the exit
contract's claim release at shutdown, and the post-cutover reattach retry
endpoint.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_bridge.db import Database
from agent_bridge.models import SessionStatus
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


def _session_manager(tmp_path) -> SessionManager:
    db = Database(tmp_path / "sessions.db")
    return SessionManager(db, session_host_state_dir=str(tmp_path / "hosts"))


def _register(mgr: SessionManager, session_id: str, *, host_pid: int = 111) -> HostRecord:
    rec = HostRecord(session_id=session_id, port=9000, host_pid=host_pid, child_pid=222)
    mgr._host_index.register(rec)
    return rec


def test_generation_id_is_computed_and_stable(tmp_path) -> None:
    mgr = _session_manager(tmp_path)
    assert mgr._generation_id
    # Stable across repeated reads within the same process lifetime.
    assert mgr._generation_id == mgr._generation_id


def test_two_managers_get_distinct_generation_ids(tmp_path) -> None:
    a = _session_manager(tmp_path / "a")
    b = _session_manager(tmp_path / "b")
    assert a._generation_id != b._generation_id


def test_claim_host_record_succeeds_on_a_fresh_record(tmp_path) -> None:
    mgr = _session_manager(tmp_path)
    _register(mgr, "s1")
    assert mgr._claim_host_record(SimpleNamespace(session_id="s1")) is True
    assert mgr._host_index.get("s1").owner_generation == mgr._generation_id


def test_claim_host_record_is_idempotent(tmp_path) -> None:
    mgr = _session_manager(tmp_path)
    _register(mgr, "s1")
    assert mgr._claim_host_record(SimpleNamespace(session_id="s1")) is True
    assert mgr._claim_host_record(SimpleNamespace(session_id="s1")) is True


def test_claim_host_record_refuses_a_live_other_generation(tmp_path) -> None:
    mgr = _session_manager(tmp_path)
    _register(mgr, "s1")
    mgr._host_index.claim(
        "s1", generation="some-other-generation", owner_pid=os.getpid(),
        pid_alive=lambda p: True,
    )
    assert mgr._claim_host_record(SimpleNamespace(session_id="s1")) is False
    # The live other generation's claim is untouched.
    assert mgr._host_index.get("s1").owner_generation == "some-other-generation"


def test_claim_host_record_recovers_a_dead_generations_claim(tmp_path) -> None:
    mgr = _session_manager(tmp_path)
    _register(mgr, "s1")
    mgr._host_index.claim(
        "s1", generation="dead-generation", owner_pid=999999,
        pid_alive=lambda p: False,
    )
    assert mgr._claim_host_record(SimpleNamespace(session_id="s1")) is True
    assert mgr._host_index.get("s1").owner_generation == mgr._generation_id


def test_claim_host_record_permits_an_untracked_record(tmp_path) -> None:
    """A record never registered in this process's own HostIndex (the shape
    many existing reattach tests use via a bare SimpleNamespace) is not a
    claim conflict -- Phase 3 must not regress tests that predate it."""
    mgr = _session_manager(tmp_path)
    assert mgr._claim_host_record(SimpleNamespace(session_id="untracked")) is True


def test_claim_host_record_is_a_noop_without_a_host_index(tmp_path) -> None:
    mgr = _session_manager(tmp_path)
    mgr._host_index = None
    assert mgr._claim_host_record(SimpleNamespace(session_id="s1")) is True


@pytest.mark.asyncio
async def test_reattach_skips_a_record_still_claimed_by_a_live_generation(
    tmp_path, monkeypatch,
) -> None:
    mgr = _session_manager(tmp_path)
    session = Session("session-1", "agent", SpawnTarget(type="local", cwd=str(tmp_path)))
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "acp-1"
    mgr._sessions[session.session_id] = session
    _register(mgr, session.session_id)
    mgr._host_index.claim(
        session.session_id, generation="some-other-generation", owner_pid=os.getpid(),
        pid_alive=lambda p: True,
    )
    rec = SimpleNamespace(
        session_id=session.session_id, protocol_version=1, host_version="test",
        host_pid=123, child_pid=456, created_at=time.time(),
        resume_on_reattach=False, boundary="local",
    )
    attach = AsyncMock(return_value=True)
    monkeypatch.setattr(mgr, "_recover_remote_host_records", AsyncMock(return_value=0))
    monkeypatch.setattr(mgr, "_prune_dead_hosts", lambda: None)
    monkeypatch.setattr(mgr, "_live_host_records", lambda: [rec])
    monkeypatch.setattr(mgr, "_rec_child_alive", lambda _rec: True)
    monkeypatch.setattr(mgr, "_reattach_one", attach)

    assert await mgr.reattach_session_hosts(remote_recovery_timeout=1.0) == 0
    attach.assert_not_awaited()
    # Deliberately NOT marked inconclusive (PR #4543 review): that would
    # permanently block a later retry (the post-cutover claim sweep) for
    # this session, defeating the exact mechanism it exists to make work.
    assert session.session_id not in mgr._remote_recovery_inconclusive


@pytest.mark.asyncio
async def test_reattach_retries_successfully_once_a_live_claim_is_released(
    tmp_path, monkeypatch,
) -> None:
    """The retry path this whole mechanism exists for: a first pass sees the
    record claimed by a live other generation and skips it; a second pass,
    after that generation releases the claim, succeeds."""
    mgr = _session_manager(tmp_path)
    session = Session("session-1", "agent", SpawnTarget(type="local", cwd=str(tmp_path)))
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "acp-1"
    mgr._sessions[session.session_id] = session
    _register(mgr, session.session_id)
    mgr._host_index.claim(
        session.session_id, generation="some-other-generation", owner_pid=os.getpid(),
        pid_alive=lambda p: True,
    )
    rec = SimpleNamespace(
        session_id=session.session_id, protocol_version=1, host_version="test",
        host_pid=123, child_pid=456, created_at=time.time(),
        resume_on_reattach=False, boundary="local",
    )
    attach = AsyncMock(return_value=True)
    monkeypatch.setattr(mgr, "_recover_remote_host_records", AsyncMock(return_value=0))
    monkeypatch.setattr(mgr, "_prune_dead_hosts", lambda: None)
    monkeypatch.setattr(mgr, "_live_host_records", lambda: [rec])
    monkeypatch.setattr(mgr, "_rec_child_alive", lambda _rec: True)
    monkeypatch.setattr(mgr, "_reattach_one", attach)

    # First pass: contended, skipped.
    assert await mgr.reattach_session_hosts(remote_recovery_timeout=1.0) == 0
    attach.assert_not_awaited()

    # The other generation releases its claim (its own exit contract).
    mgr._host_index.release("session-1", "some-other-generation")

    # Second pass (the post-cutover retry): now succeeds.
    assert await mgr.reattach_session_hosts(remote_recovery_timeout=1.0) == 1
    attach.assert_awaited_once()


@pytest.mark.asyncio
async def test_reattach_claims_and_proceeds_once_the_prior_generation_is_dead(
    tmp_path, monkeypatch,
) -> None:
    mgr = _session_manager(tmp_path)
    session = Session("session-1", "agent", SpawnTarget(type="local", cwd=str(tmp_path)))
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "acp-1"
    mgr._sessions[session.session_id] = session
    _register(mgr, session.session_id)
    mgr._host_index.claim(
        session.session_id, generation="dead-generation", owner_pid=999999,
        pid_alive=lambda p: False,
    )
    rec = SimpleNamespace(
        session_id=session.session_id, protocol_version=1, host_version="test",
        host_pid=123, child_pid=456, created_at=time.time(),
        resume_on_reattach=False, boundary="local",
    )
    attach = AsyncMock(return_value=True)
    monkeypatch.setattr(mgr, "_recover_remote_host_records", AsyncMock(return_value=0))
    monkeypatch.setattr(mgr, "_prune_dead_hosts", lambda: None)
    monkeypatch.setattr(mgr, "_live_host_records", lambda: [rec])
    monkeypatch.setattr(mgr, "_rec_child_alive", lambda _rec: True)
    monkeypatch.setattr(mgr, "_reattach_one", attach)

    assert await mgr.reattach_session_hosts(remote_recovery_timeout=1.0) == 1
    attach.assert_awaited_once()
    assert mgr._host_index.get(session.session_id).owner_generation == mgr._generation_id


def test_shutdown_releases_every_claim_this_generation_holds(tmp_path) -> None:
    """The exit-contract half: releasing is the outgoing generation's own
    initiative at shutdown, not something the next generation has to wait on
    or discover by probing a dead pid."""
    mgr = _session_manager(tmp_path)
    _register(mgr, "s1")
    _register(mgr, "s2")
    mgr._host_index.claim(
        "s1", generation=mgr._generation_id, owner_pid=1, pid_alive=lambda p: True,
    )
    mgr._host_index.claim(
        "s2", generation=mgr._generation_id, owner_pid=1, pid_alive=lambda p: True,
    )
    released = mgr._host_index.release_all(mgr._generation_id)
    assert set(released) == {"s1", "s2"}
    assert mgr._host_index.get("s1").owner_generation == ""
    assert mgr._host_index.get("s2").owner_generation == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("during_scan", [False, True])
async def test_reattach_refreshes_late_predecessor_metadata_without_replacing_owned_sessions(
    tmp_path, monkeypatch, existing, during_scan,
) -> None:
    db = Database(tmp_path / "sessions.db")
    if existing:
        db.create_session("late", "late", None, str(tmp_path), "local", "starting", 1)
    (tmp_path / "hosts").mkdir()
    mgr = SessionManager(
        db, session_host_state_dir=str(tmp_path / "hosts"), read_only_rehydrate=True,
    )
    stale = mgr._sessions.get("late")
    owned = Session("owned", "owned", SpawnTarget(type="local", cwd=str(tmp_path)))
    owned.client = SimpleNamespace(is_running=True)
    owned.status = SessionStatus.RUNNING
    owned.acp_session_id = "owned-acp"
    owned.subscriber_count = 1
    mgr._sessions["owned"] = owned
    db.create_session(
        "owned", "stale owned", None, str(tmp_path), "local", "stopped", 1,
        acp_session_id="old-owned-acp",
    )
    _register(mgr, "owned")
    owned_state = dict(vars(owned))
    reaped, attached = [], []

    def predecessor_finishes_start():
        if not existing:
            db.create_session("late", "late", None, str(tmp_path), "local", "starting", 1)
        db.update_session_acp_id("late", "late-acp")
        db.update_session_status("late", "running", 2)
        db.create_turn("late", 1, "unfinished", 2)
        _register(mgr, "late")

    async def remote_recovery(**_kwargs):
        if during_scan:
            predecessor_finishes_start()
        return 0

    async def attach(_rec, session, **_kwargs):
        assert session.acp_session_id == "late-acp"
        assert session.status is SessionStatus.RUNNING
        attached.append(session)
        session.client = SimpleNamespace(is_running=True)
        return True

    if not during_scan:
        predecessor_finishes_start()
    monkeypatch.setattr(mgr, "_recover_remote_host_records", remote_recovery)
    monkeypatch.setattr(mgr, "_prune_dead_hosts", lambda: None)
    monkeypatch.setattr(mgr, "_live_host_records", lambda: [
        mgr._host_index.get("owned"), mgr._host_index.get("late"),
    ])
    monkeypatch.setattr(mgr, "_rec_child_alive", lambda _rec: True)
    monkeypatch.setattr(mgr, "_reap_host_record", lambda *args: reaped.append(args))
    monkeypatch.setattr(mgr, "_reattach_one", attach)

    assert await mgr.reattach_session_hosts(recover_cold=False) == 1
    assert len(attached) == 1 and not reaped
    if stale is not None:
        assert attached[0] is stale
    assert attached[0].restart_status == SessionStatus.RUNNING.value
    assert mgr._sessions["owned"] is owned and vars(owned) == owned_state
    assert db.get_session("late")["status"] == "running"
    assert db.get_turns("late")[0]["completed_at"] is None
    assert mgr._host_index.get("late").owner_generation == mgr._generation_id
    db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", ["lifecycle", "turn", "subscriber"])
async def test_metadata_merge_preserves_a_session_in_use_without_a_client(
    tmp_path, busy,
) -> None:
    mgr = _session_manager(tmp_path)
    db = mgr._db
    db.create_session(
        "s1", "stored", None, str(tmp_path), "local", "running", 1,
        acp_session_id="stored-acp",
    )
    session = Session("s1", "live", SpawnTarget(type="local", cwd=str(tmp_path)))
    session.acp_session_id = "live-acp"
    mgr._sessions["s1"] = session
    lock = None
    if busy == "subscriber":
        session.subscriber_count = 1
    else:
        lock = session._lifecycle_lock if busy == "lifecycle" else session._turn_start_lock
        await lock.acquire()
    before = dict(vars(session))
    try:
        mgr._rehydrate(merge=True)
        assert mgr._sessions["s1"] is session and vars(session) == before
        assert db.get_session("s1")["status"] == "running"
    finally:
        if lock is not None:
            lock.release()
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", ["subscriber", "lifecycle"])
async def test_late_acp_identity_defers_reaping_until_metadata_can_refresh(
    tmp_path, monkeypatch, busy,
) -> None:
    mgr = _session_manager(tmp_path)
    db = mgr._db
    db.create_session("late", "late", None, str(tmp_path), "local", "starting", 1)
    mgr._rehydrate(read_only=True)
    session = mgr._sessions["late"]
    event_log = session.event_log
    _register(mgr, "late")
    db.update_session_acp_id("late", "late-acp")
    db.update_session_status("late", "running", 2)
    if busy == "subscriber":
        session.subscriber_count = 1
    else:
        await session._lifecycle_lock.acquire()
    before = dict(vars(session))
    reaped = []
    attach = AsyncMock(return_value=True)
    monkeypatch.setattr(mgr, "_recover_remote_host_records", AsyncMock(return_value=0))
    monkeypatch.setattr(mgr, "_prune_dead_hosts", lambda: None)
    monkeypatch.setattr(mgr, "_live_host_records", lambda: [mgr._host_index.get("late")])
    monkeypatch.setattr(mgr, "_rec_child_alive", lambda _rec: True)
    monkeypatch.setattr(mgr, "_reap_host_record", lambda *args: reaped.append(args))
    monkeypatch.setattr(mgr, "_reattach_one", attach)
    try:
        assert await mgr.reattach_session_hosts(recover_cold=False) == 0
        assert not reaped
        attach.assert_not_awaited()
        assert vars(session) == before and session.event_log is event_log
        assert db.get_session("late")["acp_session_id"] == "late-acp"
        if busy == "subscriber":
            session.subscriber_count = 0
        else:
            session._lifecycle_lock.release()
        assert await mgr.reattach_session_hosts(recover_cold=False) == 1
        assert not reaped and attach.await_args.args[1] is session
        assert session.acp_session_id == "late-acp"
    finally:
        if session._lifecycle_lock.locked():
            session._lifecycle_lock.release()
        db.close()


# -- claim_hosts=False: a passive instance never claims at startup (PR #4543
# review) -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_hosts_false_reattaches_without_claiming(tmp_path, monkeypatch) -> None:
    """A passive cutover instance's own startup scan must not stamp
    generation ownership onto a record the still-live old generation may
    still be driving -- even though (pre-claim) this is indistinguishable
    from a legitimately unclaimed record. It still reattaches (unchanged,
    pre-Phase-3 behavior); it just never calls claim()."""
    mgr = _session_manager(tmp_path)
    session = Session("session-1", "agent", SpawnTarget(type="local", cwd=str(tmp_path)))
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "acp-1"
    mgr._sessions[session.session_id] = session
    _register(mgr, session.session_id)
    rec = SimpleNamespace(
        session_id=session.session_id, protocol_version=1, host_version="test",
        host_pid=123, child_pid=456, created_at=time.time(),
        resume_on_reattach=False, boundary="local",
    )
    attach = AsyncMock(return_value=True)
    monkeypatch.setattr(mgr, "_recover_remote_host_records", AsyncMock(return_value=0))
    monkeypatch.setattr(mgr, "_prune_dead_hosts", lambda: None)
    monkeypatch.setattr(mgr, "_live_host_records", lambda: [rec])
    monkeypatch.setattr(mgr, "_rec_child_alive", lambda _rec: True)
    monkeypatch.setattr(mgr, "_reattach_one", attach)

    reattached = await mgr.reattach_session_hosts(
        remote_recovery_timeout=1.0, claim_hosts=False,
    )
    assert reattached == 1
    attach.assert_awaited_once()
    # No claim was ever attempted -- ownership fields are untouched.
    assert mgr._host_index.get(session.session_id).owner_generation == ""


@pytest.mark.asyncio
async def test_claim_hosts_false_does_not_steal_a_live_other_generations_claim(
    tmp_path, monkeypatch,
) -> None:
    """Even when a record IS already claimed by a live other generation, a
    passive instance's claim_hosts=False scan reattaches anyway (matching
    pre-Phase-3 behavior exactly) -- it simply never touches claim state
    either way while passive."""
    mgr = _session_manager(tmp_path)
    session = Session("session-1", "agent", SpawnTarget(type="local", cwd=str(tmp_path)))
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "acp-1"
    mgr._sessions[session.session_id] = session
    _register(mgr, session.session_id)
    mgr._host_index.claim(
        session.session_id, generation="live-old-generation", owner_pid=os.getpid(),
        pid_alive=lambda p: True,
    )
    rec = SimpleNamespace(
        session_id=session.session_id, protocol_version=1, host_version="test",
        host_pid=123, child_pid=456, created_at=time.time(),
        resume_on_reattach=False, boundary="local",
    )
    attach = AsyncMock(return_value=True)
    monkeypatch.setattr(mgr, "_recover_remote_host_records", AsyncMock(return_value=0))
    monkeypatch.setattr(mgr, "_prune_dead_hosts", lambda: None)
    monkeypatch.setattr(mgr, "_live_host_records", lambda: [rec])
    monkeypatch.setattr(mgr, "_rec_child_alive", lambda _rec: True)
    monkeypatch.setattr(mgr, "_reattach_one", attach)

    reattached = await mgr.reattach_session_hosts(
        remote_recovery_timeout=1.0, claim_hosts=False,
    )
    assert reattached == 1
    # The live other generation's own claim is untouched.
    assert mgr._host_index.get(session.session_id).owner_generation == "live-old-generation"
