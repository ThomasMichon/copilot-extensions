"""Idle-session reaper + subscriber tracking (#1826, ownership inversion).

The bridge owns session process lifetime by connection + state: an idle,
unwatched session past the TTL is stopped -- freeing its Copilot child while
staying resumable (fresh child + load_session replay). It never touches a
running/mid-turn session (goal 1), one with a live subscriber, or one hosting
active background sub-agents. A front (Neuron Forge) therefore need only
connect/disconnect and never reaps for resource reasons.
"""

from __future__ import annotations

import time
import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock
from types import SimpleNamespace

import pytest

from agent_bridge.db import Database
from agent_bridge.models import SessionStatus
from agent_bridge.runtime_custody import RuntimeAdmission
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget
from test_runtime_custody import enrolled


def _mgr(tmp_path, *, ttl: float = 0.0) -> SessionManager:
    db = Database(tmp_path / "reap.db")
    return SessionManager(
        db,
        session_host_state_dir=str(tmp_path / "hosts"),
        idle_reap_ttl_seconds=ttl,
    )


def _session(
    mgr: SessionManager,
    sid: str = "s1",
    *,
    status: SessionStatus = SessionStatus.IDLE,
    idle_for: float = 0.0,
    subscribers: int = 0,
    background: bool = False,
    turns: int = 1,
    runtime_admission: RuntimeAdmission | None = None,
) -> Session:
    s = Session(sid, sid, SpawnTarget(type="local", cwd="/tmp/x"))
    s.status = status
    s.turn_count = turns  # >=1 => has resumable state
    s.updated_at = time.time() - idle_for
    s.subscriber_count = subscribers
    s.acp_session_id = f"native-{sid}"
    if background:
        client = MagicMock()
        client.has_active_background_tasks = True
        s.client = client
    mgr._sessions[sid] = s
    mgr.db.create_session(
        sid, sid, None, s.target.cwd, s.target.type, status.value, s.updated_at,
        acp_session_id=s.acp_session_id, target_json=s.target.to_json(),
        runtime_admission=runtime_admission,
    )
    return s


# -- subscriber tracking --------------------------------------------------

