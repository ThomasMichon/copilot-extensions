"""Tests for the generic telemetry emission seam (pluggable, no-op default)."""

from __future__ import annotations

from agent_dispatch import telemetry


def _reset() -> None:
    telemetry.clear_telemetry_sink()


def test_emit_is_noop_without_sink() -> None:
    _reset()
    assert telemetry.has_sink() is False
    # No sink registered -> emit is a silent no-op (must not raise).
    telemetry.emit({"kind": "state_transition", "name": "task"})


def test_registered_sink_receives_events() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        assert telemetry.has_sink() is True
        telemetry.emit({"kind": "state_transition", "name": "task", "to": "claimed"})
        assert seen == [{"kind": "state_transition", "name": "task", "to": "claimed"}]
    finally:
        _reset()


def test_clear_sink_returns_to_noop() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    telemetry.clear_telemetry_sink()
    telemetry.emit({"x": 1})
    assert seen == []
    assert telemetry.has_sink() is False


def test_emit_is_fail_open_when_sink_raises() -> None:
    _reset()

    def _boom(_event: dict) -> None:
        raise RuntimeError("sink exploded")

    telemetry.set_telemetry_sink(_boom)
    try:
        # A raising sink must never propagate -- telemetry is best-effort.
        telemetry.emit({"kind": "state_transition"})
    finally:
        _reset()


def test_task_lifecycle_event_carries_state_only() -> None:
    _reset()
    task = {
        "id": "abc123",
        "status": "claimed",
        "repo": "example.com/acme/widget",
        "source": "context-handoff",
        "target_machine": "host-a",
        "target_worktree": "wt-1",
        "owner": "wt-1",
        "attempts": 1,
        # Sensitive fields that must NOT appear in the telemetry record:
        "prompt": "secret prompt text",
        "payload_inline": "TOKEN=deadbeef",
    }
    ev = telemetry.task_lifecycle_event("task.claimed", task)
    assert ev["kind"] == "state_transition"
    assert ev["name"] == "task"
    assert ev["event"] == "task.claimed"
    assert ev["to"] == "claimed"
    assert ev["id"] == "abc123"
    assert ev["repo"] == "example.com/acme/widget"
    assert ev["target_machine"] == "host-a"
    # Redact-by-construction: prompt/payload never surface.
    assert "prompt" not in ev
    assert "payload_inline" not in ev
    assert "payload" not in ev


def test_task_lifecycle_event_omits_absent_fields() -> None:
    _reset()
    ev = telemetry.task_lifecycle_event("task.created", {"id": "x", "status": "queued"})
    # Only present fields are carried; absent ones are omitted (not None-filled).
    assert ev["id"] == "x"
    assert ev["to"] == "queued"
    assert "owner" not in ev
    assert "target_machine" not in ev
