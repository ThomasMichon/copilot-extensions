"""Tests for the shared, transport-agnostic session-liveness probe."""
from __future__ import annotations

from session_liveness_probe import build_probe_script, parse_probe_output


def test_build_probe_script_contains_proc_and_lock_scan():
    script = build_probe_script()
    assert "/proc/$pid" in script
    assert "inuse.*.lock" in script
    assert "kill -0" not in script


def test_parse_probe_output_active_session():
    session_live = "11111111-1111-4111-8111-111111111111"
    session_stale = "22222222-2222-4222-8222-222222222222"
    stdout = (
        "ROOT\tpresent\n"
        f"LOCK\t{session_live}\t123\tlive\n"
        f"LOCK\t{session_stale}\t456\tstale\n"
        "PROCESS_SCAN\tok\n"
    )
    result = parse_probe_output(0, stdout, "")
    assert result.state == "active"
    assert result.active_sessions == [session_live]
    assert result.stale_sessions == [session_stale]


def test_parse_probe_output_idle_when_no_locks_or_processes():
    result = parse_probe_output(0, "ROOT\tabsent\nPROCESS_SCAN\tok\n", "")
    assert result.state == "idle"
    assert result.active_sessions == []


def test_parse_probe_output_process_backstop_without_lock_is_unknown():
    result = parse_probe_output(
        0, "ROOT\tpresent\nPROCESS\t789\nPROCESS_SCAN\tok\n", ""
    )
    assert result.state == "unknown"
    assert "no matching live session marker" in result.reason


def test_parse_probe_output_partial_scan_without_lock_is_unknown():
    result = parse_probe_output(0, "ROOT\tabsent\nPROCESS_SCAN\tpartial\n", "")
    assert result.state == "unknown"
    assert result.reason == "process backstop was incomplete"


def test_parse_probe_output_nonzero_returncode_is_unknown():
    result = parse_probe_output(1, "", "boom")
    assert result.state == "unknown"
    assert result.reason == "boom"


def test_parse_probe_output_invalid_header_is_unknown():
    result = parse_probe_output(0, "GARBAGE\n", "")
    assert result.state == "unknown"
    assert result.reason == "session-state probe returned an invalid header"


def test_parse_probe_output_non_uuid_session_is_unknown():
    # 36 hex/hyphen chars satisfies the LOCK line's shape but not uuid.UUID.
    bad_session = "1" * 35 + "-"
    result = parse_probe_output(
        0, f"ROOT\tpresent\nLOCK\t{bad_session}\t123\tlive\n", ""
    )
    assert result.state == "unknown"
    assert "non-UUID" in result.reason
