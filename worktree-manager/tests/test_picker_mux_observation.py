"""Mux presentation uses published evidence, never render-time probes."""

import subprocess

import pytest

from worktree_manager.production_picker.picker_tui import data_local, derive


@pytest.mark.parametrize("row,expected", [
    ({"mux_session": True, "mux_clients": 2, "mux_attached": True}, "MUX(2)"),
    ({"mux_session": True, "mux_clients": 0}, "MUX(0)"),
    ({"session_bound_live": True}, "PROC"),
    ({"session_bridge_live": True, "interface": "acp"}, "ACP"),
    ({}, "-"),
])
def test_legacy_and_current_presentation_remains_compatible(row, expected):
    assert derive._sess(row) == expected


@pytest.mark.parametrize("row,expected", [
    ({"mux_session": True, "mux_clients": 7, "mux_attached": True}, "MUX(?)"),
    ({"session_bound_live": True}, "HOST(?)"),
    ({"session_bound_live": True, "session_bare_orphan": True}, "PROC"),
    ({"session_bridge_live": True, "interface": "acp"}, "ACP"),
    ({}, "?"),
])
def test_unknown_mux_evidence_cannot_become_a_count_or_plain_process(
    monkeypatch, row, expected,
):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("render-time probe"))
    row["fact_provenance"] = {"mux": {"quality": "unavailable", "observed_at": None}}
    assert derive._sess(row) == expected


def test_last_known_presence_is_uncertain_not_live():
    assert derive._sess({
        "fact_provenance": {"mux": {
            "quality": "unavailable", "observed_at": None,
        }},
        "mux_last_known": {"present": True, "observed_at": "2026-01-01T00:00:00"},
        "session_bound_live": True,
    }) == "MUX(?)"


@pytest.mark.parametrize("status", ["stale", "advisory"])
def test_stale_envelope_does_not_promote_older_positive_evidence(status):
    row = {
        "mux_session": True, "mux_attached": True, "mux_clients": 7,
        "fact_provenance": {"mux": {"quality": "current", "observed_at": 1}},
        "_availability": {"status": status, "reason": "enumeration unavailable"},
    }
    assert derive._sess(row) == "MUX(?)"
    assert derive._mux_observation_unknown(row)


def test_legacy_reconcile_preserves_but_labels_last_known_mux():
    row = {"id": "one", "mux_session": True, "mux_attached": True, "mux_clients": 7}
    data_local._merge_reconcile_row(row, {"id": "one"}, preserve_mux=True)
    assert row["mux_clients"] == 7
    assert derive._sess(row) == "MUX(?)"
    data_local._merge_reconcile_row(
        row, {"mux_session": True, "mux_attached": False, "mux_clients": 0},
    )
    assert derive._sess(row) == "MUX(0)"


def test_normalized_unknown_mux_cannot_enable_attachment_controls():
    row = derive.norm({
        "id": "one", "machine": "host", "platform": "windows",
        "mux_session": True, "mux_attached": True, "mux_clients": 7,
        "fact_provenance": {"mux": {"quality": "unavailable", "observed_at": None}},
    }, "host", "Win")
    assert row["sess"] == "MUX(?)"
    assert row["mux_live"] is False and row["attached"] is False