def test_add_remove_subscriber_counts(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    _session(mgr, "s1", subscribers=0)
    mgr.add_subscriber("s1")
    mgr.add_subscriber("s1")
    assert mgr._sessions["s1"].subscriber_count == 2
    mgr.remove_subscriber("s1")
    assert mgr._sessions["s1"].subscriber_count == 1
    mgr.remove_subscriber("s1")
    assert mgr._sessions["s1"].subscriber_count == 0
    # clamp at zero
    mgr.remove_subscriber("s1")
    assert mgr._sessions["s1"].subscriber_count == 0


def test_remove_last_subscriber_resets_idle_clock(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    s = _session(mgr, "s1", subscribers=1, idle_for=1000)
    mgr.remove_subscriber("s1")
    # touch() on the last unsubscribe starts the idle-reap TTL from *now*.
    assert time.time() - s.updated_at < 5


def test_subscriber_ops_on_unknown_session_are_noops(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    mgr.add_subscriber("nope")
    mgr.remove_subscriber("nope")  # must not raise


# -- idle reaper decision matrix ------------------------------------------

@pytest.mark.asyncio
async def test_reaper_disabled_when_ttl_zero(tmp_path) -> None:
    mgr = _mgr(tmp_path, ttl=0)
    _session(mgr, "s1", idle_for=99999)
    assert await mgr.sweep_idle_sessions() == 0
    assert mgr._sessions["s1"].status == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_reaper_stops_idle_unwatched_past_ttl_and_reaps_host(
    tmp_path, monkeypatch
) -> None:
    mgr = _mgr(tmp_path, ttl=60)
    reasons: list[str] = []
    monkeypatch.setattr(
        mgr, "_reap_host_record", lambda rec, reason: reasons.append(reason)
    )
    s = _session(mgr, "s1", idle_for=120, subscribers=0)
    mgr._host_index.register(
        HostRecord(session_id="s1", port=1, host_pid=1, child_pid=1)
    )

    assert await mgr.sweep_idle_sessions() == 1
    assert s.status == SessionStatus.STOPPED  # resumable, not ended
    assert reasons  # the Copilot child was reaped (freed), not merely detached


@pytest.mark.asyncio
async def test_reaper_skips_watched_session(tmp_path) -> None:
    mgr = _mgr(tmp_path, ttl=60)
    s = _session(mgr, "s1", idle_for=120, subscribers=1)
    assert await mgr.sweep_idle_sessions() == 0
    assert s.status == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_reaper_skips_within_ttl(tmp_path) -> None:
    mgr = _mgr(tmp_path, ttl=600)
    s = _session(mgr, "s1", idle_for=120)
    assert await mgr.sweep_idle_sessions() == 0
    assert s.status == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_reaper_never_touches_running_session(tmp_path) -> None:
    mgr = _mgr(tmp_path, ttl=60)
    s = _session(mgr, "s1", status=SessionStatus.RUNNING, idle_for=99999)
    assert await mgr.sweep_idle_sessions() == 0
    assert s.status == SessionStatus.RUNNING


@pytest.mark.asyncio
async def test_reaper_skips_active_background_tasks(tmp_path) -> None:
    mgr = _mgr(tmp_path, ttl=60)
    s = _session(mgr, "s1", idle_for=99999, background=True)
    assert await mgr.sweep_idle_sessions() == 0
    assert s.status == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_reaper_skips_zero_turn_session(tmp_path) -> None:
    # A 0-turn session has no persisted ACP conversation, so a fresh child
    # cannot load_session it -- reaping to STOPPED would leave it unresumable.
    mgr = _mgr(tmp_path, ttl=60)
    s = _session(mgr, "s1", idle_for=99999, turns=0)
    assert await mgr.sweep_idle_sessions() == 0
    assert s.status == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_idle_reaper_does_not_invert_reattach_prompt_locks(tmp_path, monkeypatch) -> None:
    mgr = _mgr(tmp_path, ttl=60)
    s = _session(mgr, idle_for=120)
    lifecycle_entered, turn_entered = asyncio.Event(), asyncio.Event()

    class ObservedTurnLock(asyncio.Lock):
        async def __aenter__(self) -> None:
            await super().__aenter__()
            turn_entered.set()

    s._turn_start_lock = ObservedTurnLock()
    prompt = AsyncMock(return_value=0)
    stop = AsyncMock(wraps=mgr.stop_session)
    monkeypatch.setattr(mgr, "_submit_prompt_locked", prompt)
    monkeypatch.setattr(mgr, "stop_session", stop)

    async def reattach(_rec, session, *, new_status, send_resume):
        assert session is s and send_resume
        assert session._lifecycle_lock.locked()
        lifecycle_entered.set()
        await turn_entered.wait()
        await mgr.submit_prompt(session.session_id, "Synthetic reattach resume.")
        return True

    monkeypatch.setattr(mgr, "_reattach_one", reattach)
    rec = SimpleNamespace(session_id=s.session_id, boundary="local", resume_on_reattach=True)
    recovery = asyncio.create_task(mgr._reattach_or_note_failure(
        rec, s, keep=SessionStatus.IDLE, codespace_availability={}, now=time.time(),
    ))
    await asyncio.wait_for(lifecycle_entered.wait(), 1)
    sweep = asyncio.create_task(mgr.sweep_idle_sessions())
    recovered, reaped = await asyncio.wait_for(asyncio.gather(recovery, sweep), 1)
    assert recovered is True and reaped == 0
    prompt.assert_awaited_once()
    stop.assert_not_awaited()
    assert not s._turn_start_lock.locked() and not s._lifecycle_lock.locked()
    assert s.status == SessionStatus.IDLE and s.background_recovery_enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("captured", [False, True])
async def test_idle_reaper_preserves_original_native_custody(tmp_path, monkeypatch, captured):
    mgr = _mgr(tmp_path, ttl=60)
    owner, _producer, admission = enrolled(mgr.db)
    s = _session(mgr, idle_for=120, runtime_admission=admission)
    if captured:
        s._native_turn_transport = object()
    mgr._host_index.register(HostRecord(session_id=s.session_id, port=1, host_pid=1, child_pid=1))
    stop = AsyncMock(wraps=mgr.stop_session)
    reap = Mock()
    monkeypatch.setattr(mgr, "stop_session", stop)
    monkeypatch.setattr(mgr, "_reap_host_record", reap)
    before = mgr.db.runtime_epoch(owner)
    assert not mgr._host_reapable(s)
    assert await mgr.sweep_idle_sessions() == 0
    stop.assert_not_awaited()
    reap.assert_not_called()
    assert s.status == SessionStatus.IDLE
    assert mgr._host_index.get(s.session_id) is not None
    assert mgr.db.runtime_epoch(owner) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["turn-lock", "lifecycle-lock", "final-reap"])
async def test_idle_reaper_rechecks_custody_after_scheduling(tmp_path, monkeypatch, boundary):
    mgr = _mgr(tmp_path, ttl=60)
    s = _session(mgr, idle_for=120)
    mgr._host_index.register(HostRecord(session_id=s.session_id, port=1, host_pid=1, child_pid=1))
    reap = Mock()
    quiesce = AsyncMock()
    monkeypatch.setattr(mgr, "_reap_host_record", reap)
    monkeypatch.setattr(mgr, "_quiesce_session", quiesce)
    if boundary == "final-reap":
        async def acquire_during_quiesce(*_args, **_kwargs):
            s._native_turn_transport = object()
        quiesce.side_effect = acquire_during_quiesce
        assert await mgr.sweep_idle_sessions() == 0
    else:
        lock = s._turn_start_lock if boundary == "turn-lock" else s._lifecycle_lock
        entered = asyncio.Event()
        if boundary == "lifecycle-lock":
            request_stop = mgr.db.request_cold_recovery_stop
            def requested(*args, **kwargs):
                entered.set()
                return request_stop(*args, **kwargs)
            monkeypatch.setattr(mgr.db, "request_cold_recovery_stop", requested)
        await lock.acquire()
        async def stop_after_turn_lock() -> None:
            async with s._turn_start_lock:
                await mgr.stop_session(s.session_id, reap_host=True, _automatic_reap=True)

        task = asyncio.create_task(
            mgr.sweep_idle_sessions() if boundary == "turn-lock" else stop_after_turn_lock(),
        )
        try:
            if boundary == "turn-lock":
                await asyncio.sleep(0)
            else:
                await asyncio.wait_for(entered.wait(), 5)
            s._native_turn_transport = object()
        finally:
            lock.release()
        if boundary == "turn-lock":
            assert await asyncio.wait_for(task, 5) == 0
        else:
            from agent_bridge.runtime_custody import RuntimeCustodyError

            with pytest.raises(RuntimeCustodyError, match="Automatic Host reaping"):
                await asyncio.wait_for(task, 5)
        quiesce.assert_not_awaited()
    reap.assert_not_called()
    assert mgr._host_index.get(s.session_id) is not None
