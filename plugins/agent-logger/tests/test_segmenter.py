"""Tests for the de-multi-machine system-ized segmenter wiring.

These assert the config-driven store paths and that the three console-script
entry points are importable and callable. End-to-end collation is covered by
manual/functional checks.
"""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

import pytest

import agent_logger.segmenter.collate as collate
import agent_logger.segmenter.digest as digest
import agent_logger.segmenter.ramp_up as ramp_up
import agent_logger.segmenter.read_digest as read_digest


def test_segmenter_mains_callable() -> None:
    from agent_logger.segmenter import prepare_log

    assert callable(collate.main)
    assert callable(read_digest.main)
    assert callable(prepare_log.main)


def test_collate_default_digest_root_honors_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path))
    root = collate._default_digest_root()
    assert Path(root) == tmp_path / "session-digests"


def test_persistent_digest_root_honors_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path))
    assert read_digest._persistent_digest_root() == tmp_path / "session-digests"


def test_remote_store_root_optional(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENT_LOGGER_REMOTE_STORE", raising=False)
    assert read_digest._remote_store_root() is None
    monkeypatch.setenv("AGENT_LOGGER_REMOTE_STORE", str(tmp_path))
    assert read_digest._remote_store_root() == tmp_path


# --- streamed collation ------------------------------------------------------

def _ts(hms: str) -> str:
    return f"2026-01-01T{hms}Z"


_SESSION_EVENTS: list[object] = [
    {"type": "session.start", "timestamp": _ts("00:00:00"), "data": {
        "sessionId": "sid-1", "copilotVersion": "1.2.3", "startTime": _ts("00:00:00"),
        "context": {"cwd": "/repo", "gitRoot": "/repo", "branch": "main",
                    "headCommit": "0123456789abcdef"},
    }},
    {"type": "assistant.message", "timestamp": _ts("00:00:01"),
     "data": {"content": "orphan before any turn"}},
    {"type": "assistant.turn_start", "timestamp": _ts("00:00:02"), "data": {}},
    {"type": "assistant.message", "timestamp": _ts("00:00:03"),
     "data": {"content": "proactive hello"}},
    {"type": "user.message", "timestamp": _ts("00:01:00"), "data": {"content": "first question"}},
    {"type": "hook.start", "timestamp": _ts("00:01:01"), "data": {}},
    {"type": "assistant.message", "timestamp": _ts("00:01:02"), "data": {
        "reasoningText": "thinking",
        "content": "answer one",
        "toolRequests": [
            {"name": "report_intent", "toolCallId": "r0", "arguments": {"intent": "x"}},
            {"name": "powershell", "toolCallId": "c1",
             "arguments": {"command": "ls", "description": "List"}},
            {"name": "view", "toolCallId": "c2", "arguments": {"path": "/repo/a.py"}},
        ],
    }},
    {"type": "tool.execution_complete", "timestamp": _ts("00:01:03"),
     "data": {"toolCallId": "c1", "success": True, "result": {"content": "x" * 50}}},
    {"type": "tool.execution_complete", "timestamp": _ts("00:01:04"),
     "data": {"toolCallId": "c2", "success": False, "result": "boom"}},
    "",
    "not json",
    {"type": "user.message", "timestamp": _ts("00:02:00"), "data": {"content": "second question"}},
    {"type": "assistant.message", "timestamp": _ts("00:02:01"), "data": {"content": "answer two"}},
    {"type": "user.message", "timestamp": _ts("00:03:00"), "data": {"content": "third question"}},
    {"type": "assistant.message", "timestamp": _ts("00:03:01"), "data": {"content": "answer three"}},
]

_SESSION_START = {
    "session_id": "sid-1", "version": "1.2.3", "start_time": _ts("00:00:00"),
    "cwd": "/repo", "git_root": "/repo", "branch": "main", "head_commit": "0123456789abcdef",
}


def _turn(user: str, ts: str, *messages: str, tool_calls: list | None = None) -> dict:
    return {"user_message": user, "timestamp": _ts(ts),
            "assistant_messages": list(messages), "tool_calls": tool_calls or []}


_TURN_PROACTIVE = _turn("", "00:00:02", "proactive hello")
_TURN_FIRST = _turn("first question", "00:01:00", "*[Reasoning]:* thinking", "answer one", tool_calls=[
    {"id": "c1", "tool": "powershell", "args": "List: ls",
     "result": "x" * 20 + "\n... [truncated, 50 total chars]", "success": True},
    {"id": "c2", "tool": "view", "args": "/repo/a.py", "result": "boom", "success": False},
])
_TURN_SECOND = _turn("second question", "00:02:00", "answer two")
_TURN_THIRD = _turn("third question", "00:03:00", "answer three")


def _write_session(session_dir: Path, events: list[object]) -> Path:
    session_dir.mkdir(parents=True, exist_ok=True)
    lines = [e if isinstance(e, str) else json.dumps(e) for e in events]
    (session_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return session_dir


@pytest.mark.parametrize(
    ("cutoff", "since", "expected"),
    [
        (None, None, [_TURN_PROACTIVE, _TURN_FIRST, _TURN_SECOND, _TURN_THIRD]),
        (_ts("00:02:30"), None, [_TURN_PROACTIVE, _TURN_FIRST, _TURN_SECOND]),
        (None, _ts("00:01:30"), [_TURN_SECOND, _TURN_THIRD]),
        (_ts("00:02:30"), _ts("00:01:30"), [_TURN_SECOND]),
        (_ts("00:00:00"), None, []),
    ],
    ids=["all", "cutoff", "since", "window", "empty"],
)
def test_parse_events_turns(tmp_path: Path, cutoff, since, expected) -> None:
    session_dir = _write_session(tmp_path / "s", _SESSION_EVENTS)

    parsed = collate.parse_events(session_dir, cutoff, 20, since=since)

    assert parsed == {"session_start": _SESSION_START, "turns": expected}
    stream = collate.TurnStream(session_dir, cutoff, 20, since=since)
    assert list(stream) == expected
    assert stream.session_start == _SESSION_START
    assert stream.turn_count == len(expected)


def test_parse_events_without_transcript(tmp_path: Path) -> None:
    assert collate.parse_events(tmp_path, None, 20) == {"session_start": {}, "turns": []}


def _many_turns_session(session_dir: Path, turns: int) -> Path:
    events: list[object] = [_SESSION_EVENTS[0]]
    for i in range(turns):
        events += [
            {"type": "user.message", "timestamp": _ts("00:01:00"), "data": {"content": f"q{i}"}},
            {"type": "assistant.message", "timestamp": _ts("00:01:01"), "data": {
                "content": f"a{i}", "toolRequests": [
                    {"name": "powershell", "toolCallId": f"c{i}", "arguments": {"command": "ls"}},
                ]}},
            {"type": "tool.execution_complete", "timestamp": _ts("00:01:02"), "data": {
                "toolCallId": f"c{i}", "success": i % 3 != 0, "result": {"content": f"r{i} " * 30}}},
        ]
    return _write_session(session_dir, events)


def _read_dir(path: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(path.iterdir())}


@pytest.mark.parametrize("segment_size", [1, 400, 10**6])
def test_write_segments_streams_identically_to_a_turn_list(tmp_path: Path, segment_size: int) -> None:
    session_dir = _many_turns_session(tmp_path / "s", 12)
    ws = {"id": "sid-1", "cwd": "/repo"}
    parsed = collate.parse_events(session_dir, None, 40)
    from_list = collate.write_segments(
        ws, parsed["session_start"], [], parsed["turns"], [], None, tmp_path / "list", segment_size, 40,
    )
    stream = collate.TurnStream(session_dir, None, 40)
    from_stream = collate.write_segments(
        ws, stream.session_start, [], stream, [], None, tmp_path / "stream", segment_size, 40,
    )

    assert from_stream == from_list
    assert from_list[0] == "00-context.md" and len(from_list) > 1
    assert _read_dir(tmp_path / "stream") == _read_dir(tmp_path / "list")
    context = (tmp_path / "stream" / "00-context.md").read_text(encoding="utf-8")
    assert "- **Turns:** 12" in context and "- **Failed tool calls:** 4" in context
    assert "- **Branch:** main" in context


def _legacy_digest(ws, session_start, checkpoints, turns, snapshots, max_total) -> str:
    """The pre-streaming digest: render everything, then truncate."""
    parts = [*collate._format_metadata(ws, session_start, None),
             *collate._format_checkpoints(checkpoints), "## Conversation Transcript\n"]
    if checkpoints:
        parts += ["*Note: Checkpoints above cover earlier work. The transcript",
                  "below includes the full session for completeness, but checkpoint",
                  "content is authoritative for the periods they cover.*\n"]
    parts += [collate._format_turn(turn, i) for i, turn in enumerate(turns, 1)]
    parts += collate._format_turn_stats(collate.TurnStats.of(turns), checkpoints, snapshots)
    output = "\n".join(parts)
    if len(output) > max_total:
        output = output[:max_total] + f"\n\n... [digest truncated at {max_total} chars]"
    return output


@pytest.mark.parametrize("max_total", [10**9, 3000, 2000, 300, 0, -5])
def test_format_digest_streams_identically_to_a_turn_list(tmp_path: Path, max_total: int) -> None:
    session_dir = _many_turns_session(tmp_path / "s", 12)
    ws = {"id": "sid-1"}
    checkpoints = [{"filename": "001-cp.md", "content": "checkpoint body"}]
    parsed = collate.parse_events(session_dir, None, 40)
    expected = _legacy_digest(ws, parsed["session_start"], checkpoints, parsed["turns"], [{}], max_total)
    stream = collate.TurnStream(session_dir, None, 40)

    assert collate.format_digest(
        ws, parsed["session_start"], checkpoints, parsed["turns"], [{}], None, max_total,
    ) == expected
    assert collate.format_digest(ws, stream.session_start, checkpoints, stream, [{}], None, max_total) == expected
    assert expected.endswith(f"[digest truncated at {max_total} chars]") == (max_total != 10**9)


def _failing_after(session_dir: Path, turns: int, exc: BaseException):
    for i, turn in enumerate(collate.TurnStream(session_dir, None, 40)):
        if i == turns:
            raise exc
        yield turn


@pytest.mark.parametrize("exc", [RuntimeError("transcript read failed"), KeyboardInterrupt()])
def test_write_segments_keeps_completed_segments_when_the_turn_stream_fails(
    tmp_path: Path, capsys, exc: BaseException,
) -> None:
    session_dir = _many_turns_session(tmp_path / "s", 6)
    complete = tmp_path / "complete"
    collate.write_segments({}, {}, [], collate.TurnStream(session_dir, None, 40), [], None, complete, 1, 40)
    out = tmp_path / "out"
    out.mkdir()
    (out / "00-context.md").write_text("stale context from an earlier run", encoding="utf-8")

    with pytest.raises(type(exc)):
        collate.write_segments({}, {}, [], _failing_after(session_dir, 4, exc), [], None, out, 1, 40)

    # Turns 1-3 were in segments closed before the failure; segment 04 was
    # still open, and no 00-context.md presents the run as complete.
    survived = _read_dir(out)
    assert sorted(survived) == ["01-turns.md", "02-turns.md", "03-turns.md"]
    assert survived == {name: data for name, data in _read_dir(complete).items() if name in survived}
    assert "kept 3 completed segment(s)" in capsys.readouterr().err


def test_write_to_nas_records_no_manifest_for_an_interrupted_digest(tmp_path: Path, monkeypatch) -> None:
    session_dir = _many_turns_session(tmp_path / "s", 6)

    class FailingStream(collate.TurnStream):
        def __iter__(self):
            for i, turn in enumerate(super().__iter__()):
                if i == 4:
                    raise OSError("archive member truncated")
                yield turn

    monkeypatch.setattr(collate, "TurnStream", FailingStream)
    nas = tmp_path / "nas"
    with pytest.raises(OSError, match="truncated"):
        collate.write_to_nas(session_dir, "sid-1", "box", nas, None, 40, 1, backfill=False)

    digest = nas / "box" / "sid-1" / "full"
    assert sorted(p.name for p in digest.iterdir()) == ["01-turns.md", "02-turns.md", "03-turns.md"]
    assert not collate._has_full_digest(digest.parent)
    assert not (digest.parent / "index.yaml").exists()


@pytest.mark.parametrize("tail_turns", [0, 3, 50])
def test_ramp_up_brief_from_a_tail_matches_the_full_turn_list(tmp_path: Path, tail_turns: int) -> None:
    session_dir = _many_turns_session(tmp_path / "s", 12)
    parsed = collate.parse_events(session_dir, None, 40)
    turns = parsed["turns"]
    args = ({"id": "sid-1"}, {"cwd": "/repo"}, parsed["session_start"], [])

    expected = ramp_up._render_brief(*args, turns, [], tail_turns, tmp_path / "d", 0)
    tail = turns[-tail_turns:] if tail_turns else []
    actual = ramp_up._render_brief(
        *args, tail, [], tail_turns, tmp_path / "d", 0, turn_stats=collate.TurnStats.of(turns),
    )

    assert actual == expected


def test_build_session_digest_gives_a_charter_its_view_in_the_same_pass(tmp_path: Path) -> None:
    session_dir = _many_turns_session(tmp_path / "s", 9)
    out = tmp_path / "out"
    out.mkdir()
    (out / "99-turns.md").write_text("stale segment from an earlier run", encoding="utf-8")
    seen: list[dict] = []

    result = digest.build_session_digest(
        session_dir, out, segment_size=400, max_tool_output=40, on_turn=seen.append,
    )

    stream = collate.TurnStream(session_dir, None, 40)
    expected = collate.write_segments(
        digest.read_workspace(session_dir), stream.session_start, digest.read_checkpoints(session_dir),
        stream, digest.read_rewind_index(session_dir), None, tmp_path / "ref", 400, 40,
    )
    assert result.files == expected
    assert _read_dir(out) == _read_dir(tmp_path / "ref")
    assert [turn["user_message"] for turn in seen] == [f"q{i}" for i in range(9)]
    assert (result.stats.turns, result.stats.failed_tool_calls, result.turn_count) == (9, 3, 9)


def test_write_segments_memory_is_bounded_for_a_large_transcript(tmp_path: Path) -> None:
    session_dir = _many_turns_session(tmp_path / "s", 0)
    filler = "y" * 4000
    with open(session_dir / "events.jsonl", "a", encoding="utf-8") as fh:
        i = 0
        while fh.tell() < 16 * 1024 * 1024:
            fh.write(json.dumps({"type": "user.message", "timestamp": _ts("00:01:00"),
                                 "data": {"content": f"q{i}"}}) + "\n")
            fh.write(json.dumps({"type": "assistant.message", "timestamp": _ts("00:01:01"), "data": {
                "content": f"a{i}", "toolRequests": [
                    {"name": "view", "toolCallId": f"c{i}", "arguments": {"path": "/repo/a.py"}},
                ]}}) + "\n")
            fh.write(json.dumps({"type": "tool.execution_complete", "timestamp": _ts("00:01:02"),
                                 "data": {"toolCallId": f"c{i}", "result": {"content": filler}}}) + "\n")
            i += 1
    size = (session_dir / "events.jsonl").stat().st_size
    out = tmp_path / "out"

    tracemalloc.start()
    try:
        stream = collate.TurnStream(session_dir, None, 200)
        files = collate.write_segments({}, stream.session_start, [], stream, [], None, out, 200_000, 200)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert stream.turn_count == i and len(files) > 2
    print(f"write_segments: peak {peak} bytes for {size}-byte transcript")
    assert peak < size // 4
