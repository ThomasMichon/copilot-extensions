"""Tests for the generic telemetry emission seam (pluggable, no-op default)."""

from __future__ import annotations

import json

from agent_bridge import telemetry
from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.live_representation import LiveEventStore


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


def test_health_events_are_not_malformed_state_transitions() -> None:
    ev = telemetry.session_lifecycle_event(
        "context_warning",
        "s",
        {
            "context_pct": 81.5,
            "threshold": 80,
            "message": "private",
            "status": " RUNNING ",
        },
        from_state="idle",
    )
    assert ev["kind"] == "event"
    assert ev["name"] == "session_health"
    assert "from" not in ev
    assert "to" not in ev
    assert ev["context_pct"] == 81.5
    assert ev["threshold"] == 80
    assert "message" not in ev


def test_event_log_emits_replayable_conversation_and_tool_transitions() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        log = EventLog(
            session_id="sess-9",
            acp_session_id="acp-9",
            worktree_id="wt-9",
        )
        log.append("session_state_changed", {"status": "idle"})
        log.append("user_message", {"content": "a secret prompt"})
        log.append("session_state_changed", {"status": "running", "turn_index": 1})
        log.append(
            "tool_call_start",
            {
                "tool_call_id": "tool-1",
                "kind": "read",
                "title": "secret title",
                "raw_input": {"path": "/secret"},
            },
        )
        log.append(
            "tool_call_update",
            {
                "tool_call_id": "tool-1",
                "status": "completed",
                "raw_output": {"content": "secret result"},
            },
        )
        log.append("turn_complete", {"stop_reason": "end_turn"})
        log.append("session_state_changed", {"status": "idle"})

        conversation = [r for r in seen if r["name"] == "conversation"]
        assert [(r.get("from"), r["to"], r["event"]) for r in conversation] == [
            (None, "idle", "session_state_changed"),
            ("idle", "sending", "user_message"),
            ("sending", "responding", "session_state_changed"),
            ("responding", "end-turn", "turn_complete"),
            ("end-turn", "idle", "session_state_changed"),
        ]
        tools = [r for r in seen if r["name"] == "tool_call"]
        assert [(r.get("from"), r["to"]) for r in tools] == [
            (None, "running"),
            ("running", "completed"),
        ]
        assert all(r["session_id"] == "sess-9" for r in seen)
        assert all(r["acp_session_id"] == "acp-9" for r in seen)
        assert all(r["worktree_id"] == "wt-9" for r in seen)
        serialized = json.dumps(seen)
        assert "secret prompt" not in serialized
        assert "secret title" not in serialized
        assert "/secret" not in serialized
        assert "secret result" not in serialized
        assert all(r["log_epoch"] for r in conversation + tools)
    finally:
        _reset()


def test_cancel_and_error_paths_settle_back_to_idle() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        log = EventLog(session_id="s")
        log.append("session_state_changed", {"status": "idle"})
        log.append("user_message", {"content": "cancel me"})
        log.append("session_state_changed", {"status": "running"})
        log.append(
            "tool_call_start", {"tool_call_id": "cancelled-tool", "kind": "read"}
        )
        log.append("turn_complete", {"stop_reason": "cancelled"})
        log.append("session_state_changed", {"status": "idle"})
        log.append("user_message", {"content": "fail me"})
        log.append("session_state_changed", {"status": "running"})
        log.append(
            "tool_call_start", {"tool_call_id": "failed-tool", "kind": "read"}
        )
        log.append("error", {"message": "private failure detail"})
        log.append("session_state_changed", {"status": "idle"})

        conversation = [r for r in seen if r["name"] == "conversation"]
        assert [r["to"] for r in conversation] == [
            "idle", "sending", "responding", "cancelled", "idle",
            "sending", "responding", "error", "idle",
        ]
        tools = [r for r in seen if r["name"] == "tool_call"]
        assert [(r["tool_call_id"], r["to"]) for r in tools] == [
            ("cancelled-tool", "running"),
            ("cancelled-tool", "cancelled"),
            ("failed-tool", "running"),
            ("failed-tool", "error"),
        ]
        assert "private failure detail" not in json.dumps(seen)
    finally:
        _reset()


