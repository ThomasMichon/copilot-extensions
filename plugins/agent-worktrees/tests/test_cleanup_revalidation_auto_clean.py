from __future__ import annotations

import multiprocessing as mp
import time
from datetime import datetime, timezone

from agent_worktrees import git_ops, tracking

from _cleanup_revalidation_helpers import (
    CleanupHarness,
    S,
    hold_record_lock_worker,
    info,
    iso_at,
    make_record,
    mutate_record,
    session_ctx,
)


def _skipped_reason(report: dict, wt_id: str) -> str | None:
    for item in report["skipped"]:
        if item["id"] == wt_id:
            return item["reason"]
    return None


def test_sweep_finished_session_dirty_after_selection_refused(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.DIRTY, dirty=1))
    report = harness.run_sweep()
    assert report["removed"] == []
    assert "uncommitted change" in _skipped_reason(report, "wt1")


def test_sweep_finished_session_active_after_selection_refused(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    harness.seed(rec)
    norm = harness.norm(rec.worktree_path)
    harness.set_build_active_paths_sequence([set(), {norm}])
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    report = harness.run_sweep()
    assert report["removed"] == []
    assert _skipped_reason(report, "wt1") == "worktree became active since the initial scan"


def test_sweep_finished_session_wip_after_selection_refused(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active")
    harness.seed(rec)
    harness.set_classifier(info(S.COMPLETED), info(S.WIP))
    report = harness.run_sweep()
    assert report["removed"] == []
    assert "content not on the default branch" in _skipped_reason(report, "wt1")


def test_sweep_finalized_status_currently_masks_new_wip_gap(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.WIP))
    report = harness.run_sweep()
    assert [item["id"] for item in report["removed"]] == ["wt1"]
    assert "content is on the default branch" in report["removed"][0]["reason"]


def test_sweep_finished_session_new_conversation_turn_after_selection_refused(
    monkeypatch, tmp_path
):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active")
    harness.seed(rec)
    norm = harness.norm(rec.worktree_path)
    harness.set_classifier(info(S.COMPLETED), info(S.UNUSED))
    harness.set_session_contexts(
        [session_ctx(turn_count={norm: 0}), session_ctx(turn_count={norm: 2})]
    )
    report = harness.run_sweep()
    assert report["removed"] == []
    assert "session held 2 turn" in _skipped_reason(report, "wt1")


def test_sweep_finalized_status_currently_masks_new_conversation_gap(
    monkeypatch, tmp_path
):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    harness.seed(rec)
    norm = harness.norm(rec.worktree_path)
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    harness.set_session_contexts(
        [session_ctx(turn_count={norm: 0}), session_ctx(turn_count={norm: 3})]
    )
    report = harness.run_sweep()
    assert [item["id"] for item in report["removed"]] == ["wt1"]


def test_sweep_finished_session_held_claim_after_selection_refused(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    path = harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    harness.set_finalize_lock(
        lambda: mutate_record(
            path,
            lambda latest: latest.resources.append(
                tracking.ResourceClaim(kind="codespace", ref="cs-1", state="active")
            ),
        )
    )
    report = harness.run_sweep()
    assert report["removed"] == []
    assert "held resource claim" in _skipped_reason(report, "wt1")


def test_sweep_finished_session_follow_up_after_selection_refused(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    path = harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    harness.set_finalize_lock(
        lambda: mutate_record(
            path,
            lambda latest: latest.follow_ups.append(
                tracking.FollowUpRecord(id="fu-1", summary="deploy it", state="open")
            ),
        )
    )
    report = harness.run_sweep()
    assert report["removed"] == []
    assert "open follow-up" in _skipped_reason(report, "wt1")


def test_sweep_finished_session_branch_becomes_unmerged_after_selection_refused(
    monkeypatch, tmp_path
):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active", exists=False)
    harness.seed(rec)
    harness.set_branch_merged_sequence([True, False])
    report = harness.run_sweep()
    assert report["removed"] == []
    assert _skipped_reason(report, "wt1") == "branch has unmerged commits (worktree dir missing)"


def test_sweep_finished_session_claimant_unknown_under_lock_refused(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    path = harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    harness.set_claimant_alive_sequence([None])
    harness.set_finalize_lock(
        lambda: mutate_record(
            path,
            lambda latest: (
                setattr(latest, "status", "active"),
                setattr(latest, "owner_ref", "m/owner/wt-owner"),
            ),
        )
    )
    report = harness.run_sweep()
    assert report["removed"] == []
    assert "claimant liveness unconfirmed" in _skipped_reason(report, "wt1")


def test_sweep_finished_session_idle_grace_refresh_postpones_removal(
    monkeypatch, tmp_path
):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(
        tmp_path,
        status="finalized",
        started_at=iso_at(0),
        last_resumed_at=iso_at(0),
    )
    path = harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    harness.set_finalize_lock(
        lambda: mutate_record(path, lambda latest: setattr(latest, "last_resumed_at", iso_at(180)))
    )
    monkeypatch.setattr(
        time,
        "time",
        lambda: 200.0,
    )
    report = harness.run_sweep(min_idle_secs=60, now=200.0)
    assert report["removed"] == []
    assert _skipped_reason(report, "wt1") == "idle grace not elapsed (activity since selection)"


def test_sweep_finished_session_mux_fallback_negative_cache_race_refused(
    monkeypatch, tmp_path
):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    rec.mux_live = False
    rec.mux_live_at = datetime.now(timezone.utc).isoformat()
    harness.seed(rec)
    harness.set_session_contexts([session_ctx(), session_ctx()])
    harness.use_real_build_active_paths_with_mux_sequence(has_mux_values=[False, True])
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    report = harness.run_sweep()
    assert report["removed"] == []
    assert _skipped_reason(report, "wt1") == "worktree became active since the initial scan"
    assert harness.has_mux_calls == 2


def test_sweep_revalidation_holds_finalize_lock_and_fences_mutation(
    monkeypatch, tmp_path
):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    path = harness.seed(rec)
    order: list[str] = []
    harness.set_finalize_lock(order=order)
    harness.install_record_lock_wrapper(order)
    harness.set_classifier(
        info(S.UNUSED),
        info(S.UNUSED),
        assert_revalidation_under_finalize=True,
    )
    harness.set_session_contexts(
        [session_ctx(), session_ctx()],
        assert_revalidation_under_finalize=True,
    )

    def _reap(latest, wt_info):
        assert harness.finalize_held is True
        harness.assert_record_lock_blocks_peer_thread(path)
        return 0, []

    harness.set_reap_callback(_reap)
    report = harness.run_sweep()
    assert [item["id"] for item in report["removed"]] == ["wt1"]
    assert order[:2] == ["finalize-acquire", "record-enter"]
    assert harness.finalize_events == ["finalize-acquire", "finalize-release"]
    assert harness.reaped == [("wt1", git_ops.WorktreeState.COMPLETED.value)]


def test_sweep_cross_process_record_lock_contention_fails_closed(
    monkeypatch, tmp_path
):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    yaml_path = harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    ctx = mp.get_context("spawn")
    holder = ctx.Process(
        target=hold_record_lock_worker,
        args=(str(yaml_path), str(ready), str(release)),
    )
    holder.start()
    try:
        deadline = time.monotonic() + 60
        while not ready.exists():
            assert holder.is_alive()
            assert time.monotonic() < deadline
            time.sleep(0.02)
        report = harness.run_sweep()
    finally:
        release.write_text("1", encoding="utf-8")
        holder.join(timeout=60)
    assert holder.exitcode == 0
    assert report["removed"] == []
    assert _skipped_reason(report, "wt1") == "revalidation lock contended"
