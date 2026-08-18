"""Tests for prune-safety triage (agent_worktrees.prune)."""

from __future__ import annotations

from dataclasses import dataclass

from agent_worktrees import git_ops, prune, tracking

S = git_ops.WorktreeState


def _rec(status="finalized", prs=None) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id="wt-1",
        branch="worktree/wt-1",
        worktree_path="/tmp/wt-1",
        repo="owner/repo",
        machine="m",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status=status,
        completed_at=None,
        sessions=[],
        prs=prs or [],
    )


def _pr(number, state, branch="feature/x") -> tracking.PRRecord:
    return tracking.PRRecord(state=state, branch=branch, number=number,
                             provider="gitea", repo="owner/repo")


def _info(state, *, ahead=0, dirty=0) -> git_ops.WorktreeStateInfo:
    return git_ops.WorktreeStateInfo(state=state, ahead=ahead, dirty=dirty)


@dataclass
class _FakePull:
    state: str = "open"
    merged: bool = False
    number: int | None = None


# --- assess: PR-aware path --------------------------------------------------

class TestAssessPRMode:
    def test_merged_pr_is_safe(self):
        rec = _rec(prs=[_pr(10, "merged")])
        v = prune.assess(rec, _info(S.COMPLETED))
        assert v.safe is True
        assert v.category == "merged"
        assert "#10" in v.reason

    def test_open_pr_is_unsafe(self):
        rec = _rec(prs=[_pr(11, "open")])
        v = prune.assess(rec, _info(S.UNUSED))
        assert v.safe is False
        assert v.category == "open-pr"
        assert "#11" in v.reason

    def test_one_merged_one_open_is_unsafe_open(self):
        # A second PR still in flight keeps the worktree alive.
        rec = _rec(prs=[_pr(10, "merged"), _pr(12, "open")])
        v = prune.assess(rec, _info(S.UNUSED))
        assert v.safe is False
        assert v.category == "open-pr"

    def test_duplicate_pr_closed_but_content_on_master_is_safe(self):
        # The #1151/#1150 shape: the recorded PR is closed-unmerged, but git
        # confirms the content landed (via a sibling/duplicate merged PR).
        rec = _rec(prs=[_pr(1151, "closed")])
        v = prune.assess(rec, _info(S.COMPLETED))
        assert v.safe is True
        assert v.category == "completed-local"

    def test_closed_unmerged_without_git_proof_needs_review(self):
        rec = _rec(prs=[_pr(99, "closed")])
        v = prune.assess(rec, _info(S.UNUSED))
        assert v.safe is False
        assert v.category == "closed-unmerged"


# --- assess: no-PR path (git + session activity) ----------------------------

class TestAssessNoPR:
    def test_completed_local_is_safe(self):
        v = prune.assess(_rec(), _info(S.COMPLETED))
        assert v.safe is True
        assert v.category == "completed-local"

    def test_unused_zero_turns_is_empty_safe(self):
        v = prune.assess(_rec(status="unused"), _info(S.UNUSED), turn_count=0)
        assert v.safe is True
        assert v.category == "empty"

    def test_unused_with_turns_is_conversation_only_unsafe(self):
        v = prune.assess(_rec(status="unused"), _info(S.UNUSED), turn_count=7)
        assert v.safe is False
        assert v.category == "conversation-only"
        assert "7 turn" in v.reason

    def test_wip_is_unsafe(self):
        v = prune.assess(_rec(), _info(S.WIP, ahead=2))
        assert v.safe is False
        assert v.category == "unmerged"


# --- assess: terminal git states -------------------------------------------

class TestAssessStates:
    def test_active_is_unsafe(self):
        v = prune.assess(_rec(prs=[_pr(10, "merged")]), _info(S.ACTIVE))
        assert v.safe is False
        assert v.category == "active"

    def test_dirty_is_unsafe(self):
        v = prune.assess(_rec(), _info(S.DIRTY, dirty=3))
        assert v.safe is False
        assert v.category == "unmerged"
        assert "3 uncommitted" in v.reason

    def test_gone_is_flagged(self):
        v = prune.assess(_rec(), _info(S.GONE))
        assert v.safe is False
        assert v.category == "gone"

    def test_orphan_is_unsafe(self):
        v = prune.assess(_rec(), _info(S.ORPHAN))
        assert v.safe is False
        assert v.category == "unmerged"


