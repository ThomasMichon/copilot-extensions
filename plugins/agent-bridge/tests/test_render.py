"""Tests for the collapse/expand stream renderer."""

from __future__ import annotations

from agent_bridge.render import StreamRenderer


def _r(**kw) -> StreamRenderer:
    kw.setdefault("color", False)
    return StreamRenderer(**kw)


class TestCollapsedFeed:
    """Default (collapsed) rendering."""

    def test_agent_message_streams_in_full(self) -> None:
        r = _r()
        out = r.render_event("agent_message", {"text": "Hello world"})
        assert out == "Hello world"

    def test_consecutive_message_chunks_concatenate(self) -> None:
        r = _r()
        a = r.render_event("agent_message", {"text": "Hello "})
        b = r.render_event("agent_message", {"text": "world"})
        assert a + b == "Hello world"

    def test_thoughts_collapse_to_single_marker(self) -> None:
        r = _r()
        first = r.render_event("agent_thought", {"text": "step 1"})
        second = r.render_event("agent_thought", {"text": "step 2"})
        # One marker for the burst, the rest suppressed.
        assert "thinking" in first
        assert second == ""
        # The raw thought text never leaks into the feed.
        assert "step 1" not in first
        assert "step 2" not in (first + second)

    def test_thinking_marker_repeats_after_interruption(self) -> None:
        r = _r()
        r.render_event("agent_thought", {"text": "a"})
        r.render_event("agent_message", {"text": "X"})  # interrupts the burst
        again = r.render_event("agent_thought", {"text": "b"})
        assert "thinking" in again

    def test_tool_call_collapses_to_one_line(self) -> None:
        r = _r()
        start = r.render_event(
            "tool_call_start", {"tool_call_id": "t1", "title": "Read file"}
        )
        done = r.render_event(
            "tool_call_update", {"tool_call_id": "t1", "status": "completed"}
        )
        line = start + done
        assert "Read file" in line
        assert "done" in line
        assert line.endswith("\n")
        # The whole tool call is a single rendered line.
        assert line.count("\n") == 1

    def test_running_tool_updates_are_suppressed(self) -> None:
        r = _r()
        r.render_event("tool_call_start", {"tool_call_id": "t1", "title": "Build"})
        running = r.render_event(
            "tool_call_update", {"tool_call_id": "t1", "status": "running"}
        )
        assert running == ""

    def test_failed_tool_shows_failed(self) -> None:
        r = _r()
        r.render_event("tool_call_start", {"tool_call_id": "t1", "title": "Build"})
        done = r.render_event(
            "tool_call_update", {"tool_call_id": "t1", "status": "failed"}
        )
        assert "failed" in done

    def test_tool_content_not_shown_when_collapsed(self) -> None:
        r = _r()
        r.render_event("tool_call_start", {"tool_call_id": "t1", "title": "Cmd"})
        out = r.render_event(
            "tool_call_update",
            {"tool_call_id": "t1", "status": "completed",
             "content": ["lots of stdout noise"]},
        )
        assert "lots of stdout noise" not in out

    def test_turn_complete_marker(self) -> None:
        r = _r()
        out = r.render_event("turn_complete", {"stop_reason": "end_turn"})
        assert "Turn complete" in out
        assert "end_turn" in out

    def test_error_marker(self) -> None:
        r = _r()
        out = r.render_event("error", {"message": "boom"})
        assert "boom" in out
        assert "FAIL" in out

    def test_dangling_tool_line_closed_by_other_event(self) -> None:
        r = _r()
        start = r.render_event(
            "tool_call_start", {"tool_call_id": "t1", "title": "Read"}
        )
        # A message arrives before the tool finishes -> close the open line.
        msg = r.render_event("agent_message", {"text": "hi"})
        assert not start.endswith("\n")
        assert msg.startswith("\n")

    def test_silent_events_render_nothing(self) -> None:
        r = _r()
        assert r.render_event("usage_update", {"input_tokens": 5}) == ""
        assert r.render_event(
            "session_state_changed", {"status": "running"}
        ) == ""


class TestExpandedFeed:
    """Expansion modes reveal the collapsed detail."""

    def test_expand_thoughts_shows_full_text(self) -> None:
        r = _r(expand_thoughts=True)
        out = r.render_event("agent_thought", {"text": "deep reasoning"})
        assert "deep reasoning" in out

    def test_expand_tools_shows_content(self) -> None:
        r = _r(expand_tools=True)
        r.render_event("tool_call_start", {"tool_call_id": "t1", "title": "Cmd"})
        out = r.render_event(
            "tool_call_update",
            {"tool_call_id": "t1", "status": "completed",
             "content": ["stdout line one"]},
        )
        assert "stdout line one" in out


class TestRenderEvents:
    """Batch rendering for range reads."""

    def test_render_events_joins(self) -> None:
        r = _r()
        events = [
            {"event": "agent_message", "data": {"text": "A"}},
            {"event": "agent_message", "data": {"text": "B"}},
        ]
        assert r.render_events(events) == "AB"

class TestToolProgressLiveness:
    """Quiet-period liveness markers naming the in-flight tool call."""

    def test_includes_title_command_and_elapsed(self) -> None:
        r = _r()
        out = r.tool_progress_line(
            {
                "title": "Build webapp",
                "command": "rush build -t @scope/webapp",
                "elapsed_s": 1027,
            }
        )
        assert "still running" in out
        assert "Build webapp" in out
        assert "rush build -t @scope/webapp" in out
        assert "17m" in out  # 1027s -> 17m7s
        assert out.endswith("\n")

    def test_only_first_command_line_shown(self) -> None:
        r = _r()
        out = r.tool_progress_line(
            {"title": "X", "command": "line one\nline two\nline three", "elapsed_s": 5}
        )
        assert "line one" in out
        assert "line two" not in out

    def test_long_command_truncated(self) -> None:
        r = _r()
        out = r.tool_progress_line(
            {"title": "X", "command": "z" * 200, "elapsed_s": 1}
        )
        assert "\u2026" in out  # ellipsis
        assert "z" * 200 not in out

    def test_missing_command_is_ok(self) -> None:
        r = _r()
        out = r.tool_progress_line({"title": "Some tool", "elapsed_s": 42})
        assert "Some tool" in out
        assert "42s" in out

    def test_defaults_when_empty(self) -> None:
        r = _r()
        out = r.tool_progress_line({})
        assert "tool" in out
        assert "0s" in out


class TestFormatDuration:
    def test_seconds(self) -> None:
        from agent_bridge.render import _format_duration

        assert _format_duration(0) == "0s"
        assert _format_duration(45) == "45s"

    def test_minutes(self) -> None:
        from agent_bridge.render import _format_duration

        assert _format_duration(60) == "1m"
        assert _format_duration(125) == "2m5s"

    def test_hours(self) -> None:
        from agent_bridge.render import _format_duration

        assert _format_duration(3600) == "1h"
        assert _format_duration(4320) == "1h12m"
