"""A session's credential-relay supervisors end with the session.

A relay supervisor left running after its session ends keeps reconnecting
through ``gh codespace ssh`` and so re-wakes a CodeSpace that was stopped on
purpose. Every end-of-session path must retire the session's own relays while
leaving another live session's relays alone.
"""

from __future__ import annotations

import pytest

from agent_bridge.models import SessionStatus
from agent_bridge.session_host import endpoints as endpoints_mod
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


class _Relay:
    def __init__(self) -> None:
        self.stopped = 0
        self.stopped_nowait = 0
        self.is_alive = True

    async def stop(self) -> None:
        self.stopped += 1
        self.is_alive = False

    def stop_nowait(self) -> None:
        self.stopped_nowait += 1
        self.is_alive = False

    @property
    def retired_by_owner(self) -> bool:
        return (self.stopped + self.stopped_nowait) > 0


def _codespace_session(manager: SessionManager, db, session_id: str) -> Session:
    target = SpawnTarget(type="codespace")
    session = Session(session_id, "codespace", target)
    session.status = SessionStatus.IDLE
    manager._sessions[session_id] = session
    db.create_session(
        session_id=session_id,
        name=session.name,
        agent_name=None,
        caller_id=None,
        target_dir=None,
        target_type="codespace",
        status=SessionStatus.IDLE.value,
        now=1,
        target_json=target.to_json(),
    )
    return session


@pytest.fixture
def manager_with_two_sessions(tmp_db):
    manager = SessionManager(tmp_db)
    failing = _codespace_session(manager, tmp_db, "failing")
    _codespace_session(manager, tmp_db, "other")
    failing_relay, other_relay = _Relay(), _Relay()
    manager._relays["failing"] = [failing_relay]
    manager._relays["other"] = [other_relay]
    return manager, failing, failing_relay, other_relay


def test_failed_session_retires_its_relay_only(manager_with_two_sessions):
    manager, failing, failing_relay, other_relay = manager_with_two_sessions

    manager._mark_session_failed(failing, trigger="connect_failed")

    assert failing.status == SessionStatus.FAILED
    assert failing_relay.stopped_nowait == 1
    assert "failing" not in manager._relays
    assert other_relay.retired_by_owner is False
    assert manager._relays["other"] == [other_relay]


@pytest.mark.asyncio
async def test_end_failed_session_without_host_record_stops_relay(
    manager_with_two_sessions,
):
    manager, failing, failing_relay, other_relay = manager_with_two_sessions
    failing.status = SessionStatus.FAILED
    assert manager._host_index.get("failing") is None

    await manager.end_session("failing", force=True)

    assert failing_relay.stopped == 1
    assert "failing" not in manager._relays
    assert "failing" not in manager._sessions
    assert other_relay.retired_by_owner is False
    assert manager._relays["other"] == [other_relay]


@pytest.mark.asyncio
async def test_explicit_stop_stops_relay(manager_with_two_sessions):
    manager, failing, failing_relay, other_relay = manager_with_two_sessions

    await manager.stop_session("failing", force=True)

    assert failing.status == SessionStatus.STOPPED
    assert failing_relay.stopped == 1
    assert "failing" not in manager._relays
    assert other_relay.retired_by_owner is False


@pytest.mark.asyncio
async def test_redeploy_detach_keeps_relay_for_surviving_turn(
    manager_with_two_sessions,
):
    manager, failing, failing_relay, _other = manager_with_two_sessions

    await manager.stop_session("failing", force=True, cancel_turn=False)

    assert failing_relay.retired_by_owner is False
    assert manager._relays["failing"] == [failing_relay]


def test_kill_relays_sync_uses_supervisor_stop_nowait(tmp_db):
    manager = SessionManager(tmp_db)
    relay = _Relay()
    manager._relays["s1"] = [relay]

    manager._kill_relays_sync("s1")

    assert relay.stopped_nowait == 1
    assert "s1" not in manager._relays


def _codespace_endpoint(**extra):
    return {
        "kind": "codespace",
        "remote_port": 51000,
        "local_port": 49555,
        "reverse_forwards": ["9857:127.0.0.1:9857"],
        "ssh": {"host_alias": "cs.box", "config_file": "cs.config"},
        **extra,
    }


def test_codespace_relay_gets_no_wake_reconnect_gate(monkeypatch):
    captured = []

    class _Supervisor:
        def __init__(self, config, relay_port, **kw):
            captured.append(kw)

    monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", _Supervisor)

    endpoints_mod.relay_forwards_from_endpoint(
        _codespace_endpoint(codespace="example-cs", repo="octo/repo")
    )

    gate = captured[0]["reconnect_gate"]
    assert gate is not None
    assert gate.__self__.codespace_name == "example-cs"
    assert gate.__name__ == "is_running"


def test_non_codespace_relay_has_no_reconnect_gate(monkeypatch):
    captured = []

    class _Supervisor:
        def __init__(self, config, relay_port, **kw):
            captured.append(kw)

    monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", _Supervisor)

    endpoints_mod.relay_forwards_from_endpoint(
        _codespace_endpoint(kind="container")
    )

    assert captured[0]["reconnect_gate"] is None
