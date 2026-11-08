"""Real message timestamps, not workspace metadata or heartbeat freshness."""

import json
from pathlib import Path

import pytest

from agent_worktrees import sessions, sessions_messages as messages, tracking


def event(kind, at, **extra):
    return json.dumps({"type": kind, "timestamp": at, **extra}) + "\n"


def test_incremental_messages_rebuild_legacy_cache_and_wait_for_complete_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    first = event("user.message", "2026-11-07T01:00:00Z")
    path.write_text(first, encoding="utf-8")
    (tmp_path / ".aw-turns.json").write_text(json.dumps({"size": path.stat().st_size, "turns": 1}))
    assert messages.read(tmp_path) == (1, "2026-11-07T01:00:00Z")
    second = event("assistant.message", "2026-11-06T18:01:00-07:00")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(second[:-1])
    assert messages.read(tmp_path) == (1, "2026-11-07T01:00:00Z")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + event("session.usage_info", "2026-11-08T01:00:00Z"))
        handle.write(event("assistant.message", "2026-11-09T01:00:00Z", agentId="delegate"))
    assert messages.read(tmp_path) == (1, "2026-11-06T18:01:00-07:00")
    assert messages.read(tmp_path) == (1, "2026-11-06T18:01:00-07:00")
    path.write_text(event("user.message", "2026-11-07T02:00:00Z"), encoding="utf-8")
    assert messages.read(tmp_path) == (1, "2026-11-07T02:00:00Z")


def test_registered_sessions_choose_latest_real_message_even_without_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
    registry = []
    for sid, at in (("older", "2026-11-07T01:00:00Z"), ("newer", "2026-11-06T18:01:00-07:00")):
        entry = tmp_path / sid
        entry.mkdir()
        (entry / "events.jsonl").write_text(event("assistant.message", at), encoding="utf-8")
        registry.append(tracking.SessionEntry(sid, "2026-11-07T00:00:00Z"))
    detached = tmp_path / "detached"
    detached.mkdir()
    (detached / ".detached").touch()
    (detached / "events.jsonl").write_text(
        event("user.message", "2026-11-10T01:00:00Z"), encoding="utf-8",
    )
    registry.append(tracking.SessionEntry("detached", "2026-11-10T00:00:00Z"))
    record = type("Record", (), {"worktree_path": str(tmp_path / "checkout"), "sessions": registry})()
    result = sessions.scan_sessions_fast([record])
    assert result.last_message_at[record.worktree_path] == "2026-11-06T18:01:00-07:00"
    assert result.last_activity == {}


def test_message_sidecar_is_independent_of_intent_and_other_sidecar_timestamps(tmp_path):
    pulse = tmp_path / "substatus.json"
    pulse.write_text(json.dumps({
        "messageActivityVersion": 1, "lastMessageAt": "2026-11-07T01:00:00Z",
        "updatedAt": "2026-11-08T00:00:00Z", "restAt": "2026-11-09T00:00:00Z",
    }))
    assert messages.read(tmp_path) == (0, "2026-11-07T01:00:00Z")
    pulse.write_text(json.dumps({"messageActivityVersion": 2, "lastMessageAt": "2026-11-10T00:00:00Z"}))
    assert messages.read(tmp_path) == (0, None)


def test_unchanged_log_neither_decodes_history_nor_rewrites_cached_metadata(tmp_path, monkeypatch):
    (tmp_path / "events.jsonl").write_text(
        event("user.message", "2026-11-07T01:00:00Z"), encoding="utf-8",
    )
    expected = messages.read(tmp_path)
    decode = json.loads

    def decode_metadata(value, *args, **kwargs):
        assert isinstance(value, str), "unchanged event history was decoded again"
        return decode(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", decode_metadata)
    monkeypatch.setattr(
        Path, "write_text", lambda *args, **kwargs: pytest.fail("unchanged cache rewritten"),
    )
    assert messages.read(tmp_path) == expected


def test_replaced_log_cannot_reuse_an_old_files_count_or_message(tmp_path):
    log = tmp_path / "events.jsonl"
    log.write_text(event("user.message", "2026-11-07T01:00:00Z"), encoding="utf-8")
    assert messages.read(tmp_path)[0] == 1
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text(
        event("assistant.message", "2026-11-07T02:00:00Z", data={"content": "longer replacement"}),
        encoding="utf-8",
    )
    replacement.replace(log)
    assert messages.read(tmp_path) == (0, "2026-11-07T02:00:00Z")


def test_delegate_events_do_not_advance_recency_or_change_existing_total_user_turn_semantics(tmp_path):
    (tmp_path / "events.jsonl").write_text(
        event("user.message", "2026-11-07T01:00:00Z")
        + event("user.message", "2026-11-08T01:00:00Z", agentId="delegate")
        + event("assistant.message", "2026-11-08T02:00:00Z", agentId="delegate"),
        encoding="utf-8",
    )
    assert messages.read(tmp_path) == (2, "2026-11-07T01:00:00Z")
