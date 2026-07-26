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

import json  # noqa: E402


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