def test_free_text_stop_reason_and_tool_status_are_bounded() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        log = EventLog(session_id="s")
        log.append("user_message", {"content": "hidden"})
        log.append("session_state_changed", {"status": "running"})
        log.append(
            "tool_call_start", {"tool_call_id": "t", "kind": "execute"}
        )
        log.append(
            "tool_call_update",
            {"tool_call_id": "t", "status": "FAILED: private detail"},
        )
        log.append(
            "tool_call_update", {"tool_call_id": "t", "status": "failed"}
        )
        log.append(
            "turn_complete", {"stop_reason": "error: private exception"}
        )
        serialized = json.dumps(seen)
        assert "private detail" not in serialized
        assert "private exception" not in serialized
        tool = [r for r in seen if r["name"] == "tool_call"][-1]
        assert tool["tool_status"] == "error"
        turn = [r for r in seen if r["name"] == "conversation"][-1]
        assert turn["stop_reason"] == "other"
    finally:
        _reset()


def test_rebuild_restores_reducer_without_reemitting_history() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        log = EventLog(session_id="s")
        count = log.rebuild(
            [
                ("session_state_changed", {"status": "idle"}),
                ("user_message", {"content": "hidden"}),
                ("session_state_changed", {"status": "running"}),
                ("turn_complete", {"stop_reason": "end_turn"}),
            ]
        )
        assert count == 4
        assert len(seen) == 1
        assert seen[0]["kind"] == "event"
        assert seen[0]["event"] == "log_rebuilt"
        epoch = seen[0]["log_epoch"]
        assert seen[0]["conversation_state"] == "end-turn"
        assert seen[0]["active_tool_call_ids"] == []

        log.append("session_state_changed", {"status": "idle"})
        conversation = [r for r in seen if r["name"] == "conversation"]
        assert len(conversation) == 1
        assert conversation[0]["from"] == "end-turn"
        assert conversation[0]["to"] == "idle"
        assert conversation[0]["event_id"] == 5
        assert conversation[0]["log_epoch"] == epoch
    finally:
        _reset()


def test_empty_rebuild_first_event_epoch_survives_restart(tmp_path) -> None:
    _reset()
    db = Database(tmp_path / "empty-rebuild.db")
    db.create_session("s", "test", None, ".", "local", "idle", 1.0)
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        log = EventLog(db=db, session_id="s")
        log.rebuild([])
        marker = seen.pop()
        assert marker["event"] == "log_rebuilt"
        assert "log_epoch" not in marker

        log.append("session_state_changed", {"status": "idle"})
        first_epoch = seen[-1]["log_epoch"]
        db.flush()

        seen.clear()
        restored = EventLog.from_db(db, "s")
        restored.append("user_message", {"content": "hidden"})
        assert seen[-1]["log_epoch"] == first_epoch
    finally:
        _reset()
        db.close()


def test_identity_can_be_filled_after_event_log_construction() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        log = EventLog(session_id="s")
        log.set_telemetry_identity(acp_session_id="acp", worktree_id="wt")
        log.append("session_state_changed", {"status": "idle"})
        assert all(r["acp_session_id"] == "acp" for r in seen)
        assert all(r["worktree_id"] == "wt" for r in seen)
    finally:
        _reset()


def test_tool_ids_preserve_case_and_duplicate_terminal_is_suppressed() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        log = EventLog(session_id="s")
        log.append(
            "tool_call_start",
            {"tool_call_id": "Tool_AbC-123", "kind": "read"},
        )
        terminal = {
            "tool_call_id": "Tool_AbC-123",
            "status": "completed",
        }
        log.append("tool_call_update", terminal)
        log.append("tool_call_update", terminal)
        tools = [r for r in seen if r["name"] == "tool_call"]
        assert [r["tool_call_id"] for r in tools] == [
            "Tool_AbC-123",
            "Tool_AbC-123",
        ]
        assert [(r.get("from"), r["to"]) for r in tools] == [
            (None, "running"),
            ("running", "completed"),
        ]
    finally:
        _reset()


def test_mid_turn_user_echo_does_not_move_responding_back_to_sending() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        log = EventLog(session_id="s")
        log.append("user_message", {"content": "submitted"})
        log.append("session_state_changed", {"status": "running"})
        log.append("user_message", {"content": "echoed by ACP"})
        log.append("turn_complete", {"stop_reason": "end_turn"})
        assert [
            (r.get("from"), r["to"])
            for r in seen
            if r["name"] == "conversation"
        ] == [
            (None, "sending"),
            ("sending", "responding"),
            ("responding", "end-turn"),
        ]
    finally:
        _reset()