# --- reconcile_pr_states ----------------------------------------------------

class TestReconcile:
    def test_stale_open_heals_to_merged(self):
        # Local says open; provider reports merged (external squash-merge).
        rec = _rec(prs=[_pr(1119, "open")])
        lookup = lambda repo, n: _FakePull(state="closed", merged=True, number=n)
        changes = prune.reconcile_pr_states(rec, lookup)
        assert changes == [(1119, "open", "merged")]
        assert rec.prs[0].state == "merged"
        # And now assess flips from open-pr (unsafe) to merged (safe).
        v = prune.assess(rec, _info(S.UNUSED))
        assert v.safe is True and v.category == "merged"

    def test_open_stays_open(self):
        rec = _rec(prs=[_pr(20, "open")])
        lookup = lambda repo, n: _FakePull(state="open", merged=False, number=n)
        assert prune.reconcile_pr_states(rec, lookup) == []
        assert rec.prs[0].state == "open"

    def test_terminal_not_rechecked_by_default(self):
        called = []
        rec = _rec(prs=[_pr(30, "merged")])

        def lookup(repo, n):
            called.append(n)
            return _FakePull(state="closed", merged=False, number=n)

        assert prune.reconcile_pr_states(rec, lookup) == []
        assert called == []  # only_live skips terminal records

    def test_only_live_false_rechecks_terminal(self):
        rec = _rec(prs=[_pr(30, "open")])  # locally open
        lookup = lambda repo, n: _FakePull(state="closed", merged=False, number=n)
        changes = prune.reconcile_pr_states(rec, lookup, only_live=False)
        assert changes == [(30, "open", "closed")]

    def test_lookup_failure_is_non_fatal(self):
        rec = _rec(prs=[_pr(40, "open")])

        def lookup(repo, n):
            raise RuntimeError("network down")

        assert prune.reconcile_pr_states(rec, lookup) == []
        assert rec.prs[0].state == "open"  # unchanged


class TestReconcileAndPersistBestEffort:
    """reconcile_and_persist_best_effort must heal PR state to disk WITHOUT
    clobbering a concurrent update the stale-base ``rec`` never saw (#4547)."""

    def _seed(self, tmp_path):
        from pathlib import Path
        path = Path(tmp_path) / "wt-1.yaml"
        tracking.save_record(_rec(status="active", prs=[_pr(50, "open")]), path)
        return path

    def test_heals_pr_state_to_disk(self, tmp_path):
        path = self._seed(tmp_path)
        rec = tracking.load_record(path)
        lookup = lambda repo, n: _FakePull(state="closed", merged=True, number=n)
        changes = prune.reconcile_and_persist_best_effort(
            rec, lookup, rec_path=path)
        assert changes == [(50, "open", "merged")]
        # In-memory rec (used by the caller's assessment) is healed...
        assert rec.prs[0].state == "merged"
        # ...and the heal reached disk.
        assert tracking.load_record(path).prs[0].state == "merged"

    def test_preserves_concurrent_update_on_stale_base(self, tmp_path):
        # The reviewer's scenario: `rec` is loaded (and threaded across
        # git/network work) BEFORE the reconcile. Meanwhile another writer
        # updates an UNRELATED field on disk. Persisting the reconcile must not
        # roll that concurrent update back -- the deltas are re-applied onto a
        # fresh reload, not the stale snapshot.
        path = self._seed(tmp_path)
        rec = tracking.load_record(path)  # stale base (no title)

        # Concurrent foreground writer sets a title on disk.
        other = tracking.load_record(path)
        other.title = "concurrent-title"
        tracking.save_record(other, path)

        lookup = lambda repo, n: _FakePull(state="closed", merged=True, number=n)
        prune.reconcile_and_persist_best_effort(rec, lookup, rec_path=path)

        on_disk = tracking.load_record(path)
        assert on_disk.prs[0].state == "merged"  # reconcile landed
        assert on_disk.title == "concurrent-title"  # concurrent update survived

    def test_no_changes_no_write(self, tmp_path):
        path = self._seed(tmp_path)
        rec = tracking.load_record(path)
        # Provider agrees the PR is still open -> no changes -> no persist.
        before = path.read_text()
        lookup = lambda repo, n: _FakePull(state="open", merged=False, number=n)
        assert prune.reconcile_and_persist_best_effort(
            rec, lookup, rec_path=path) == []
        assert path.read_text() == before  # untouched


