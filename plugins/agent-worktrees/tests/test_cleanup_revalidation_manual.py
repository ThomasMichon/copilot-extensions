from __future__ import annotations

import multiprocessing as mp
import time
from datetime import datetime, timezone

from agent_worktrees import __main__ as cli
from agent_worktrees import git_ops, tracking

from _cleanup_revalidation_helpers import (
    CleanupHarness,
    S,
    hold_record_lock_worker,
    info,
    make_record,
    mutate_record,
    session_ctx,
)


def _assert_batch_skipped(capsys, harness: CleanupHarness, reason: str) -> None:
    assert harness.run_batch() == 0
    captured = capsys.readouterr()
    assert harness.reaped == []
    assert reason in (captured.out + captured.err)


def test_cmd_cleanup_dirty_after_scan_refused(capsys, monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.DIRTY, dirty=1))
    _assert_batch_skipped(capsys, harness, "uncommitted change")
    assert harness.classify_calls == 2


def test_cmd_cleanup_skips_unconcluded_dispatch_attempt_worktree(
    capsys, monkeypatch, tmp_path
):
    """Live incident: a headless dispatch task's spawn-reservation worktree
    was auto-cleaned by routine `cleanup` while a task was still retrying it
    -- indistinguishable from an ordinary "unused, clean, no active session"
    worktree since only `terminal_conclusion.conclude_disposable_worktree`
    (never called yet here) flips `kind` to a MANAGED_KINDS value. A
    dispatch-created, not-yet-concluded worktree must be excluded from this
    routine sweep entirely, exactly like a system/bridge worktree already is.
    """
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(
        tmp_path,
        status="active",
        dispatch_attempt=tracking.DispatchAttempt(
            task_id="task-1",
            reservation_key="dispatch-task:task-1:1",
            attempt=1,
            driver="agent-dispatch",
            supervisor="declared:repo:owner:repo-workers",
            creator_machine="m",
        ),
    )
    harness.seed(rec)
    # An unused/clean classification would ordinarily be a same-scan-safe
    # candidate; the dispatch-attempt exclusion must apply before any
    # classification is even considered.
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    assert harness.run_batch() == 0
    assert harness.reaped == []
    assert harness.classify_calls == 0


def test_cmd_cleanup_active_session_after_scan_refused(capsys, monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    harness.seed(rec)
    norm = harness.norm(rec.worktree_path)
    harness.set_session_contexts([session_ctx(), session_ctx()])
    harness.set_build_active_paths_sequence([set(), {norm}])
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    _assert_batch_skipped(capsys, harness, "became active since the initial scan")
    assert harness.classify_calls == 2


def test_cmd_cleanup_wip_after_scan_refused(capsys, monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active")
    harness.seed(rec)
    harness.set_classifier(info(S.COMPLETED), info(S.WIP))
    _assert_batch_skipped(capsys, harness, "content not on the default branch")
    assert harness.classify_calls == 2


def test_cmd_cleanup_finalized_status_currently_masks_new_wip_gap(
    capsys, monkeypatch, tmp_path
):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.WIP))
    assert harness.run_batch() == 0
    capsys.readouterr()
    assert harness.reaped == [("wt1", git_ops.WorktreeState.COMPLETED.value)]


