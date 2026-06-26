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
        handoff_prompt=None,
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