def test_from_db_restores_state_without_reemitting_history(tmp_path) -> None:
    _reset()
    db = Database(tmp_path / "events.db")
    now = 1.0
    db.create_session(
        "s",
        "test",
        None,
        target_dir=".",
        target_type="local",
        status="idle",
        target_json=None,
        now=now,
    )
    original = EventLog(db=db, session_id="s")
    original.append("session_state_changed", {"status": "idle"})
    original.append("user_message", {"content": "hidden"})
    original.append("session_state_changed", {"status": "running"})
    original.append("turn_complete", {"stop_reason": "end_turn"})
    db.flush()

    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        restored = EventLog.from_db(db, "s")
        assert seen == []
        restored.append("session_state_changed", {"status": "idle"})
        conversation = [r for r in seen if r["name"] == "conversation"]
        assert len(conversation) == 1
        assert conversation[0]["from"] == "end-turn"
        assert conversation[0]["to"] == "idle"
    finally:
        _reset()
        db.close()


def test_represented_source_is_attributed() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        log = EventLog(
            session_id="represented-1",
            telemetry_source="represented",
        )
        log.append("user_message", {"content": "hidden"})
        log.append("agent_message", {"text": "hidden"})
        log.append("turn_complete", {"stop_reason": "end_turn"})
        conversation = [r for r in seen if r["name"] == "conversation"]
        assert [r["to"] for r in conversation] == [
            "sending", "responding", "end-turn", "idle",
        ]
        assert all(r["session_id"] == "represented-1" for r in conversation)
        assert all(r["source"] == "represented" for r in conversation)
    finally:
        _reset()


def test_represented_store_threads_worktree_identity() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        store = LiveEventStore()
        log = store.get_or_create("represented-1", worktree_id="wt-1")
        log.append("turn_complete", {"stop_reason": "end_turn"})
        assert seen
        assert all(record["worktree_id"] == "wt-1" for record in seen)
    finally:
        _reset()


def test_bounded_causal_trigger_is_preserved() -> None:
    _reset()
    seen: list[dict] = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        log = EventLog(session_id="s")
        log.append(
            "session_state_changed",
            {"status": "stopped", "trigger": "daemon_restart"},
        )
        assert seen
        assert all(record["trigger"] == "daemon_restart" for record in seen)
    finally:
        _reset()


def test_event_log_append_is_unaffected_without_sink() -> None:
    _reset()
    log = EventLog(session_id="s")
    evt = log.append("session_state_changed", {"status": "idle"})
    # Append still returns the event and records it, sink or not.
    assert evt.event == "session_state_changed"
    assert log.latest_id == evt.id


# --- sink loader (config-driven install) -----------------------------------

_LOADED: list[dict] = []


def _make_recording_sink():  # module-level factory referenced by spec
    def _sink(event: dict) -> None:
        _LOADED.append(event)
    return _sink


def _make_bad_sink():
    return "not callable"


def test_load_sink_from_spec_installs_factory_result() -> None:
    _reset()
    sink = telemetry.load_sink_from_spec(f"{__name__}:_make_recording_sink")
    assert callable(sink)


def test_load_sink_from_spec_fail_open_on_bad_specs() -> None:
    _reset()
    assert telemetry.load_sink_from_spec("") is None
    assert telemetry.load_sink_from_spec("no-colon") is None
    assert telemetry.load_sink_from_spec("nonexistent.module:factory") is None
    assert telemetry.load_sink_from_spec(f"{__name__}:_make_bad_sink") is None


def test_load_sink_from_env_installs_and_delivers(monkeypatch) -> None:
    _reset()
    _LOADED.clear()
    monkeypatch.setenv(telemetry.SINK_ENV_VAR, f"{__name__}:_make_recording_sink")
    try:
        assert telemetry.load_sink_from_env() is True
        assert telemetry.has_sink() is True
        telemetry.emit({"kind": "state_transition", "to": "idle"})
        assert _LOADED == [{"kind": "state_transition", "to": "idle"}]
    finally:
        _reset()


def test_load_sink_from_env_noop_when_unset(monkeypatch) -> None:
    _reset()
    monkeypatch.delenv(telemetry.SINK_ENV_VAR, raising=False)
    assert telemetry.load_sink_from_env() is False
    assert telemetry.has_sink() is False


# --- sink loader (config-file install, env-free) ----------------------------