def test_cmd_cleanup_new_conversation_turn_after_scan_refused(capsys, monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active")
    harness.seed(rec)
    norm = harness.norm(rec.worktree_path)
    harness.set_classifier(info(S.COMPLETED), info(S.UNUSED))
    harness.set_session_contexts(
        [session_ctx(turn_count={norm: 0}), session_ctx(turn_count={norm: 3})]
    )
    _assert_batch_skipped(capsys, harness, "session held 3 turn")
    assert harness.scan_calls == 2


def test_cmd_cleanup_finalized_status_currently_masks_new_conversation_gap(
    capsys, monkeypatch, tmp_path
):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    harness.seed(rec)
    norm = harness.norm(rec.worktree_path)
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    harness.set_session_contexts(
        [session_ctx(turn_count={norm: 0}), session_ctx(turn_count={norm: 4})]
    )
    assert harness.run_batch() == 0
    capsys.readouterr()
    assert harness.reaped == [("wt1", git_ops.WorktreeState.COMPLETED.value)]


def test_cmd_cleanup_held_claim_reopened_after_scan_refused(capsys, monkeypatch, tmp_path):
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
    _assert_batch_skipped(capsys, harness, "held resource claim")


def test_cmd_cleanup_follow_up_reopened_after_scan_refused(capsys, monkeypatch, tmp_path):
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
    _assert_batch_skipped(capsys, harness, "open follow-up")


def test_cmd_cleanup_branch_becomes_unmerged_after_scan_refused(capsys, monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active", exists=False)
    harness.seed(rec)
    harness.set_branch_merged_sequence([True, False])
    _assert_batch_skipped(capsys, harness, "branch has unmerged commits")
    assert harness.merge_calls == 2


def test_cmd_cleanup_claimant_unknown_under_lock_refused(capsys, monkeypatch, tmp_path):
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
    _assert_batch_skipped(capsys, harness, "claimant liveness unconfirmed")
    assert harness.claimant_calls == 1


def test_cmd_cleanup_mux_fallback_negative_cache_race_refused(capsys, monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    rec.mux_live = False
    rec.mux_live_at = datetime.now(timezone.utc).isoformat()
    harness.seed(rec)
    harness.set_session_contexts([session_ctx(), session_ctx()])
    harness.use_real_build_active_paths_with_mux_sequence(has_mux_values=[False, True])
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    _assert_batch_skipped(capsys, harness, "became active since the initial scan")
    assert harness.has_mux_calls == 2


def test_reap_one_dirty_after_scan_refused(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.DIRTY, dirty=1))
    result = harness.run_reap_one()
    assert result["removed"] is False and result["bucket"] == "dirty"
    assert harness.reaped == []


def test_reap_one_active_session_after_scan_refused(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    harness.seed(rec)
    norm = harness.norm(rec.worktree_path)
    harness.set_build_active_paths_sequence([set(), {norm}])
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    result = harness.run_reap_one()
    assert result["removed"] is False and result["bucket"] == "active"
    assert harness.reaped == []


def test_reap_one_wip_after_scan_refused(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active")
    harness.seed(rec)
    harness.set_classifier(info(S.COMPLETED), info(S.WIP))
    result = harness.run_reap_one()
    assert result["removed"] is False and result["bucket"] == "wip"
    assert harness.reaped == []


def test_reap_one_finalized_status_currently_masks_new_wip_gap(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.WIP))
    result = harness.run_reap_one()
    assert result["removed"] is True
    assert harness.reaped == [("wt1", git_ops.WorktreeState.COMPLETED.value)]


def test_reap_one_new_conversation_turn_after_scan_refused(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active")
    harness.seed(rec)
    norm = harness.norm(rec.worktree_path)
    harness.set_classifier(info(S.COMPLETED), info(S.UNUSED))
    harness.set_session_contexts(
        [session_ctx(turn_count={norm: 0}), session_ctx(turn_count={norm: 2})]
    )
    result = harness.run_reap_one()
    assert result["removed"] is False and result["bucket"] == "conversation"
    assert harness.reaped == []


def test_reap_one_finalized_status_currently_masks_new_conversation_gap(
    monkeypatch, tmp_path
):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    harness.seed(rec)
    norm = harness.norm(rec.worktree_path)
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    harness.set_session_contexts(
        [session_ctx(turn_count={norm: 0}), session_ctx(turn_count={norm: 5})]
    )
    result = harness.run_reap_one()
    assert result["removed"] is True
    assert harness.reaped == [("wt1", git_ops.WorktreeState.COMPLETED.value)]


def test_reap_one_held_claim_reopened_after_scan_refused(monkeypatch, tmp_path):
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
    result = harness.run_reap_one()
    assert result["removed"] is False and result["bucket"] == "held-claims"
    assert harness.reaped == []


def test_reap_one_follow_up_reopened_after_scan_refused(monkeypatch, tmp_path):
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
    result = harness.run_reap_one()
    assert result["removed"] is False and result["bucket"] == "follow-up"
    assert harness.reaped == []


def test_reap_one_branch_becomes_unmerged_after_scan_refused(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active", exists=False)
    harness.seed(rec)
    harness.set_branch_merged_sequence([True, False])
    result = harness.run_reap_one()
    assert result["removed"] is False and result["reason"].startswith("branch has unmerged")
    assert harness.reaped == []


def test_reap_one_claimant_unknown_under_lock_refused(monkeypatch, tmp_path):
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
    result = harness.run_reap_one()
    assert result["removed"] is False and result["bucket"] == "claimed"
    assert harness.reaped == []


def test_reap_one_mux_fallback_negative_cache_race_refused(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    rec.mux_live = False
    rec.mux_live_at = datetime.now(timezone.utc).isoformat()
    harness.seed(rec)
    harness.set_session_contexts([session_ctx(), session_ctx()])
    harness.use_real_build_active_paths_with_mux_sequence(has_mux_values=[False, True])
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    result = harness.run_reap_one()
    assert result["removed"] is False and result["bucket"] == "active"
    assert harness.reaped == []
    assert harness.has_mux_calls == 2


def test_reap_one_force_active_session_still_rejected(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active")
    harness.seed(rec)
    norm = harness.norm(rec.worktree_path)
    harness.set_build_active_paths_sequence([{norm}])
    harness.set_classifier(info(S.UNUSED))
    result = harness.run_reap_one(force=True)
    assert result["removed"] is False and result["bucket"] == "active"
    assert harness.reaped == []


def test_reap_one_force_dirty_worktree_is_removed(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active")
    harness.seed(rec)
    harness.set_classifier(info(S.DIRTY, dirty=2), info(S.DIRTY, dirty=2))
    result = harness.run_reap_one(force=True)
    assert result["removed"] is True
    assert harness.reaped == [("wt1", git_ops.WorktreeState.DIRTY.value)]


def test_reap_one_force_wip_worktree_is_removed(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active")
    harness.seed(rec)
    harness.set_classifier(info(S.WIP), info(S.WIP))
    result = harness.run_reap_one(force=True)
    assert result["removed"] is True
    assert harness.reaped == [("wt1", git_ops.WorktreeState.WIP.value)]


def test_reap_one_force_claimed_worktree_is_removed(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active")
    rec.owner_ref = "m/owner/wt-owner"
    harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))

    class _ForbiddenRecordLock:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("force must not take record lock")

    monkeypatch.setattr(cli.tracking, "_RecordLock", _ForbiddenRecordLock)
    monkeypatch.setattr(
        cli.claimant_mod,
        "resolve_claimant_alive",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("force must bypass claimant checks")),
    )
    result = harness.run_reap_one(force=True)
    assert result["removed"] is True
    assert harness.reaped == [("wt1", git_ops.WorktreeState.UNUSED.value)]


def test_reap_one_force_attach_after_scan_still_rejected(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active")
    harness.seed(rec)
    norm = harness.norm(rec.worktree_path)
    harness.set_build_active_paths_sequence([set(), {norm}])
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    result = harness.run_reap_one(force=True)
    assert result["removed"] is False and result["bucket"] == "active"
    assert harness.reaped == []


def test_reap_one_force_mux_fallback_negative_cache_attach_still_rejected(
    monkeypatch, tmp_path
):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="active")
    rec.mux_live = False
    rec.mux_live_at = datetime.now(timezone.utc).isoformat()
    harness.seed(rec)
    harness.use_real_build_active_paths_with_mux_sequence(has_mux_values=[False, True])
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    result = harness.run_reap_one(force=True)
    assert result["removed"] is False and result["bucket"] == "active"
    assert harness.reaped == []
    assert harness.has_mux_calls == 2


def test_cmd_cleanup_revalidation_holds_finalize_lock_and_fences_mutation(
    capsys, monkeypatch, tmp_path
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
    assert harness.run_batch() == 0
    capsys.readouterr()
    assert order[:2] == ["finalize-acquire", "record-enter"]
    assert harness.finalize_events == ["finalize-acquire", "finalize-release"]
    assert harness.reaped == [("wt1", git_ops.WorktreeState.COMPLETED.value)]


def test_reap_one_revalidation_holds_finalize_lock_and_fences_mutation(
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
    result = harness.run_reap_one()
    assert result["removed"] is True
    assert order[:2] == ["finalize-acquire", "record-enter"]
    assert harness.finalize_events == ["finalize-acquire", "finalize-release"]
    assert harness.reaped == [("wt1", git_ops.WorktreeState.COMPLETED.value)]


def test_cmd_cleanup_cross_process_record_lock_contention_fails_closed(
    capsys, monkeypatch, tmp_path
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
        _assert_batch_skipped(capsys, harness, "revalidation lock contended")
    finally:
        release.write_text("1", encoding="utf-8")
        holder.join(timeout=60)
    assert holder.exitcode == 0


def test_reap_one_cross_process_record_lock_contention_fails_closed(
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
        result = harness.run_reap_one()
    finally:
        release.write_text("1", encoding="utf-8")
        holder.join(timeout=60)
    assert holder.exitcode == 0
    assert result["removed"] is False and result["bucket"] == "locked"
    assert harness.reaped == []
