"""Real Bridge recovery gates over mocked Darwin libproc, not native qualification."""

from __future__ import annotations

import ctypes
import errno
import json
import select
from dataclasses import replace
from types import SimpleNamespace

import pytest
from zdd import darwin_identity, process_exit, routing

from agent_bridge import cold_outages, serving_reconcile
from agent_bridge.app import _retire_previous_daemon
from agent_bridge.models import SessionStatus
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget

BIRTH = "1001:22"


@pytest.fixture
def native(monkeypatch):
    states = {}
    calls = []

    class Query:
        def __call__(self, pid, flavor, argument, pointer, size):
            assert (flavor, argument) == (3, 0)
            calls.append(pid)
            state = states.get(pid, "absent")
            if state in {"absent", "denied", "unknown"}:
                ctypes.set_errno({
                    "absent": errno.ESRCH, "denied": errno.EPERM, "unknown": 0,
                }[state])
                return 0
            info = ctypes.cast(pointer, ctypes.POINTER(darwin_identity._BsdInfo)).contents
            info.pid = pid + 1 if state == "wrong-pid" else pid
            info.status = 5 if state == "zombie" else 2
            info.start_seconds = 1002 if state == "reused" else 1001
            info.start_microseconds = 22
            ctypes.set_errno(errno.ESRCH)  # Partial responses never prove absence.
            return size - 1 if state == "short" else size

    monkeypatch.setattr(
        darwin_identity.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(proc_pidinfo=Query()),
    )
    monkeypatch.setattr(process_exit, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(select, "kqueue", lambda: SimpleNamespace(
        control=lambda *a: [], close=lambda: None,
    ), raising=False)
    monkeypatch.setattr(select, "kevent", lambda ident, **kw: SimpleNamespace(
        ident=ident, **kw,
    ), raising=False)
    for name, value in {
        "KQ_FILTER_PROC": -5, "KQ_EV_ADD": 1, "KQ_EV_ONESHOT": 16,
        "KQ_NOTE_EXIT": 0x80000000, "KQ_EV_ERROR": 0x4000,
    }.items():
        monkeypatch.setattr(select, name, value, raising=False)
    return states, calls


def _interrupted(manager, tmp_path, initial):
    target = SpawnTarget(type="local", cwd=str(tmp_path))
    session = Session("cold-session", "cold", target)
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "11111111-2222-4333-8444-555555555555"
    manager._sessions[session.session_id] = session
    manager._generation_id = "successor-generation"
    manager.db.create_session(
        session.session_id, session.name, None, target.cwd, "local", "running", 1,
        acp_session_id=session.acp_session_id, target_json=target.to_json(),
    )
    manager.db.execute_write(
        "INSERT INTO cold_outages "
        "(outage_id,session_id,native_session_id,target_json,previous_status,"
        "owner_generation,execution_json,interrupted_turns_json,state,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "outage", session.session_id, session.acp_session_id, target.to_json(), "running",
            "original-generation", json.dumps({
                "host": {"pid": 101, "start_time": BIRTH},
                "child": {"pid": 202, "start_time": BIRTH},
            }), "[]", initial, 1,
        ),
    )
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["prepared", "failed", "interrupted"])
@pytest.mark.parametrize("state", ["absent", "reused", "zombie"])
async def test_real_cold_recovery_claim_accepts_only_original_native_exit(
    native, session_manager, tmp_path, initial, state,
):
    states, calls = native
    states.update({101: state, 202: state})
    session = _interrupted(session_manager, tmp_path, initial)
    with (
        cold_outages.recovery_admission(session_manager, session.session_id),
        cold_outages.operation(session_manager, session.session_id),
    ):
        row = cold_outages.claim(session_manager, session)
        assert row["state"] == "interrupted"
        assert row["recovery_phase"] == "reserved"
        assert row["recovery_generation"] == session_manager._generation_id
        assert cold_outages.resume_policy(session_manager, session) == row
    assert {101, 202} <= set(calls)
    assert session_manager.db.get_session(session.session_id)["acp_session_id"] == session.acp_session_id
    assert session_manager.db.get_turns(session.session_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pid", [101, 202])
@pytest.mark.parametrize("state,error", [
    ("live", ValueError), ("denied", PermissionError), ("unknown", OSError),
    ("short", ValueError), ("wrong-pid", ValueError),
])
async def test_real_cold_recovery_uncertainty_preserves_durable_outage(
    native, session_manager, tmp_path, pid, state, error,
):
    states, _calls = native
    states[pid] = state
    session = _interrupted(session_manager, tmp_path, "prepared")
    before = cold_outages.latest(session_manager.db, session.session_id)
    with cold_outages.operation(session_manager, session.session_id):
        with pytest.raises(error):
            cold_outages.claim(session_manager, session)
    assert cold_outages.latest(session_manager.db, session.session_id) == before
    assert session_manager.db.get_turns(session.session_id) == []


@pytest.mark.parametrize("state", ["absent", "live", "reused", "denied", "unknown", "short"])
def test_real_serving_reconciliation_accepts_only_native_absent_predecessor(
    native, tmp_path, state,
):
    states, calls = native
    states[101] = state
    previous = {
        "pid": 101, "process_start_time": BIRTH, "bind": "127.0.0.1",
        "port": 41001, "generation": 1, "version": "1.0.0",
    }
    path = tmp_path / "active.json"
    path.write_text(json.dumps({"active": None, "previous": previous}), encoding="utf-8")
    before = path.read_bytes()
    if state == "absent":
        with serving_reconcile.observe_serving(tmp_path) as observed:
            assert observed is None
    else:
        with pytest.raises(serving_reconcile.ServingReconcileError):
            with serving_reconcile.observe_serving(tmp_path):
                pytest.fail("unverified predecessor must not count as absent")
    assert calls
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_already_absent_predecessor_reaches_real_bridge_recovery_gate(
    native, tmp_path, monkeypatch, session_manager,
):
    _states, calls = native
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    previous = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=41001, pid=101, version="1.0.0",
    )
    successor = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=41002, pid=202, version="1.0.1", demote_existing=True,
    )
    previous = replace(previous, process_start_time=BIRTH)
    app = SimpleNamespace(state=SimpleNamespace(
        session_manager=session_manager, ready=False, cold_recovery_ready=False,
    ))
    await _retire_previous_daemon(
        app, previous, successor,
        make_client=lambda _: pytest.fail("absent predecessor must receive no control"),
    )
    assert app.state.cold_recovery_ready is True
    assert calls.count(101) == 2  # Failed capture, then independent original-exit observation.
