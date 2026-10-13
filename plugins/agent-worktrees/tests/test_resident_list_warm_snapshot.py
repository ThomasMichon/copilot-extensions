"""Fresh, selection-scoped enrichment for one resident list warm pass."""

from __future__ import annotations

import json
import os
import subprocess
import types
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import config as cfg
from agent_worktrees import (
    classify_daemon, delegate_cli, git_ops, list_cache, list_cli, reclaim, sessions,
    tracking,
)

pytestmark = pytest.mark.guard


def _args(**overrides):
    values = dict(
        classify=True,
        mux_details=True,
        all=True,
        include_other_platforms=False,
        profile_assignment_history=False,
        tracking_status="all",
    )
    values.update(overrides)
    return types.SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def no_unit_processes(request, monkeypatch):
    tier = request.node.get_closest_marker("portfolio_tier")
    if tier is not None and tier.args == ("T3",):
        yield
        return
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        pytest.fail("unit list warming must not spawn a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    yield
    assert attempts == []


@pytest.fixture
def warm_model(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "project_name", lambda: "example")
    monkeypatch.setattr(cfg, "_load_config_uncached", lambda: types.SimpleNamespace())
    monkeypatch.setattr(list_cache.time, "time", lambda: 1001.0)
    calls = Counter()
    production_classify = core._classify_records
    production_render = core._worktree_to_dict
    record = types.SimpleNamespace(worktree_id="wt-example", worktree_path="/w/example")
    state = {"value": git_ops.WorktreeState.WIP}

    def records(args):
        calls["records"] += 1
        return [record]

    def scan(rows):
        calls["sessions"] += 1
        return sessions.SessionContext()

    def classify(rows, context, *, daemon_filters):
        calls["classify"] += 1
        return {
            rec.worktree_id: git_ops.WorktreeStateInfo(state["value"])
            for rec in rows
        }

    def mux(ids):
        calls["mux"] += 1
        return {wid: sessions.MuxInfo(exists=True, clients=1) for wid in ids}

    def orphan():
        calls["orphans"] += 1
        return set()

    def bridge():
        calls["bridge"] += 1
        return set()

    def render(rec, *, state_info=None, mux_info=None,
               include_profile_assignment_history=False, **kwargs):
        row = {"id": rec.worktree_id, "title": "example"}
        if state_info is not None:
            row["state"] = state_info.state.value
        if mux_info is not None:
            row["mux"] = mux_info.exists
        if include_profile_assignment_history:
            row["history"] = ["assignment"]
        return row

    monkeypatch.setattr(core, "_list_records_for_args", records)
    monkeypatch.setattr(core, "_classify_records", classify)
    monkeypatch.setattr(core, "_worktree_to_dict", render)
    monkeypatch.setattr(sessions, "scan_sessions_fast", scan)
    monkeypatch.setattr(sessions, "mux_status_many", mux)
    monkeypatch.setattr(reclaim, "bare_orphan_worktree_ids", orphan)
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", bridge)
    monkeypatch.setattr(delegate_cli, "annotate_delegate_graph", lambda rows: None)
    shapes = [
        _args(),
        _args(profile_assignment_history=True),
        _args(classify=False, mux_details=False),
        _args(mux_details=False),
    ]

    def demand(args, *, project="example"):
        key = list_cache.cache_key(
            args, project=project, tracking_status=args.tracking_status
        )
        list_cache.note_demand(
            key, args, project=project,
            tracking_status=args.tracking_status, now=1000.0,
        )
        return key

    return types.SimpleNamespace(
        calls=calls, record=record, state=state, shapes=shapes, demand=demand,
        production_classify=production_classify,
        production_render=production_render,
    )


@pytest.mark.portfolio_tier("T0")
def test_resident_warm_shares_enrichment_without_sharing_output_shapes(warm_model):
    keys = [warm_model.demand(args) for args in warm_model.shapes]
    assert list_cli._warm_list_cache_for_active_project() == 4
    assert warm_model.calls == {
        "records": 1, "sessions": 1, "classify": 1,
        "mux": 1, "orphans": 1, "bridge": 1,
    }
    for args, key in zip(warm_model.shapes, keys, strict=True):
        row = list_cache.read_fresh(key)["worktrees"][0]
        assert ("state" in row) == args.classify
        assert ("mux" in row) == args.mux_details
        assert ("history" in row) == args.profile_assignment_history


@pytest.mark.portfolio_tier("T0")
def test_resident_warm_observes_changed_git_state_on_next_pass(warm_model):
    keys = [warm_model.demand(args) for args in warm_model.shapes]
    list_cli._warm_list_cache_for_active_project()
    warm_model.state["value"] = git_ops.WorktreeState.DIRTY
    list_cli._warm_list_cache_for_active_project()
    assert warm_model.calls["classify"] == 2
    assert warm_model.calls["records"] == 2
    assert warm_model.calls["sessions"] == 2
    for args, key in zip(warm_model.shapes, keys, strict=True):
        row = list_cache.read_fresh(key)["worktrees"][0]
        if args.classify:
            assert row["state"] == git_ops.WorktreeState.DIRTY.value


@pytest.mark.parametrize("axis,value", [
    ("tracking_status", "active"),
    ("include_other_platforms", True),
    ("all", False),
])
@pytest.mark.portfolio_tier("T0")
def test_resident_warm_keeps_selection_axes_separate(warm_model, axis, value):
    warm_model.demand(_args())
    warm_model.demand(_args(**{axis: value}))
    assert list_cli._warm_list_cache_for_active_project() == 2
    assert warm_model.calls == {
        "records": 2, "sessions": 2, "classify": 2,
        "mux": 2, "orphans": 2, "bridge": 2,
    }


@pytest.mark.portfolio_tier("T0")
def test_standalone_payload_reads_do_not_reuse_resident_enrichment(warm_model):
    for _ in range(2):
        list_cli._build_list_json_payload(
            _args(), [warm_model.record], stamp_session_state=False
        )
    assert warm_model.calls["classify"] == 2
    assert warm_model.calls["sessions"] == 2
    assert warm_model.calls["mux"] == 2


@pytest.mark.portfolio_tier("T0")
def test_warmed_payloads_match_the_actual_standalone_row_serializer(
    warm_model, monkeypatch
):
    records = [tracking.WorktreeRecord(
        worktree_id="wt-example", branch="worktree/example",
        worktree_path="/w/example", repo="example", machine="host",
        platform="windows", started_at="", last_resumed_at="", resume_count=0,
        title="example", status="active", completed_at=None,
    )]
    monkeypatch.setattr(core, "_list_records_for_args", lambda args: records)
    monkeypatch.setattr(core, "_worktree_to_dict", warm_model.production_render)
    monkeypatch.setattr(
        cfg, "_load_config_uncached", lambda: types.SimpleNamespace(machine="host")
    )
    expected = [
        list_cli._build_list_json_payload(args, records, stamp_session_state=False)
        for args in warm_model.shapes
    ]
    keys = [warm_model.demand(args) for args in warm_model.shapes]
    assert list_cli._warm_list_cache_for_active_project() == 4
    assert [list_cache.read_fresh(key) for key in keys] == expected


@pytest.mark.portfolio_tier("T0")
def test_resident_warm_does_not_leak_between_projects(warm_model, monkeypatch):
    for project in ("example", "other"):
        monkeypatch.setattr(cfg, "project_name", lambda: project)
        warm_model.demand(_args(), project=project)
        assert list_cli._warm_list_cache_for_active_project() == 1
    assert warm_model.calls["classify"] == 2
    assert warm_model.calls["sessions"] == 2


@pytest.mark.portfolio_tier("T0")
def test_unclassified_demands_do_not_trigger_unused_enrichment(warm_model):
    warm_model.demand(_args(classify=False, mux_details=False))
    assert list_cli._warm_list_cache_for_active_project() == 1
    assert warm_model.calls == {"records": 1, "sessions": 1}


@pytest.mark.portfolio_tier("T0")
def test_snapshot_refuses_different_records_or_filters(warm_model):
    records = [warm_model.record]
    enrichment = list_cli._ListEnrichment(records, list_cli._list_selection_key(_args()))
    with pytest.raises(ValueError, match="does not match"):
        list_cli._build_list_json_payload(
            _args(), list(records), stamp_session_state=False, enrichment=enrichment
        )
    with pytest.raises(ValueError, match="does not match"):
        list_cli._build_list_json_payload(
            _args(tracking_status="active"), records,
            stamp_session_state=False, enrichment=enrichment,
        )
    assert warm_model.calls == {}


@pytest.mark.portfolio_tier("T0")
def test_shared_enrichment_cannot_stamp_session_state(warm_model):
    records = [warm_model.record]
    enrichment = list_cli._ListEnrichment(records, list_cli._list_selection_key(_args()))
    with pytest.raises(ValueError, match="cannot stamp session state"):
        list_cli._build_list_json_payload(_args(), records, enrichment=enrichment)
    assert warm_model.calls == {}


@pytest.mark.portfolio_tier("T0")
def test_failed_classification_does_not_publish_a_success_shaped_cache(
    warm_model, monkeypatch
):
    key = warm_model.demand(_args())

    def fail(*args, **kwargs):
        raise RuntimeError("classification unavailable")

    monkeypatch.setattr(core, "_classify_records", fail)
    with pytest.raises(RuntimeError, match="classification unavailable"):
        list_cli._warm_list_cache_for_active_project()
    assert list_cache.read_fresh(key) is None


@pytest.mark.portfolio_tier("T0")
def test_unavailable_reclaim_observation_stays_unknown_for_the_pass(
    warm_model, monkeypatch
):
    attempts = []
    observed = []

    def unavailable():
        attempts.append("probe")
        raise OSError("unavailable")

    def render(rec, **kwargs):
        observed.append((kwargs["bare_orphan_wts"], kwargs["bridge_live_wts"]))
        return {"id": rec.worktree_id, "title": "example"}

    monkeypatch.setattr(reclaim, "bare_orphan_worktree_ids", unavailable)
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", unavailable)
    monkeypatch.setattr(core, "_worktree_to_dict", render)
    warm_model.demand(_args())
    warm_model.demand(_args(profile_assignment_history=True))
    assert list_cli._warm_list_cache_for_active_project() == 2
    assert observed == [(None, None), (None, None)]
    assert attempts == ["probe", "probe"]
    list_cli._warm_list_cache_for_active_project()
    assert attempts == ["probe"] * 4


@pytest.mark.portfolio_tier("T0")
def test_overlapping_warm_calls_keep_their_snapshots_local(warm_model, monkeypatch):
    barrier = Barrier(2)
    classify = core._classify_records

    def overlapping(*args, **kwargs):
        barrier.wait(timeout=5)
        return classify(*args, **kwargs)

    monkeypatch.setattr(core, "_classify_records", overlapping)
    for args in warm_model.shapes:
        warm_model.demand(args)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(
            lambda _: list_cli._warm_list_cache_for_active_project(), range(2)
        ))
    assert results == [4, 4]
    assert warm_model.calls["classify"] == 2
    assert warm_model.calls["records"] == 2
    assert warm_model.calls["sessions"] == 2


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("network")
@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_LIST_WARM_NATIVE") != "1",
    reason="opt-in real Git resident warm-pass budget and freshness integration",
)
def test_native_resident_warm_two_cycle_git_budget(warm_model, monkeypatch, tmp_path):
    from test_git_tree_diff import _native_fixture

    environment = git_ops.repository_identity_env()
    for name in set(os.environ) - set(environment):
        monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "checkout"
    _native_fixture(root, 1)
    warm_model.record.worktree_path = str(root)

    def compute(kind, payload):
        assert kind == classify_daemon.KIND
        assert payload["project"] == "example"
        return core._serialize_classify_map({
            warm_model.record.worktree_id: git_ops.classify_worktree(
                str(root), "worktree/example", default_branch="main",
            )
        })

    server = classify_daemon.start_server(compute)
    server.start()
    lock_path = tmp_path / "fixture-monitor.json"
    lock_path.write_text(
        json.dumps(classify_daemon.rendezvous_fields(server)), encoding="utf-8"
    )
    monkeypatch.setattr(core, "_monitor_lock_path", lambda: lock_path)
    monkeypatch.setattr(core, "_classify_records", warm_model.production_classify)
    monkeypatch.setattr(
        core, "_classify_records_lease_guarded",
        lambda *args, **kwargs: pytest.fail("native consumer bypassed classify IPC"),
    )
    real_run = git_ops.subprocess.run
    starts = []

    def counted_run(cmd, **kwargs):
        if cmd[0] == "git":
            starts.append(tuple(cmd[1:]))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(git_ops.subprocess, "run", counted_run)
    keys = [warm_model.demand(args) for args in warm_model.shapes]
    try:
        for cycle in range(2):
            starts.clear()
            assert list_cli._warm_list_cache_for_active_project() == 4
            assert len(starts) == 8
            for args, key in zip(warm_model.shapes, keys, strict=True):
                row = list_cache.read_fresh(key)["worktrees"][0]
                if args.classify:
                    assert row["state"] == git_ops.WorktreeState.WIP.value
            print(f"warm_cycle={cycle + 1} shapes=4 direct_git_starts={len(starts)}")
        (root / "dirty.txt").write_text("changed since last pass\n", encoding="utf-8")
        list_cli._warm_list_cache_for_active_project()
        for args, key in zip(warm_model.shapes, keys, strict=True):
            if args.classify:
                assert list_cache.read_fresh(key)["worktrees"][0]["state"] == \
                    git_ops.WorktreeState.DIRTY.value
    finally:
        server.close()
