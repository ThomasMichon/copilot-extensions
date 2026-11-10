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
    ({"session_bridge_live": True}, "HOST(?)"),
    ({"session_ahp_live": True}, "HOST(?)"),
    ({"execution_leg_live": True}, "HOST(?)"),
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


def test_unknown_mux_keeps_open_but_cannot_offer_destructive_recovery():
    from worktree_manager.production_picker.picker_tui.engine_worktree_actions import (
        PickerScreenWorktreeActionsMixin,
    )

    row = {
        "mux_observation_unknown": True, "session_bound_live": True,
        "last_session_id": "original", "cleanup_bucket": "conversation",
    }
    actions = PickerScreenWorktreeActionsMixin._session_action_verbs(row)
    assert "Open" in actions
    assert not {"Reclaim", "Restore", "Stop", "Repair", "Cleanup"} & set(actions)
    assert "Finalize" in actions


@pytest.mark.parametrize("mux_fields", [
    {}, {"mux_clients": 0},
    {"mux_session": False, "mux_clients": 0, "mux_attached": False},
])
def test_actions_query_preserves_unavailable_mux_until_complete_current_observation(
    tmp_path, monkeypatch, mux_fields,
):
    from types import SimpleNamespace
    from worktree_manager.production_picker import engine_group_c, project_config
    from worktree_manager.production_picker.picker_tui import worktree_detail_queries

    (tmp_path / "one.yaml").write_text("")
    monkeypatch.setattr(project_config, "tracking_dir", lambda _: tmp_path)
    batch = engine_group_c.PickerReconcileLocalBatch(
        version=1,
        rows=[{"id": "one", "session_bound_live": True, **mux_fields}],
        summary={"mux_scan_ok": False},
    )
    monkeypatch.setattr(engine_group_c, "picker_reconcile_local", lambda *a, **kw: batch)
    raw = {
        "id": "one", "mux_session": True, "mux_attached": True, "mux_clients": 7,
        "mux_observation_unavailable": True,
        "fact_provenance": {
            "mux": {"quality": "unavailable", "observed_at": None},
            "git": {"quality": "current", "observed_at": 12},
        },
        "mux_last_known": {"present": True, "observed_at": "2020-01-01T00:00:00"},
    }
    rec = derive.norm(raw, "host", "Win")
    source = SimpleNamespace(is_local=lambda *a: True)
    row, local = worktree_detail_queries.verify_liveness(rec, source, "project")
    assert local is True and row["mux_observation_unavailable"] is True
    data_local._stamp_from_raw(None, raw, row)
    retained = derive.norm(raw, "host", "Win")
    assert retained["sess"] == "MUX(?)"
    assert retained["attached"] is False and retained["mux_live"] is False
    assert raw["mux_clients"] == 7
    batch.summary["mux_scan_ok"] = True
    batch.rows[:] = [{
        "id": "one", "session_bound_live": True,
        "mux_session": False, "mux_clients": 0, "mux_attached": False,
    }]
    row, _ = worktree_detail_queries.verify_liveness(rec, source, "project")
    data_local._stamp_from_raw(None, raw, row)
    assert raw["mux_clients"] == 0 and derive._sess(raw) == "PROC"
    assert raw["fact_provenance"] == {"git": {"quality": "current", "observed_at": 12}}
    assert "mux_last_known" not in raw


def test_complete_mux_refinement_does_not_clear_stale_source_envelope():
    availability = {"status": "stale", "reason": "source disconnected"}
    raw = {
        "id": "one", "_availability": availability,
        "fact_provenance": {"mux": {"quality": "unavailable"}},
        "mux_last_known": {"present": True, "observed_at": "2020-01-01T00:00:00"},
    }
    data_local._merge_reconcile_row(raw, {
        "mux_session": True, "mux_clients": 2, "mux_attached": True,
    })
    assert raw["_availability"] is availability
    assert "mux" not in raw["fact_provenance"] and "mux_last_known" not in raw
    row = derive.norm(raw, "host", "Win")
    assert row["sess"] == "MUX(?)"
    assert row["attached"] is False and row["mux_live"] is False
