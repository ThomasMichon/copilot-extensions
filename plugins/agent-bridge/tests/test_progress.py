"""Structured progress-marker capture (#46.3)."""

from __future__ import annotations

from agent_bridge.session_manager import (
    Session,
    SessionManager,
    _parse_progress_markers,
)
from agent_bridge.transport import SpawnTarget


class TestParseProgressMarkers:
    def test_single_marker(self):
        assert _parse_progress_markers("PROGRESS: build=ok") == {"build": "ok"}

    def test_multiple_markers_one_line(self):
        out = _parse_progress_markers("PROGRESS: commit=abc123 pr=42")
        assert out == {"commit": "abc123", "pr": "42"}

    def test_marker_embedded_in_text(self):
        text = "Done building.\nPROGRESS: build=ok\nMoving on."
        assert _parse_progress_markers(text) == {"build": "ok"}

    def test_colon_optional(self):
        # The dispatch skill documents the no-colon form too.
        assert _parse_progress_markers("PROGRESS build=ok n=42") == {
            "build": "ok", "n": "42",
        }

    def test_no_marker(self):
        assert _parse_progress_markers("just a normal message") == {}

    def test_empty(self):
        assert _parse_progress_markers("") == {}


class TestCaptureProgress:
    def _session(self):
        return Session("s1", "calm-lake", SpawnTarget(type="local", cwd="/wt"))

    def test_agent_message_updates_progress(self):
        s = self._session()
        SessionManager._capture_progress(
            s, "agent_message", {"text": "PROGRESS: build=ok"}
        )
        SessionManager._capture_progress(
            s, "agent_message", {"text": "PROGRESS: pr=42 build=fail"}
        )
        # Latest value per key wins; keys accumulate.
        assert s.progress == {"build": "fail", "pr": "42"}

    def test_non_agent_message_ignored(self):
        s = self._session()
        SessionManager._capture_progress(
            s, "tool_call_start", {"text": "PROGRESS: build=ok"}
        )
        assert s.progress == {}