def test_load_sink_from_config_installs_and_delivers(tmp_path) -> None:
    _reset()
    _LOADED.clear()
    cfg = tmp_path / "telemetry.json"
    cfg.write_text(f'{{"sink": "{__name__}:_make_recording_sink"}}', encoding="utf-8")
    try:
        assert telemetry.load_sink_from_config(cfg) is True
        assert telemetry.has_sink() is True
        telemetry.emit({"kind": "state_transition", "to": "idle"})
        assert _LOADED == [{"kind": "state_transition", "to": "idle"}]
    finally:
        _reset()


def test_load_sink_from_config_noop_when_file_missing(tmp_path) -> None:
    _reset()
    assert telemetry.load_sink_from_config(tmp_path / "absent.json") is False
    assert telemetry.has_sink() is False


def test_load_sink_from_config_fail_open_on_bad_json(tmp_path) -> None:
    _reset()
    cfg = tmp_path / "telemetry.json"
    cfg.write_text("{not valid json", encoding="utf-8")
    assert telemetry.load_sink_from_config(cfg) is False
    assert telemetry.has_sink() is False


def test_load_sink_from_config_fail_open_on_missing_or_bad_sink(tmp_path) -> None:
    _reset()
    for payload in ('{}', '{"sink": ""}', '{"sink": "no-colon"}', '[]'):
        cfg = tmp_path / "telemetry.json"
        cfg.write_text(payload, encoding="utf-8")
        assert telemetry.load_sink_from_config(cfg) is False
        assert telemetry.has_sink() is False


def test_load_sink_from_config_default_path_honors_config_dir(monkeypatch, tmp_path) -> None:
    _reset()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    expected = tmp_path / telemetry.CONFIG_FILENAME
    assert telemetry._default_config_path() == expected


# --- built-in spool sink (dependency-free, out-of-process drain) ------------


def _read_spool(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_make_spool_sink_appends_jsonl_with_ts(tmp_path) -> None:
    _reset()
    spool = tmp_path / "agent-bridge.spool"
    sink = telemetry.make_spool_sink(spool)
    assert callable(sink)
    sink({"kind": "state_transition", "name": "session", "event": "session_state_changed", "to": "idle"})
    rows = _read_spool(spool)
    assert len(rows) == 1
    assert rows[0]["name"] == "session"
    assert rows[0]["to"] == "idle"
    assert isinstance(rows[0]["ts"], int)


def test_make_spool_sink_preserves_existing_ts(tmp_path) -> None:
    _reset()
    spool = tmp_path / "s.spool"
    telemetry.make_spool_sink(spool)({"kind": "event", "name": "session", "ts": 456})
    assert _read_spool(spool)[0]["ts"] == 456


def test_make_spool_sink_noop_without_path() -> None:
    _reset()
    assert telemetry.make_spool_sink("") is None
    assert telemetry.make_spool_sink(None) is None


def test_make_spool_sink_reads_path_from_config(tmp_path) -> None:
    _reset()
    spool = tmp_path / "declared.spool"
    cfg = tmp_path / "telemetry.json"
    cfg.write_text(
        json.dumps({"sink": telemetry.SPOOL_SINK_SPEC, "spool": str(spool)}), encoding="utf-8"
    )
    assert telemetry._configured_spool_path(cfg) == str(spool)


def test_spool_sink_selected_end_to_end_via_config(tmp_path, monkeypatch) -> None:
    _reset()
    spool = tmp_path / "e2e.spool"
    cfg = tmp_path / "telemetry.json"
    cfg.write_text(
        json.dumps({"sink": telemetry.SPOOL_SINK_SPEC, "spool": str(spool)}), encoding="utf-8"
    )
    monkeypatch.setattr(telemetry, "_default_config_path", lambda: cfg)
    try:
        assert telemetry.load_sink_from_config(cfg) is True
        assert telemetry.has_sink() is True
        telemetry.emit({"kind": "state_transition", "name": "session", "to": "idle"})
        rows = _read_spool(spool)
        assert rows and rows[0]["to"] == "idle"
    finally:
        _reset()


def test_spool_sink_fail_open_on_bad_path(tmp_path) -> None:
    _reset()
    sink = telemetry.make_spool_sink(tmp_path)  # a dir -> unwritable as a file
    assert callable(sink)
    sink({"kind": "event", "name": "session"})  # must not raise
