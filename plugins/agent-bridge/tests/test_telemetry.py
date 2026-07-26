"""Tests for the generic telemetry emission seam (pluggable, no-op default)."""

from __future__ import annotations

from agent_bridge import telemetry
from agent_bridge.events import EventLog


def _reset() -> None:
    telemetry.clear_telemetry_sink()


def test_emit_is_noop_without_sink() -> None:
    _reset()
    assert telemetry.has_sink() is False
    telemetry.emit({"kind": "state_transition", "name": "session"})


def test_registered_sink_receives_events() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        assert telemetry.has_sink() is True
        telemetry.emit({"kind": "state_transition", "name": "session", "to": "idle"})
        assert seen == [{"kind": "state_transition", "name": "session", "to": "idle"}]
    finally:
        _reset()


def test_emit_is_fail_open_when_sink_raises() -> None:
    _reset()

    def _boom(_event: dict) -> None:
        raise RuntimeError("sink exploded")

    telemetry.set_telemetry_sink(_boom)
    try:
        telemetry.emit({"kind": "state_transition"})  # must not raise
    finally:
        _reset()


def test_session_lifecycle_event_carries_state_only() -> None:
    _reset()
    ev = telemetry.session_lifecycle_event(
        "session_state_changed", "sess-1", {"status": "idle", "message": "hello there"}
    )
    assert ev["kind"] == "state_transition"
    assert ev["name"] == "session"
    assert ev["event"] == "session_state_changed"
    assert ev["session_id"] == "sess-1"
    assert ev["to"] == "idle"
    # Redact-by-construction: message/content never surfaces.
    assert "message" not in ev


def test_lifecycle_events_only_forwarded_from_event_log() -> None:
    """EventLog.append forwards lifecycle events to the sink but not content
    events (user messages, tool calls)."""
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        log = EventLog(session_id="sess-9")
        # A content event -> NOT forwarded to telemetry.
        log.append("user_message", {"content": "a secret prompt"})
        assert seen == []
        # A lifecycle event -> forwarded as a generic state-transition.
        log.append("session_state_changed", {"status": "running"})
        assert len(seen) == 1
        assert seen[0]["event"] == "session_state_changed"
        assert seen[0]["session_id"] == "sess-9"
        assert seen[0]["to"] == "running"
        # A content event carrying no status must never leak its content.
        log.append("error", {"message": "boom"})
        assert len(seen) == 2
        assert seen[1]["event"] == "error"
        assert "message" not in seen[1]
    finally:
        _reset()


def test_event_log_append_is_unaffected_without_sink() -> None:
    _reset()
    log = EventLog(session_id="s")
    evt = log.append("session_state_changed", {"status": "idle"})
    # Append still returns the event and records it, sink or not.
    assert evt.event == "session_state_changed"
    assert log.latest_id == evt.id