# --- cleanup_disposition ----------------------------------------------------

class TestCleanupDisposition:
    def test_finalized_is_always_cleanable(self):
        d = prune.cleanup_disposition(_rec(status="finalized"), _info(S.COMPLETED))
        assert d.cleanable is True and d.bucket == "clean"

    def test_empty_needs_include_unused(self):
        rec = _rec(status="unused")
        d0 = prune.cleanup_disposition(rec, _info(S.UNUSED), turn_count=0)
        assert d0.cleanable is False and d0.bucket == "unused"
        d1 = prune.cleanup_disposition(rec, _info(S.UNUSED), turn_count=0,
                                       include_unused=True)
        assert d1.cleanable is True

    def test_conversation_only_needs_include_conversations(self):
        rec = _rec(status="unused")
        # --include-unused does NOT drop a conversation-only worktree.
        d_u = prune.cleanup_disposition(rec, _info(S.UNUSED), turn_count=5,
                                        include_unused=True)
        assert d_u.cleanable is False and d_u.bucket == "conversation"
        d_c = prune.cleanup_disposition(rec, _info(S.UNUSED), turn_count=5,
                                        include_conversations=True)
        assert d_c.cleanable is True

    def test_open_pr_is_preserved_even_with_include_unused(self):
        rec = _rec(status="active", prs=[_pr(21, "open")])
        d = prune.cleanup_disposition(rec, _info(S.UNUSED), turn_count=9,
                                      include_unused=True,
                                      include_conversations=True)
        assert d.cleanable is False and d.bucket == "open-pr"

    def test_merged_pr_unused_is_cleanable(self):
        rec = _rec(status="active", prs=[_pr(21, "merged")])
        d = prune.cleanup_disposition(rec, _info(S.UNUSED))
        assert d.cleanable is True and d.bucket == "clean"

    def test_active_is_never_cleanable(self):
        d = prune.cleanup_disposition(_rec(prs=[_pr(1, "merged")]), _info(S.ACTIVE))
        assert d.cleanable is False and d.bucket == "active"

    def test_follow_up_downgrades_finalized_to_review(self):
        # worktree-status-core: an agent-asserted follow-up makes a would-be
        # SAFE (finalized/clean) worktree REVIEW -- not auto-pruned.
        rec = _rec(status="finalized")
        rec.follow_up = True
        d = prune.cleanup_disposition(rec, _info(S.COMPLETED))
        assert d.cleanable is False
        assert d.bucket == "follow-up"
        assert "follow-ups" in d.reason

    def test_follow_up_downgrades_merged_pr(self):
        rec = _rec(status="active", prs=[_pr(21, "merged")])
        rec.follow_up = True
        d = prune.cleanup_disposition(rec, _info(S.UNUSED))
        assert d.cleanable is False and d.bucket == "follow-up"

    def test_follow_up_does_not_override_active(self):
        # An ACTIVE worktree stays 'active' (already non-cleanable); the flag
        # doesn't reclassify a live session.
        rec = _rec(status="finalized")
        rec.follow_up = True
        d = prune.cleanup_disposition(rec, _info(S.ACTIVE))
        assert d.bucket == "active"

    def test_follow_up_no_effect_when_not_flagged(self):
        d = prune.cleanup_disposition(_rec(status="finalized"), _info(S.COMPLETED))
        assert d.cleanable is True and d.bucket == "clean"


# --- citadel paired-worktree BOTH-gate (#957) -------------------------------

def _rec_paired(*, status="finalized", pair_kind="worktree",
                pair_ref="m/citadel-knowledge/wt-k"):
    r = _rec(status=status)
    r.pair_id = "p1"
    r.pair_role = "harness"
    r.pair_ref = pair_ref
    r.pair_kind = pair_kind
    return r


