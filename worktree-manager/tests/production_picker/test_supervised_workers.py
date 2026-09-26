"""Worktrees rows show the remote workers they supervise (venue-pivots-ux).

A venue pivot opts in with a manifest ``worker`` block; the Picker joins its
cached rows to a worktree by the full driving-worktree id and renders a dim
``→ <venue> LIVE|IDLE <activity>`` line under that worktree's row.
"""

from __future__ import annotations

import types

import pytest
from worktree_manager.production_picker.picker_tui import engine_views, pivot_manifest, pivots

_WID = "host-win-20260925-120000-ab12"


def _manifest(**extra):
    data = {
        "label": "Venues",
        "list": ["venue-cli", "list"],
        "entry": {"id": "name", "worktree": "worktree"},
    }
    data.update(extra)
    return pivots.parse_manifest(data, name="venues", source_path="venues.json")


class _Runtime:
    def __init__(self, rows_by_scope, state="ready"):
        self._rows_by_scope = rows_by_scope
        self._state = state
        self.ensured: list[object] = []
        self.repolled: list[object] = []

    def get(self, scope):
        rows = self._rows_by_scope.get(scope)
        return (self._state, rows, "") if rows is not None else ("idle", [], "")

    def ensure(self, scope):
        self.ensured.append(scope)

    def repoll(self, scope):
        self.repolled.append(scope)


# --- manifest -----------------------------------------------------------------

def test_worker_block_is_parsed_with_label_defaulting_to_the_id_field():
    reg = _manifest(worker={"worktree": "worktree_id", "live": "sess", "activity": "activity"})
    assert reg.worker == pivot_manifest.WorkerSpec(
        worktree_field="worktree_id", label_field="name",
        live_field="sess", activity_field="activity",
    )
    assert _manifest().worker is None


@pytest.mark.parametrize("worker", [
    "worktree_id",
    {"live": "sess"},
    {"worktree": ""},
    {"worktree": "worktree_id", "colour": "red"},
])
def test_malformed_worker_block_is_rejected(worker):
    with pytest.raises(pivots.ManifestError):
        _manifest(worker=worker)


def test_shipped_venue_manifests_declare_a_worker_block():
    import json
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    for plugin in ("agent-codespaces", "agent-containers"):
        path = repo / "plugins" / plugin / "pivots" / f"{plugin}.json"
        reg = pivots.parse_manifest(
            json.loads(path.read_text(encoding="utf-8")), name=plugin, source_path=str(path))
        assert reg.worker is not None and reg.worker.worktree_field == "worktree_id", plugin


# --- the join -------------------------------------------------------------------

def _worker_pivot(name="agent-codespaces", account_scoped=True):
    spec = pivot_manifest.WorkerSpec(
        worktree_field="worktree_id", label_field="display",
        live_field="sess", activity_field="activity",
    )
    return types.SimpleNamespace(
        name=name, worktree_field="worktree", group_field="group",
        account_scoped=account_scoped, worker=spec,
    )


def test_find_supervised_workers_matches_the_full_driving_worktree_id():
    reg = _worker_pivot()
    rows = [
        {"display": "cs-one", "worktree_id": _WID, "sess": "LIVE", "activity": "pr: opened"},
        {"display": "cs-two", "worktree_id": "other-host-20260101-000000-ab12", "sess": "IDLE"},
        {"display": "cs-three", "worktree_id": "", "worktree": "ab12"},
    ]
    found = pivots.find_supervised_workers(
        [{"pivot": reg}], {reg.name: _Runtime({"": rows})}, "host", _WID.upper())
    assert found == [
        {"pivot": "agent-codespaces", "id": "", "label": "cs-one", "live": "LIVE", "activity": "pr: opened"},
    ]


def test_find_supervised_workers_uses_the_machine_scope_for_machine_pivots():
    reg = _worker_pivot("agent-containers", account_scoped=False)
    rows = [{"display": "box-1", "worktree_id": _WID, "sess": "IDLE"}]
    runtimes = {reg.name: _Runtime({"host": rows})}
    assert [w["label"] for w in pivots.find_supervised_workers(
        [{"pivot": reg}], runtimes, "host", _WID)] == ["box-1"]
    assert pivots.find_supervised_workers([{"pivot": reg}], runtimes, "other", _WID) == []


