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


def test_load_sink_from_spec_rejects_non_callable_factory_result() -> None:
    _reset()
    assert telemetry.load_sink_from_spec(f"{__name__}:_make_bad_sink") is None


def test_load_sink_from_spec_fail_open_on_bad_specs() -> None:
    _reset()
    assert telemetry.load_sink_from_spec("") is None
    assert telemetry.load_sink_from_spec("no-colon") is None
    assert telemetry.load_sink_from_spec("nonexistent.module:factory") is None
    assert telemetry.load_sink_from_spec(f"{__name__}:no_such_attr") is None


def test_load_sink_from_env_installs_and_delivers(monkeypatch) -> None:
    _reset()
    _LOADED.clear()
    monkeypatch.setenv(telemetry.SINK_ENV_VAR, f"{__name__}:_make_recording_sink")
    try:
        assert telemetry.load_sink_from_env() is True
        assert telemetry.has_sink() is True
        telemetry.emit({"kind": "state_transition", "to": "claimed"})
        assert _LOADED == [{"kind": "state_transition", "to": "claimed"}]
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
        telemetry.emit({"kind": "state_transition", "to": "claimed"})
        assert _LOADED == [{"kind": "state_transition", "to": "claimed"}]
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


def test_load_sink_from_config_default_path_is_convention() -> None:
    _reset()
    from pathlib import Path

    expected = Path.home() / ".agent-dispatch" / telemetry.CONFIG_FILENAME
    assert telemetry._default_config_path() == expected


# --- built-in spool sink (dependency-free, out-of-process drain) ------------

import json  # noqa: E402


def _read_spool(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_make_spool_sink_appends_jsonl_with_ts(tmp_path) -> None:
    _reset()
    spool = tmp_path / "agent-dispatch.spool"
    sink = telemetry.make_spool_sink(spool)
    assert callable(sink)
    sink({"kind": "state_transition", "name": "task", "event": "task.claimed", "to": "claimed"})
    sink({"kind": "state_transition", "name": "task", "event": "task.completed", "to": "completed"})
    rows = _read_spool(spool)
    assert len(rows) == 2
    assert rows[0]["event"] == "task.claimed"
    assert rows[0]["to"] == "claimed"
    assert isinstance(rows[0]["ts"], int)  # emit ts stamped when absent


def test_result_recorded_telemetry_is_distinct_from_completion() -> None:
    record = telemetry.task_lifecycle_event(
        "task.result_recorded",
        {"id": "t1", "status": "completed", "result": {"secret": "excluded"}},
    )

    assert record["event"] == "task.result_recorded"
    assert record["to"] == "completed"
    assert "result" not in record


def test_make_spool_sink_preserves_existing_ts(tmp_path) -> None:
    _reset()
    spool = tmp_path / "s.spool"
    telemetry.make_spool_sink(spool)({"kind": "event", "name": "task", "ts": 123})
    assert _read_spool(spool)[0]["ts"] == 123


def test_make_spool_sink_noop_without_path(monkeypatch) -> None:
    _reset()
    # Isolate from any ambient telemetry config on the host: with no explicit
    # path AND no configured spool, the factory must fail open to None. Stub the
    # config lookup so a real multi-machine config file can't leak a path in.
    monkeypatch.setattr(telemetry, "_configured_spool_path", lambda: None)
    assert telemetry.make_spool_sink("") is None
    assert telemetry.make_spool_sink(None) is None  # no config file -> None


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
    # The built-in factory reads its spool path from the convention config file;
    # point the default there so the zero-arg factory resolves it.
    monkeypatch.setattr(telemetry, "_default_config_path", lambda: cfg)
    try:
        assert telemetry.load_sink_from_config(cfg) is True
        assert telemetry.has_sink() is True
        telemetry.emit({"kind": "state_transition", "name": "task", "to": "claimed"})
        rows = _read_spool(spool)
        assert rows and rows[0]["to"] == "claimed"
    finally:
        _reset()


def test_spool_sink_fail_open_on_bad_path(tmp_path) -> None:
    _reset()
    # A directory path is unwritable as a file -> the sink swallows and drops.
    sink = telemetry.make_spool_sink(tmp_path)
    assert callable(sink)
    sink({"kind": "event", "name": "task"})  # must not raise