class TestPairedBothGate:
    """cleanup_disposition holds a paired worktree until BOTH halves finalize."""

    def test_finalized_pair_held_when_sibling_not_final(self):
        rec = _rec_paired()
        d = prune.cleanup_disposition(
            rec, _info(S.COMPLETED),
            paired_sibling_final=lambda r: False,
        )
        assert d.cleanable is False and d.bucket == "paired-pending"
        assert "BOTH paired worktrees finalized" in d.reason

    def test_finalized_pair_cleanable_when_sibling_final(self):
        rec = _rec_paired()
        d = prune.cleanup_disposition(
            rec, _info(S.COMPLETED),
            paired_sibling_final=lambda r: True,
        )
        assert d.cleanable is True and d.bucket == "clean"

    def test_unknown_sibling_holds(self):
        rec = _rec_paired()
        d = prune.cleanup_disposition(
            rec, _info(S.COMPLETED),
            paired_sibling_final=lambda r: None,
        )
        assert d.cleanable is False and d.bucket == "paired-pending"
        assert "unknown" in d.reason

    def test_merged_pair_held(self):
        rec = _rec_paired(status="active")
        rec.prs = [_pr(21, "merged")]
        d = prune.cleanup_disposition(
            rec, _info(S.UNUSED),
            paired_sibling_final=lambda r: False,
        )
        assert d.cleanable is False and d.bucket == "paired-pending"

    def test_no_probe_is_backward_compatible(self):
        # Without a probe injected the gate is inert (existing callers unaffected).
        rec = _rec_paired()
        d = prune.cleanup_disposition(rec, _info(S.COMPLETED))
        assert d.cleanable is True and d.bucket == "clean"

    def test_unpaired_flows_through(self):
        d = prune.cleanup_disposition(
            _rec(status="finalized"), _info(S.COMPLETED),
            paired_sibling_final=lambda r: False,
        )
        assert d.cleanable is True and d.bucket == "clean"

    def test_dirty_pair_stays_dirty_not_paired_pending(self):
        # The gate only downgrades the SAFE path; a dirty worktree is already held.
        rec = _rec_paired(status="active")
        d = prune.cleanup_disposition(
            rec, _info(S.DIRTY, dirty=2),
            paired_sibling_final=lambda r: False,
        )
        assert d.cleanable is False and d.bucket == "dirty"


class TestDefaultPairedSiblingFinal:
    """The default probe resolves the sibling from the tracking dir."""

    def _save(self, tracking_dir, rec):
        tracking.save_record(rec, tracking_dir / f"{rec.worktree_id}.yaml")

    def test_unpaired_true(self):
        assert prune.default_paired_sibling_final(_rec()) is True

    def test_anchor_true(self):
        rec = _rec_paired(pair_kind="anchor")
        assert prune.default_paired_sibling_final(rec) is True

    def test_sibling_finalized_true(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tracking.cfg, "tracking_dir", lambda: tmp_path)
        sib = _rec()
        sib.worktree_id = "wt-k"
        sib.status = "finalized"
        self._save(tmp_path, sib)
        rec = _rec_paired(pair_ref="m/k/wt-k")
        assert prune.default_paired_sibling_final(rec) is True

    def test_sibling_not_finalized_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tracking.cfg, "tracking_dir", lambda: tmp_path)
        sib = _rec()
        sib.worktree_id = "wt-k"
        sib.status = "active"
        self._save(tmp_path, sib)
        rec = _rec_paired(pair_ref="m/k/wt-k")
        assert prune.default_paired_sibling_final(rec) is False

    def test_sibling_missing_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tracking.cfg, "tracking_dir", lambda: tmp_path)
        rec = _rec_paired(pair_ref="m/k/wt-gone")
        assert prune.default_paired_sibling_final(rec) is None


# --- claimed-resource safety (resource-claims) ------------------------------

def _rec_owned(owner_ref, status="active", prs=None):
    r = _rec(status=status, prs=prs)
    r.owner_ref = owner_ref
    return r