def test_find_supervised_workers_is_empty_until_the_pivot_has_loaded():
    reg = _worker_pivot()
    rows = [{"display": "cs-one", "worktree_id": _WID}]
    runtimes = {reg.name: _Runtime({"": rows}, state="loading")}
    assert pivots.find_supervised_workers([{"pivot": reg}], runtimes, "host", _WID) == []
    assert pivots.find_supervised_workers([{"pivot": reg}], {}, "host", _WID) == []
    assert pivots.find_supervised_workers([{"pivot": reg}], runtimes, "host", "") == []


def test_worker_pivots_never_supply_a_claiming_task_phase_badge():
    reg = _worker_pivot()
    rows = [{"display": "cs-one", "worktree": _WID, "worktree_id": _WID, "group": "repo @ acct"}]
    assert pivots.find_claiming_task(
        [{"pivot": reg}], {reg.name: _Runtime({"": rows})}, "host", _WID, "ab12") is None


# --- rendering ------------------------------------------------------------------

def _view(workers):
    view = engine_views.WorktreesView.__new__(engine_views.WorktreesView)
    view._eng = types.SimpleNamespace(_worktree_supervised_workers=lambda rec: workers)
    return view


def test_worker_line_shows_venue_liveness_and_activity():
    view = _view([{"pivot": "agent-codespaces", "label": "cs-one", "live": "live",
                   "activity": "pr: build=ok tests=ok"}])
    [line] = view._worker_lines({"id": _WID}, 80)
    assert line.plain == "      → cs-one LIVE  pr: build=ok tests=ok"


def test_worker_lines_clip_to_width_and_summarize_the_overflow():
    workers = [
        {"pivot": "p", "label": f"cs-{i}", "live": "IDLE", "activity": "x" * 200}
        for i in range(4)
    ]
    lines = _view(workers)._worker_lines({"id": _WID}, 40)
    assert len(lines) == 3
    assert all(line.cell_len <= 40 for line in lines)
    assert lines[0].plain.startswith("      → cs-0 IDLE  ")
    assert lines[-1].plain == "      → +2 more"


def test_no_worker_lines_without_supervised_workers():
    assert _view([])._worker_lines({"id": _WID}, 80) == []


# --- keeping venue pivots warm --------------------------------------------------

def _refresh_holder():
    import worktree_manager.production_picker.picker_tui.engine as eng_mod

    for obj in vars(eng_mod).values():
        if isinstance(obj, type) and hasattr(obj, "_maybe_refresh_worker_pivots"):
            return obj
    raise AssertionError("no _maybe_refresh_worker_pivots holder found")


def test_worker_pivots_are_loaded_then_repolled_on_a_slow_cadence(monkeypatch):
    import worktree_manager.production_picker.picker_tui.engine as eng_mod
    from worktree_manager.production_picker.picker_tui import engine_runtime

    monkeypatch.setattr(eng_mod, "POLL_SECS", 45.0)
    clock = [1000.0]
    monkeypatch.setattr(engine_runtime.time, "monotonic", lambda: clock[0])
    holder = _refresh_holder()
    inst = holder.__new__(holder)
    worker_reg = _worker_pivot()
    plain_reg = types.SimpleNamespace(name="tasks", worker=None, account_scoped=False)
    rt = _Runtime({})
    plain_rt = _Runtime({})
    inst.pivots = [{"pivot": worker_reg}, {"pivot": plain_reg}, {"kind": "builtin"}]
    inst.progress = None
    inst._pivot_machine_id = lambda: "host"
    inst._pivot_runtime = lambda reg: rt if reg is worker_reg else plain_rt

    inst._maybe_refresh_worker_pivots()
    assert rt.ensured == [""] and rt.repolled == [""]
    clock[0] += 30
    inst._maybe_refresh_worker_pivots()
    assert rt.repolled == [""]            # not yet due (2 * POLL_SECS)
    clock[0] += 61
    inst._maybe_refresh_worker_pivots()
    assert rt.repolled == ["", ""]
    assert plain_rt.ensured == [] and plain_rt.repolled == []
