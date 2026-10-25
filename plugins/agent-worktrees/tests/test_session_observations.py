"""Metadata observations retain uncertainty and never project transcript bodies."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import session_observations as provider


def _event(kind, data=None, **extra):
    return {"type": kind, "data": data or {}, **extra}


@pytest.fixture
def history(tmp_path, monkeypatch):
    root = tmp_path / "session-state"
    entry = root / "session-1"
    entry.mkdir(parents=True)
    path = entry / "events.jsonl"
    monkeypatch.setattr(provider.sessions, "_session_state_dir", lambda: root)

    def write(events, suffix=b""):
        path.write_bytes(
            b"".join(json.dumps(event).encode() + b"\n" for event in events) + suffix,
        )
        return path

    return write


def test_full_history_accumulates_many_starts_not_just_tail(history):
    events = [_event("session.start")]
    events += [
        _event("tool.execution_start", {"toolName": "read", "toolCallId": f"c-{i}"})
        for i in range(6000)
    ]
    events += [_event("assistant.message", {"content": "private" * 20000})]
    path = history(events)
    before = path.stat()
    result = provider.session_observations("session-1")
    assert result["availability"] == "available"
    assert result["history"] == {
        "scope": "retained_file", "complete": True,
        "session_start_records": 1, "valid_records": 6002,
    }
    assert result["tools"]["started_records"] == 6000
    assert result["tools"]["by_name"]["read"]["started_records"] == 6000
    assert result["source"]["snapshot_bytes"] == path.stat().st_size
    assert result["source"]["observed_bytes"] == path.stat().st_size
    assert before.st_mtime_ns == path.stat().st_mtime_ns
    assert list(path.parent.iterdir()) == [path]
    assert result["pending_input"] == {"availability": "unknown"}
    assert "private" not in json.dumps(result)


def test_record_counts_include_repeats_and_correlate_only_valid_ids(history):
    history([
        _event("tool.execution_complete", {"toolCallId": "c"}),
        _event("tool.execution_start", {"toolCallId": "c", "toolName": "read"}),
        _event("tool.execution_start", {"toolCallId": "c", "toolName": "read"}),
        _event("tool.execution_complete", {"toolName": "write"}),
        _event("tool.execution_complete", {"parentToolCallId": "c"}),
        _event("tool.execution_start", {"toolCallId": [], "toolName": "bad\nname"}),
        _event("tool.execution_complete", {"toolCallId": "unknown"}),
        _event("tool.execution_start", {"turnId": "turn", "toolName": "read"}),
    ])
    result = provider.session_observations("session-1")
    assert result["history"]["session_start_records"] == 0
    assert result["tools"] == {
        "started_records": 4, "completed_records": 4,
        "by_name": {
            "read": {"started_records": 3, "completed_records": 1},
            "write": {"started_records": 0, "completed_records": 1},
        },
        "unattributed_records": {"started": 1, "completed": 2},
        "conflicting_tool_call_ids": 0,
    }


def test_conflicting_correlation_is_unattributed(history):
    history([
        _event("tool.execution_start", {"toolCallId": "c", "toolName": "read"}),
        _event("tool.execution_start", {"toolCallId": "c", "toolName": "write"}),
        _event("tool.execution_complete", {"toolCallId": "c"}),
    ])
    tools = provider.session_observations("session-1")["tools"]
    assert tools["unattributed_records"]["completed"] == 1
    assert tools["conflicting_tool_call_ids"] == 1


def test_subagents_use_only_actual_ids_and_reported_lifecycle(history):
    history([
        _event("tool.execution_start", {"parentToolCallId": "not-an-agent"}),
        _event("subagent.started", {"agentName": "research", "parentToolCallId": "p",
                                   "model": "example", "agentDisplayName": "Research"},
               agentId="a"),
        _event("subagent.completed", {"durationMs": 23, "totalToolCalls": 4,
                                     "error": "private-error"}, agentId="a"),
        _event("subagent.started", {"parentToolCallId": "p"}),
        _event("subagent.failed", {"durationMs": -1, "totalToolCalls": True,
                                  "model": ["invalid"]}, agentId="b"),
        _event("subagent.started", {"agentName": "invalid\nname"}, agentId=[]),
        _event("subagent.completed", {"durationMs": "45", "totalToolCalls": 2.5},
               agentId="b"),
    ])
    result = provider.session_observations("session-1")
    agents = result["subagents"]
    assert agents["availability"] == "observed"
    assert agents["runtime_continuity"] == "unknown"
    assert agents["unattributed_records"] == 2
    assert agents["lifecycle_records"] == {"started": 3, "completed": 2, "failed": 1}
    a, b = agents["agents"]
    assert a == {
        "agent_id": "a", "last_reported_lifecycle": "completed",
        "parent_tool_call_id": "p", "name": "research", "display_name": "Research",
        "model": "example", "reported_duration_ms": 23, "reported_tool_calls": 4,
        "lifecycle_records": 2,
    }
    assert b["reported_duration_ms"] is None and b["reported_tool_calls"] is None
    assert b["model"] is None
    assert "private-error" not in json.dumps(result)
    assert "retired" not in json.dumps(result) and "released" not in json.dumps(result)


@pytest.mark.parametrize("suffix,issue", [
    (b'{"type": "tool.execution_start"', "partial_final_record"),
    (b'{"content":"private-malformed"} invalid\n', "malformed_record"),
    (b'\xff\n', "malformed_record"),
    (b'[]\n', "malformed_record"),
])
def test_bad_records_are_partial_sanitized_evidence(history, suffix, issue):
    history([_event("session.start")], suffix)
    result = provider.session_observations("session-1")
    assert result["availability"] == "partial"
    assert result["history"]["complete"] is False
    assert result["issues"] == {issue: 1}
    assert "private-malformed" not in json.dumps(result)


def test_large_record_is_explicit_partial_and_later_records_accumulate(history):
    history([
        _event("assistant.message", {"content": "x" * (provider._RECORD_BYTES + 10)}),
        _event("tool.execution_start", {"toolName": "read"}),
    ])
    result = provider.session_observations("session-1")
    assert result["availability"] == "partial"
    assert result["issues"] == {"oversized_record": 1}
    assert result["tools"]["started_records"] == 1


@pytest.mark.parametrize("content", [b"", b"private invalid\n", b'{"type":'])
def test_no_readable_records_are_unavailable_not_zero_success(history, content):
    history([], content)
    result = provider.session_observations("session-1")
    assert result["availability"] == "unavailable"
    assert result["tools"] is None and result["subagents"] is None
    assert result["issues"]


def test_missing_and_unreadable_are_sanitized(history, monkeypatch):
    path = history([_event("session.start")])
    path.unlink()
    assert provider.session_observations("session-1")["availability"] == "unavailable"
    history([_event("session.start")])
    original = Path.open

    def denied(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("private path and transcript")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    result = provider.session_observations("session-1")
    assert result["issues"] == {"file_unreadable": 1}
    assert result["tools"] is None
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("change", ["grow", "truncate", "replace", "remove"])
def test_snapshot_detects_changes_without_reading_appended_events(history, monkeypatch, change):
    path = history([_event("session.start")])
    original = provider._scan
    original_stat = Path.stat

    def scan(stream, size, observations, issues):
        if change == "grow":
            with path.open("ab") as writer:
                writer.write(json.dumps(_event("tool.execution_start")).encode() + b"\n")
        result = original(stream, size, observations, issues)
        if change == "truncate":
            path.write_bytes(b"")
        elif change == "replace":
            def replaced(candidate, *args, **kwargs):
                stat = original_stat(candidate, *args, **kwargs)
                if candidate == path:
                    return SimpleNamespace(
                        st_dev=stat.st_dev, st_ino=stat.st_ino + 1,
                        st_size=stat.st_size, st_mtime_ns=stat.st_mtime_ns,
                        st_ctime_ns=stat.st_ctime_ns,
                    )
                return stat
            monkeypatch.setattr(Path, "stat", replaced)
        elif change == "remove":
            def removed(candidate, *args, **kwargs):
                if candidate == path:
                    raise FileNotFoundError
                return original_stat(candidate, *args, **kwargs)
            monkeypatch.setattr(Path, "stat", removed)
        return result

    monkeypatch.setattr(provider, "_scan", scan)
    result = provider.session_observations("session-1")
    assert result["availability"] == "partial"
    assert not result["history"]["complete"]
    assert result["tools"]["started_records"] == 0
    assert result["source"]["observed_bytes"] == result["source"]["snapshot_bytes"]
    assert result["issues"]


@pytest.mark.parametrize("session_id", [
    "../escape", "..", "a/b", "a\\b", "C:escape", "", "a\x00b", "\\server", "/root",
])
def test_unsafe_ids_never_reach_filesystem(monkeypatch, session_id):
    def unexpected():
        pytest.fail("unsafe ID reached state path resolution")

    monkeypatch.setattr(provider.sessions, "_session_state_dir", unexpected)
    result = provider.session_observations(session_id)
    assert result["issues"] == {"invalid_session_id": 1}
    assert result["tools"] is None


def test_symlink_escape_is_unavailable(history, tmp_path, monkeypatch):
    path = history([])
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "events.jsonl").write_bytes(b'{}\n')
    link = path.parent.parent / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink permission unavailable")
    result = provider.session_observations("linked")
    assert result["issues"] == {"unsafe_session_path": 1}


@pytest.mark.parametrize("failure", ["short_read", "read_error"])
def test_short_read_is_partial_without_fabricating_remaining_counts(history, monkeypatch, failure):
    path = history([_event("session.start"), _event("tool.execution_start")])
    original = Path.open

    class Short:
        def __init__(self, stream):
            self.stream, self.read_once = stream, False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            if self.read_once:
                if failure == "read_error":
                    raise OSError("private read error")
                return b""
            self.read_once = True
            return self.stream.readline()

    def opened(candidate, *args, **kwargs):
        stream = original(candidate, *args, **kwargs)
        return Short(stream) if candidate == path else stream

    monkeypatch.setattr(Path, "open", opened)
    result = provider.session_observations("session-1")
    assert result["availability"] == "partial"
    assert result["issues"] == {failure: 1}
    assert result["tools"]["started_records"] == 0
    assert 0 < result["source"]["observed_bytes"] < result["source"]["snapshot_bytes"]
    assert result["history"]["valid_records"] == 1
    assert "private read error" not in json.dumps(result)


@pytest.mark.parametrize("call_id,expected", [("launch", "launch"), ([], "fallback")])
def test_lifecycle_launch_call_preserves_parent_metadata_without_inventing_agent_identity(
    history, call_id, expected,
):
    history([
        _event("subagent.started", {"toolCallId": call_id, "parentToolCallId": "fallback"},
               agentId="actual-agent"),
        _event("subagent.completed", {"toolCallId": "not-an-agent"}),
    ])
    agents = provider.session_observations("session-1")["subagents"]
    assert len(agents["agents"]) == 1
    assert agents["agents"][0]["agent_id"] == "actual-agent"
    assert agents["agents"][0]["parent_tool_call_id"] == expected
    assert agents["unattributed_records"] == 1


def test_unknown_event_shapes_and_restarts_do_not_invent_observations(history):
    history([
        _event("session.started"), _event("session.start"), _event("session.start"),
        _event("subagent.unknown", agentId="a"), {"type": [], "data": {}},
        _event("assistant.message", {"content": "private"}),
        _event("permission.requested", {"question": "private"}),
        _event("tool.execution_complete", ["invalid"]),
    ])
    result = provider.session_observations("session-1")
    assert result["history"]["session_start_records"] == 2
    assert result["tools"]["completed_records"] == 1
    assert result["subagents"]["agents"] == []
    assert result["subagents"]["availability"] == "unknown"
    assert result["pending_input"]["availability"] == "unknown"