class TestAssessClaimedResource:
    """A resource worktree with a live / not-confirmed-gone claimant is never
    prunable, above the git/PR verdict (claimed-resource-not-reclaimed)."""

    OWNER = "anomalous-potato/test-chamber/wt-A#s1"

    def test_claimed_alive_overrides_empty(self):
        rec = _rec_owned(self.OWNER, status="active")
        v = prune.assess(rec, _info(S.UNUSED), claimant_alive=lambda ref: True)
        assert v.safe is False and v.category == "claimed"
        assert self.OWNER in v.reason and "alive" in v.reason

    def test_claimed_unknown_spares_inflight(self):
        # None (unconfirmed) -> spare an IN-FLIGHT resource; absence of a local
        # owner is not proof. (A FINISHED resource is collectable regardless.)
        rec = _rec_owned(self.OWNER, status="active")
        v = prune.assess(rec, _info(S.WIP), claimant_alive=lambda ref: None)
        assert v.safe is False and v.category == "claimed"
        assert "unconfirmed" in v.reason

    def test_finished_completed_claimed_collectable_when_alive(self):
        # A git-COMPLETED owned resource is collectable even when
        # its claimant is alive -- the owner has demonstrably moved on.
        rec = _rec_owned(self.OWNER, status="active")
        v = prune.assess(rec, _info(S.COMPLETED), claimant_alive=lambda ref: True)
        assert v.safe is True and v.category == "completed-local"

    def test_finished_merged_pr_claimed_collectable_when_alive(self):
        rec = _rec_owned(self.OWNER, status="active", prs=[_pr(7, "merged")])
        v = prune.assess(rec, _info(S.COMPLETED), claimant_alive=lambda ref: True)
        assert v.safe is True and v.category == "merged"

    def test_inflight_dirty_claimed_spared_when_alive(self):
        # A dirty owned resource (unpushed work) is still protected under a live
        # claimant -- narrowing collects only FINISHED resources.
        rec = _rec_owned(self.OWNER, status="active")
        v = prune.assess(rec, _info(S.DIRTY, dirty=2), claimant_alive=lambda ref: True)
        assert v.safe is False and v.category == "claimed"

    def test_claimed_gone_falls_through(self):
        # False (confirmed gone) -> normal git verdict applies (empty is safe).
        rec = _rec_owned(self.OWNER)
        v = prune.assess(rec, _info(S.UNUSED), turn_count=0,
                         claimant_alive=lambda ref: False)
        assert v.safe is True and v.category == "empty"

    def test_no_probe_ignores_owner_ref(self):
        # Behavior is byte-identical when no probe is injected.
        rec = _rec_owned(self.OWNER)
        v = prune.assess(rec, _info(S.UNUSED), turn_count=0)
        assert v.category == "empty" and v.safe is True

    def test_probe_not_consulted_without_owner_ref(self):
        called = []
        rec = _rec(status="active")  # no owner_ref
        prune.assess(rec, _info(S.UNUSED),
                     claimant_alive=lambda ref: called.append(ref) or True)
        assert called == []

    def test_active_still_wins_over_claimed(self):
        rec = _rec_owned(self.OWNER)
        v = prune.assess(rec, _info(S.ACTIVE), claimant_alive=lambda ref: True)
        assert v.category == "active"


class TestCleanupDispositionClaimed:
    OWNER = "anomalous-potato/test-chamber/wt-A#s1"

    def test_finalized_claimed_is_collectable_when_alive(self):
        # The core flip: a finalized/COMPLETED resource is
        # collectable even while its claimant is alive -- a host kept open for
        # days must not pin its merged children forever.
        rec = _rec_owned(self.OWNER, status="finalized")
        d = prune.cleanup_disposition(rec, _info(S.COMPLETED),
                                      claimant_alive=lambda ref: True)
        assert d.cleanable is True and d.bucket == "clean"

    def test_inflight_claimed_spared_when_alive(self):
        # A still-in-flight (dirty) owned resource is spared under a live claimant.
        rec = _rec_owned(self.OWNER, status="active")
        d = prune.cleanup_disposition(rec, _info(S.DIRTY, dirty=1),
                                      claimant_alive=lambda ref: True)
        assert d.cleanable is False and d.bucket == "claimed"

    def test_finalized_claimed_followup_still_preserved(self):
        # A finalized+claimed resource the agent flagged with pending follow-ups
        # is still preserved (the follow-up gate wins over the clean path), just
        # as it does for a non-claimed finalized worktree.
        rec = _rec_owned(self.OWNER, status="finalized")
        rec.follow_up = True
        d = prune.cleanup_disposition(rec, _info(S.COMPLETED),
                                      claimant_alive=lambda ref: True)
        assert d.cleanable is False and d.bucket == "follow-up"

    def test_claimed_gone_is_cleanable(self):
        rec = _rec_owned(self.OWNER, status="finalized")
        d = prune.cleanup_disposition(rec, _info(S.COMPLETED),
                                      claimant_alive=lambda ref: False)
        assert d.cleanable is True and d.bucket == "clean"
