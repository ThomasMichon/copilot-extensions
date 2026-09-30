"""Synthetic unit tests for ``live_turn_probe.py``'s transcript analyzer
(``_turn_balance_and_boundary_crossing``, agent-bridge-unified-zdd-cutover
Phase 6).

A single successful live run exercises only the happy path -- it does not
prove the analyzer correctly REJECTS a damaged transcript. These synthetic
``events.jsonl``-shaped cases cover the failure paths directly, with no
real agent-bridge install or live Copilot turn required.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scenarios" / "agent-bridge-cutover" / "fixtures" / "live_turn_probe.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("live_turn_probe", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def probe():
    return _load_module()


def _ev(eid, etype, turn_id, ts, extra_data=None):
    data = {"turnId": turn_id}
    if extra_data:
        data.update(extra_data)
    return json.dumps({"id": eid, "type": etype, "data": data, "timestamp": ts})


def test_clean_single_turn_crossing_the_boundary(probe):
    lines = [
        _ev("1", "assistant.turn_start", "0", "2026-01-01T00:00:00Z"),
        _ev("2", "assistant.turn_end", "0", "2026-01-01T00:00:10Z"),
    ]
    boundary_ts = probe.datetime.fromisoformat("2026-01-01T00:00:05+00:00").timestamp()
    result = probe._turn_balance_and_boundary_crossing(lines, boundary_ts, "0")
    assert result["balanced"] is True
    assert result["crossed"] is True
    assert result["orphan_ends"] == 0
    assert result["duplicate_ids"] == 0
    assert result["repeated_starts"] == 0


def test_duplicate_event_id_is_flagged(probe):
    end_line = _ev("2", "assistant.turn_end", "0", "2026-01-01T00:00:10Z")
    lines = [
        _ev("1", "assistant.turn_start", "0", "2026-01-01T00:00:00Z"),
        end_line,
        end_line,  # the exact same event, replayed verbatim
    ]
    result = probe._turn_balance_and_boundary_crossing(lines, 0.0, "0")
    assert result["balanced"] is False
    assert result["duplicate_ids"] == 1


def test_repeated_start_of_the_same_turn_id_is_flagged(probe):
    """start(A), start(A), end(A) with DISTINCT event ids must not balance
    by depth-count alone (a real gap review caught in an earlier revision)."""
    lines = [
        _ev("1", "assistant.turn_start", "0", "2026-01-01T00:00:00Z"),
        _ev("2", "assistant.turn_start", "0", "2026-01-01T00:00:01Z"),
        _ev("3", "assistant.turn_end", "0", "2026-01-01T00:00:10Z"),
    ]
    result = probe._turn_balance_and_boundary_crossing(lines, 0.0, "0")
    assert result["balanced"] is False
    assert result["repeated_starts"] == 1


def test_replaying_a_start_end_pair_after_an_earlier_completed_pair_is_flagged(probe):
    lines = [
        _ev("1", "assistant.turn_start", "0", "2026-01-01T00:00:00Z"),
        _ev("2", "assistant.turn_end", "0", "2026-01-01T00:00:05Z"),
        _ev("3", "assistant.turn_start", "0", "2026-01-01T00:00:06Z"),  # turn_id "0" again
        _ev("4", "assistant.turn_end", "0", "2026-01-01T00:00:10Z"),
    ]
    result = probe._turn_balance_and_boundary_crossing(lines, 0.0, "0")
    assert result["balanced"] is False
    assert result["repeated_starts"] == 1


def test_orphan_end_with_no_matching_start_is_flagged(probe):
    lines = [_ev("1", "assistant.turn_end", "0", "2026-01-01T00:00:10Z")]
    result = probe._turn_balance_and_boundary_crossing(lines, 0.0, "0")
    assert result["balanced"] is False
    assert result["orphan_ends"] == 1


def test_still_open_turn_is_flagged_and_never_crosses(probe):
    lines = [_ev("1", "assistant.turn_start", "0", "2026-01-01T00:00:00Z")]
    result = probe._turn_balance_and_boundary_crossing(lines, 0.0, "0")
    assert result["balanced"] is False
    assert result["still_open"] == 1
    assert result["crossed"] is False


def test_an_unrelated_turns_close_after_the_boundary_does_not_count_as_crossing(probe):
    """Only the SPECIFIC boundary turn's own close may satisfy `crossed` --
    a different turn closing later must not produce a false PASS."""
    lines = [
        _ev("1", "assistant.turn_start", "0", "2026-01-01T00:00:00Z"),
        _ev("2", "assistant.turn_end", "0", "2026-01-01T00:00:01Z"),  # boundary turn closes early
        _ev("3", "assistant.turn_start", "1", "2026-01-01T00:00:02Z"),
        _ev("4", "assistant.turn_end", "1", "2026-01-01T00:00:20Z"),  # a DIFFERENT turn, later
    ]
    boundary_ts = probe.datetime.fromisoformat("2026-01-01T00:00:10+00:00").timestamp()
    result = probe._turn_balance_and_boundary_crossing(lines, boundary_ts, "0")
    assert result["balanced"] is True  # both turns are individually well-formed
    assert result["crossed"] is False  # but turn "0" (the boundary turn) closed BEFORE boundary_ts


def test_boundary_turn_closing_before_reattach_does_not_cross(probe):
    lines = [
        _ev("1", "assistant.turn_start", "0", "2026-01-01T00:00:00Z"),
        _ev("2", "assistant.turn_end", "0", "2026-01-01T00:00:01Z"),
    ]
    boundary_ts = probe.datetime.fromisoformat("2026-01-01T00:00:10+00:00").timestamp()
    result = probe._turn_balance_and_boundary_crossing(lines, boundary_ts, "0")
    assert result["balanced"] is True
    assert result["crossed"] is False
