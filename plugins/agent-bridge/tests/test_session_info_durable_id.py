"""Tests for ``SessionInfo.durable_session_id`` -- the field every consumer
should use for anything that outlives the current request (a deep link, a
dispatch-task binding, a cross-service correlation key), instead of
``session_id`` directly, which is only durable for a cold-store-sourced
session.

Traces back to a real incident: agent-dispatch's ``owner_session_id`` was
captured from agent-bridge's own ephemeral live-session ``session_id``
instead of the durable ``acp_session_id``, breaking every downstream
"View reviewer" deep link (copilot-extensions PR #2964).
"""

from __future__ import annotations

from agent_bridge.cold_store import ColdStoreSession
from agent_bridge.cold_store_views import cold_store_session_info
from agent_bridge.events import EventLog
from agent_bridge.routes.sessions import _persisted_session_info, _session_info
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget


def _live_session(*, acp_session_id: str | None) -> Session:
    session = Session("bridge-escrow-id", "name", SpawnTarget(type="local", cwd="/tmp/x"))
    session.event_log = EventLog()
    session.acp_session_id = acp_session_id
    return session


class TestLiveSessionInfo:
    def test_prefers_acp_session_id_when_known(self) -> None:
        session = _live_session(acp_session_id="11111111-1111-1111-1111-111111111111")
        info = _session_info(session)
        assert info.session_id == "bridge-escrow-id"
        assert info.acp_session_id == "11111111-1111-1111-1111-111111111111"
        assert info.durable_session_id == "11111111-1111-1111-1111-111111111111"

    def test_falls_back_to_bridge_session_id_when_acp_unknown(self) -> None:
        """A brand-new session whose CLI hasn't reported its ACP identity back
        yet -- degrade to the (non-durable) bridge id rather than leaving
        durable_session_id unresolvable."""
        session = _live_session(acp_session_id=None)
        info = _session_info(session)
        assert info.acp_session_id is None
        assert info.durable_session_id == "bridge-escrow-id"


class TestPersistedSessionInfo:
    def test_prefers_acp_session_id_when_known(self) -> None:
        row = {
            "id": "bridge-escrow-id",
            "name": "name",
            "status": "ended",
            "acp_session_id": "22222222-2222-2222-2222-222222222222",
            "created_at": 0.0,
            "updated_at": 0.0,
        }
        info = _persisted_session_info(row, daemon_running=False)
        assert info.session_id == "bridge-escrow-id"
        assert info.durable_session_id == "22222222-2222-2222-2222-222222222222"

    def test_falls_back_to_row_id_when_acp_unknown(self) -> None:
        row = {
            "id": "bridge-escrow-id",
            "name": "name",
            "status": "ended",
            "created_at": 0.0,
            "updated_at": 0.0,
        }
        info = _persisted_session_info(row, daemon_running=False)
        assert info.durable_session_id == "bridge-escrow-id"


class TestColdStoreSessionInfo:
    def test_durable_session_id_mirrors_the_real_archived_id(self) -> None:
        """Cold-store's session_id already IS the durable Copilot ACP session
        id -- no acp_session_id ambiguity to resolve for this path."""
        cold = ColdStoreSession(session_id="33333333-3333-3333-3333-333333333333")
        info = cold_store_session_info(cold)
        assert info.durable_session_id == "33333333-3333-3333-3333-333333333333"
