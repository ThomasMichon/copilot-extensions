"""Tests wiring `prune.assemble_closure_descriptor` into `_worktree_to_dict`
(worktree-finality-and-obligations Phase 4: `list --json --classify` publishes
the canonical closure descriptor, additive alongside the legacy
`cleanup_bucket`/`state` fields)."""

from __future__ import annotations

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import git_ops, tracking
from agent_worktrees import prune


@pytest.fixture(autouse=True)
def confirmed_fixture_checkout(monkeypatch):
    monkeypatch.setattr("agent_worktrees.finalized_checkout.matches", lambda record: True)


def _rec(**kw):
    base = dict(
        worktree_id="wt-1", branch="worktree/wt-1", worktree_path="/tmp/wt-1",
        repo="owner/repo", machine="m", platform="wsl",
        started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
        resume_count=0, title=None, status="finalized", completed_at=None,
    )
    base.update(kw)
    return tracking.WorktreeRecord(**base)


def test_closure_present_and_final_when_clean():
    rec = _rec()
    info = git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState.COMPLETED, fetch_requested=True,
    )
    row = cli._worktree_to_dict(rec, state_info=info)
    assert "closure" in row
    assert row["closure"]["closure"] == {"final": True}
    assert row["closure"]["label"] == "FINAL"
    assert row["closure"]["facts"]["upstream_containment"]["confirmed"] is True
    assert row["closure"]["facts"]["open_claims"]["confirmed"] is True


def test_closure_reports_merged_when_held_claim_present():
    rec = _rec()
    rec.resources = [
        tracking.ResourceClaim(kind="codespace", ref="cs-1", state="active")
    ]
    info = git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState.COMPLETED, fetch_requested=True,
    )
    row = cli._worktree_to_dict(rec, state_info=info)
    assert row["cleanup_bucket"] == "held-claims"
    assert row["closure"]["label"] == "MERGED"
    assert row["closure"]["claims"] == {"held": 1}
    assert {"code": "held-claims", "count": 1} in row["closure"]["blockers"]


def test_closure_wires_cross_machine_claims_through_production_call_site():
    # The production list --json --classify path (_worktree_to_dict) must
    # carry the cross-machine claim count into the engine <-> Picker
    # contract, not just assemble_closure_descriptor's own unit coverage --
    # otherwise facts.open_claims.cross_machine_held/XM<N> never reach real
    # output.
    rec = _rec()
    rec.resources = [
        tracking.ResourceClaim(
            kind="worktree",
            ref=tracking.format_claim_ref("other-machine", "proj", "wt-child"),
            state="active"),
    ]
    info = git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState.COMPLETED, fetch_requested=True,
    )
    row = cli._worktree_to_dict(rec, state_info=info)
    assert row["cleanup_bucket"] == "held-claims-cross-machine"
    assert row["closure"]["facts"]["open_claims"]["cross_machine_held"] == 1
    assert "XM1" in row["closure"]["compact"]


def test_closure_absent_without_state_info():
    rec = _rec()
    row = cli._worktree_to_dict(rec)
    assert "closure" not in row


def test_closure_downgrades_to_cached_when_fetch_failed():
    # worktree-finality-and-obligations Phase 5 follow-up: a classification
    # whose REQUESTED fetch failed/timed out must not still report
    # "refreshed" evidence -- that would let a stale/failed --fetch attempt
    # claim FINAL on out-of-date local refs.
    rec = _rec()
    info = git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState.COMPLETED,
        fetch_requested=True, fetch_failed=True,
    )
    row = cli._worktree_to_dict(rec, state_info=info)
    assert row["closure"]["facts"]["upstream_containment"]["confirmed"] is False
    assert row["closure"]["label"] == "MERGED"
    assert row["closure"]["closure"] == {"final": False}


def test_closure_downgrades_to_cached_when_no_fetch_requested():
    # #discussion_r4009567365: this is the REAL current-callers' scenario --
    # `_classify_one_record` and the Picker/streaming `--classify` paths all
    # call `classify_worktree(..., fetch=False)`, so `fetch_requested`
    # defaults False even though the classification itself is fresh. Without
    # checking `fetch_requested`, an ordinary fetch-free `list --json
    # --classify` could still report "refreshed"/FINAL from stale local refs.
    rec = _rec()
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    row = cli._worktree_to_dict(rec, state_info=info)
    assert row["closure"]["facts"]["upstream_containment"]["confirmed"] is False
    assert row["closure"]["label"] == "MERGED"
    assert row["closure"]["closure"] == {"final": False}


def test_finalized_display_is_independent_of_cached_removal_permission():
    rec = _rec()
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    descriptor = cli._worktree_to_dict(rec, state_info=info)["closure"]
    assert descriptor["display"] == {
        "label": "FINAL", "style": "final", "compact": "FINAL", "finalized": True,
        "settled_sessions": 0,
    }
    interpreted = prune.interpret_descriptor_payload(descriptor)
    assert interpreted["supported"]
    assert interpreted["label"] == "FINAL"
    assert interpreted["final"] is False
    assert interpreted["action_disposition"] == "blocked"


def test_new_work_and_responsibility_do_not_inherit_finalized_display():
    rec = _rec()
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    for changed in (
        _rec(status="active"),
        _rec(follow_up=True),
        _rec(resources=[tracking.ResourceClaim(kind="codespace", ref="cs", state="active")]),
    ):
        assert not cli._worktree_to_dict(changed, state_info=info)["closure"]["display"]["finalized"]
    dirty = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.DIRTY, dirty=1)
    assert not cli._worktree_to_dict(rec, state_info=dirty)["closure"]["display"]["finalized"]


def test_finalized_display_rejects_malformed_counts_without_authorizing_removal():
    rec = _rec()
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    descriptor = cli._worktree_to_dict(rec, state_info=info)["closure"]
    descriptor["git"]["dirty"] = False
    assert not prune.interpret_descriptor_payload(descriptor)["supported"]


def test_finalized_display_requires_explicit_settled_session_count():
    rec = _rec()
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    descriptor = cli._worktree_to_dict(rec, state_info=info)["closure"]
    del descriptor["display"]["settled_sessions"]
    assert not prune.interpret_descriptor_payload(descriptor)["supported"]
