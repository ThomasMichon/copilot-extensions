"""Tests for `_cleanup_per_item_skip_reason` (worktree-finality-and-
obligations Phase 5): a worktree blocked by held claims or open follow-ups
must get its own per-item skip line in `cleanup`'s report, not silently
vanish (neither listed as skipped nor counted in any summary bucket)."""

from __future__ import annotations

from agent_worktrees import __main__ as m
from agent_worktrees import prune


def _disp(bucket, reason="reason text", cleanable=False):
    return prune.CleanupDisposition(cleanable, bucket, reason)


def test_held_claims_gets_its_own_reason():
    assert m._cleanup_per_item_skip_reason(_disp("held-claims", "1 held claim")) == (
        "1 held claim"
    )


def test_follow_up_gets_its_own_reason():
    assert m._cleanup_per_item_skip_reason(_disp("follow-up", "1 open follow-up")) == (
        "1 open follow-up"
    )


def test_active_uses_fixed_message():
    assert m._cleanup_per_item_skip_reason(_disp("active", "whatever")) == (
        "active Copilot session in use"
    )


def test_claimed_open_pr_closed_unmerged_paired_pending_use_disp_reason():
    for bucket in ("claimed", "open-pr", "closed-unmerged", "paired-pending"):
        assert m._cleanup_per_item_skip_reason(_disp(bucket, "x")) == "x"


def test_aggregate_buckets_have_no_per_item_reason():
    for bucket in ("unused", "conversation", "dirty", "wip", "clean"):
        assert m._cleanup_per_item_skip_reason(_disp(bucket, "x")) == ""
