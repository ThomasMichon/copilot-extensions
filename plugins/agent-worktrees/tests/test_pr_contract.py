"""Tests for the provider-neutral PR event/verdict contract (pr_contract).

Covers the two pure cores the ``pr-*`` family shares: the transition diff +
cursor (from pr-watch) and the head-aware verdict reduction + consent
classification (from pr-consent), plus the binding-absent = no-op invariant.
"""

from __future__ import annotations

from agent_worktrees import pr_contract as pc


def _rev(
    rid,
    state,
    user="reviewer",
    commit_id="head",
    dismissed=False,
    submitted_at="",
):
    return pc.Review(
        id=rid,
        state=state,
        user=user,
        commit_id=commit_id,
        dismissed=dismissed,
        submitted_at=submitted_at,
    )


# ---------------------------------------------------------------------------
# Cursor / Baseline
# ---------------------------------------------------------------------------

class TestCursor:
    def test_roundtrip_plain(self):
        assert pc.Baseline(max_review_id=13).to_cursor() == "r13"
        assert pc.Baseline.from_cursor("r13").max_review_id == 13

    def test_roundtrip_flags(self):
        b = pc.Baseline(max_review_id=1246, merged=True, closed=True)
        assert b.to_cursor() == "r1246.mc"
        parsed = pc.Baseline.from_cursor("r1246.mc")
        assert parsed.max_review_id == 1246
        assert parsed.merged is True
        assert parsed.closed is True

    def test_bare_integer_cursor(self):
        b = pc.Baseline.from_cursor("13")
        assert b.max_review_id == 13
        assert b.merged is False and b.closed is False

    def test_empty_cursor(self):
        assert pc.Baseline.from_cursor("").max_review_id == 0

    def test_invalid_cursor_raises(self):
        import pytest
        with pytest.raises(ValueError):
            pc.Baseline.from_cursor("rXYZ")

    def test_from_snapshot_high_water(self):
        snap = pc.PRSnapshot(
            reviews=(_rev(5, "APPROVED"), _rev(7, "COMMENT"), _rev(3, "PENDING")),
        )
        # PENDING is not a submitted state, so it does not raise the high-water.
        assert pc.Baseline.from_snapshot(snap).max_review_id == 7


# ---------------------------------------------------------------------------
# compute_events -- transition diff
# ---------------------------------------------------------------------------

class TestComputeEvents:
    def test_new_approval_fires(self):
        base = pc.Baseline(max_review_id=0)
        snap = pc.PRSnapshot(reviews=(_rev(1, "APPROVED"),))
        events = pc.compute_events(base, snap, pc.DEFAULT_UNTIL)
        assert [e["event"] for e in events] == ["approved"]
        assert events[0]["review"]["id"] == 1

    def test_review_at_or_below_cursor_ignored(self):
        base = pc.Baseline(max_review_id=1)
        snap = pc.PRSnapshot(reviews=(_rev(1, "APPROVED"),))
        assert pc.compute_events(base, snap, pc.DEFAULT_UNTIL) == []

    def test_author_own_review_never_fires(self):
        base = pc.Baseline(max_review_id=0)
        snap = pc.PRSnapshot(
            author="alice",
            reviews=(_rev(2, "APPROVED", user="alice"),),
        )
        assert pc.compute_events(base, snap, pc.DEFAULT_UNTIL) == []

    def test_changes_requested_variant_normalizes(self):
        base = pc.Baseline(max_review_id=0)
        snap = pc.PRSnapshot(reviews=(_rev(4, "REQUEST_CHANGES"),))
        events = pc.compute_events(base, snap, pc.DEFAULT_UNTIL)
        assert [e["event"] for e in events] == ["changes_requested"]

    def test_commented_excluded_from_default_until(self):
        base = pc.Baseline(max_review_id=0)
        snap = pc.PRSnapshot(reviews=(_rev(1, "COMMENT"),))
        assert pc.compute_events(base, snap, pc.DEFAULT_UNTIL) == []
        # ...but available under "any".
        events = pc.compute_events(base, snap, ("any",))
        assert [e["event"] for e in events] == ["commented"]

    def test_conflict_flip_fires(self):
        base = pc.Baseline(mergeable=True)
        snap = pc.PRSnapshot(pr_state="open", mergeable=False)
        events = pc.compute_events(base, snap, pc.DEFAULT_UNTIL)
        assert [e["event"] for e in events] == ["conflict"]

    def test_mergeable_recovery_fires(self):
        base = pc.Baseline(mergeable=False)
        snap = pc.PRSnapshot(pr_state="open", mergeable=True)
        events = pc.compute_events(base, snap, pc.DEFAULT_UNTIL)
        assert [e["event"] for e in events] == ["mergeable"]

    def test_none_baseline_mergeable_does_not_fire(self):
        base = pc.Baseline(mergeable=None)
        snap = pc.PRSnapshot(pr_state="open", mergeable=False)
        # A first concrete value is adopted by the caller, not fired here.
        assert pc.compute_events(base, snap, pc.DEFAULT_UNTIL) == []

    def test_merged_fires_once(self):
        snap = pc.PRSnapshot(pr_state="closed", merged=True)
        assert [e["event"] for e in pc.compute_events(
            pc.Baseline(merged=False), snap, pc.DEFAULT_UNTIL)] == ["merged"]
        assert pc.compute_events(pc.Baseline(merged=True), snap, pc.DEFAULT_UNTIL) == []

    def test_closed_unmerged_fires(self):
        snap = pc.PRSnapshot(pr_state="closed", merged=False)
        events = pc.compute_events(pc.Baseline(), snap, pc.DEFAULT_UNTIL)
        assert [e["event"] for e in events] == ["closed"]

    # --- CI checks + approval dismissal regressions (#225) -----------------

    def test_checks_failed_fires_on_transition_to_failure(self):
        base = pc.Baseline(checks_state="pending")
        snap = pc.PRSnapshot(pr_state="open", checks_state="failure")
        events = pc.compute_events(base, snap, pc.DEFAULT_UNTIL)
        assert [e["event"] for e in events] == ["checks_failed"]
        assert events[0]["checks_state"] == "failure"

    def test_checks_failed_not_refired_when_already_failure(self):
        base = pc.Baseline(checks_state="failure")
        snap = pc.PRSnapshot(pr_state="open", checks_state="failure")
        assert pc.compute_events(base, snap, pc.DEFAULT_UNTIL) == []

    def test_checks_unknown_baseline_does_not_fire(self):
        # "" == not-yet-known: adopted by the caller, never fired here.
        base = pc.Baseline(checks_state="")
        snap = pc.PRSnapshot(pr_state="open", checks_state="failure")
        assert pc.compute_events(base, snap, pc.DEFAULT_UNTIL) == []

    def test_checks_failed_not_fired_after_merge(self):
        base = pc.Baseline(checks_state="pending")
        snap = pc.PRSnapshot(pr_state="closed", merged=True, checks_state="failure")
        assert "checks_failed" not in [
            e["event"] for e in pc.compute_events(base, snap, pc.DEFAULT_UNTIL)]

    def test_approval_dismissed_fires_on_dismissed_approval(self):
        # Dismissal flips an existing (already-seen) review's flag, so baseline it.
        base = pc.Baseline(approved=True, max_review_id=5)
        snap = pc.PRSnapshot(
            pr_state="open", head_sha="h",
            reviews=(_rev(5, "APPROVED", dismissed=True),),
        )
        events = pc.compute_events(base, snap, pc.DEFAULT_UNTIL)
        assert [e["event"] for e in events] == ["approval_dismissed"]

    def test_approval_dismissed_does_not_fire_without_prior_approval(self):
        base = pc.Baseline(approved=None, max_review_id=5)  # never knew of approval
        snap = pc.PRSnapshot(
            pr_state="open", head_sha="h",
            reviews=(_rev(5, "APPROVED", dismissed=True),),
        )
        assert pc.compute_events(base, snap, pc.DEFAULT_UNTIL) == []

    def test_fresh_changes_requested_is_not_approval_dismissed(self):
        # A reviewer switching to changes-requested fires changes_requested (via
        # the review-id loop), NOT approval_dismissed (no dismissed approval).
        base = pc.Baseline(approved=True, max_review_id=5)
        snap = pc.PRSnapshot(
            pr_state="open", head_sha="h",
            reviews=(_rev(5, "APPROVED"), _rev(9, "CHANGES_REQUESTED")),
        )
        events = [e["event"] for e in pc.compute_events(base, snap, pc.DEFAULT_UNTIL)]
        assert events == ["changes_requested"]
        assert "approval_dismissed" not in events


# ---------------------------------------------------------------------------
# effective_verdict -- head-aware reduction
# ---------------------------------------------------------------------------

class TestEffectiveVerdict:
    def test_latest_review_wins(self):
        reviews = (_rev(1, "APPROVED"), _rev(2, "CHANGES_REQUESTED"))
        assert pc.effective_verdict(reviews, "head", "author") == "CHANGES_REQUESTED"

    def test_stale_approval_on_old_head_ignored(self):
        reviews = (_rev(1, "APPROVED", commit_id="old"),)
        assert pc.effective_verdict(reviews, "new", "author") == ""

    def test_stale_approval_can_be_retained_by_policy(self):
        reviews = (
            _rev(
                1,
                "APPROVED",
                commit_id="old",
                submitted_at="2026-01-01T00:02:00Z",
            ),
        )
        assert pc.effective_verdict(
            reviews,
            "new",
            "author",
            allow_stale_approval=True,
            stale_approval_head_sha="new",
            stale_approval_head_pushed_at="2026-01-01T00:01:00Z",
        ) == "APPROVED"

    def test_stale_approval_before_current_head_is_not_retained(self):
        reviews = (
            _rev(
                1,
                "APPROVED",
                commit_id="old",
                submitted_at="2026-01-01T00:01:00Z",
            ),
        )
        assert pc.effective_verdict(
            reviews,
            "new",
            "author",
            allow_stale_approval=True,
            stale_approval_head_sha="new",
            stale_approval_head_pushed_at="2026-01-01T00:02:00Z",
        ) == ""

    def test_stale_approval_without_matching_publication_evidence_is_not_retained(self):
        reviews = (
            _rev(
                1,
                "APPROVED",
                commit_id="old",
                submitted_at="2026-01-01T00:02:00Z",
            ),
        )
        assert pc.effective_verdict(
            reviews,
            "new",
            "author",
            allow_stale_approval=True,
            stale_approval_head_sha="different",
            stale_approval_head_pushed_at="2026-01-01T00:01:00Z",
        ) == ""

    def test_approval_at_current_head_counts(self):
        reviews = (_rev(1, "APPROVED", commit_id="head"),)
        assert pc.effective_verdict(reviews, "head", "author") == "APPROVED"

    def test_author_own_review_ignored(self):
        reviews = (_rev(1, "APPROVED", user="alice", commit_id="head"),)
        assert pc.effective_verdict(reviews, "head", "alice") == ""

    def test_dismissed_ignored(self):
        reviews = (_rev(1, "APPROVED", commit_id="head", dismissed=True),)
        assert pc.effective_verdict(reviews, "head", "author") == ""

    def test_comment_is_not_a_verdict(self):
        assert pc.effective_verdict((_rev(1, "COMMENT"),), "head", "author") == ""

    def test_request_changes_variant_normalizes(self):
        reviews = (_rev(1, "REQUEST_CHANGES"),)
        assert pc.effective_verdict(reviews, "head", "author") == "CHANGES_REQUESTED"


# ---------------------------------------------------------------------------
# title_is_wip / merge_state
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_title_is_wip(self):
        prefixes = ("wip:", "[wip]", "draft:")
        assert pc.title_is_wip("WIP: thing", prefixes) is True
        assert pc.title_is_wip("[WIP] thing", prefixes) is True
        assert pc.title_is_wip("normal title", prefixes) is False

    def test_title_is_wip_no_prefixes_is_noop(self):
        assert pc.title_is_wip("WIP: thing", ()) is False

    def test_ensure_wip_title_prepends_canonical(self):
        # A plain title gains the canonical server-recognised prefix.
        assert pc.ensure_wip_title("Add feature") == "WIP: Add feature"

    def test_ensure_wip_title_idempotent_on_native_prefix(self):
        # Already server-recognised (WIP:/[WIP]) -> returned unchanged.
        assert pc.ensure_wip_title("WIP: Add feature") == "WIP: Add feature"
        assert pc.ensure_wip_title("[WIP] Add feature") == "[WIP] Add feature"

    def test_ensure_wip_title_forces_prefix_on_non_native_marker(self):
        # "Draft:" is NOT a server-recognised prefix, so the PR would open
        # non-draft -- ensure must still prepend the canonical WIP: so the
        # server actually marks it draft (issue: false draft:true report).
        assert pc.ensure_wip_title("Draft: Add feature") == "WIP: Draft: Add feature"

    def test_strip_wip_title_removes_single_prefix(self):
        clean, was_wip = pc.strip_wip_title("WIP: Add feature")
        assert (clean, was_wip) == ("Add feature", True)

    def test_strip_wip_title_removes_all_stacked_prefixes(self):
        # A doubly-marked title must end fully un-drafted, not with a residual
        # recognised prefix that leaves the server still seeing it as draft.
        clean, was_wip = pc.strip_wip_title("WIP: [WIP] Add feature")
        assert (clean, was_wip) == ("Add feature", True)

    def test_strip_wip_title_noop_reports_not_wip(self):
        clean, was_wip = pc.strip_wip_title("Add feature")
        assert (clean, was_wip) == ("Add feature", False)

    def test_strip_wip_title_does_not_overstrip(self):
        # A title whose body merely starts with a WIP-ish word is left intact.
        clean, was_wip = pc.strip_wip_title("WIP: wips of change")
        assert (clean, was_wip) == ("wips of change", True)

    def test_merge_state(self):
        assert pc.merge_state(pc.PRSnapshot(merged=True)) == "merged"
        assert pc.merge_state(pc.PRSnapshot(pr_state="closed")) == "closed"
        assert pc.merge_state(pc.PRSnapshot(mergeable=False)) == "conflict"
        assert pc.merge_state(pc.PRSnapshot(mergeable=True)) == "clean"
        assert pc.merge_state(pc.PRSnapshot(mergeable=None)) == "unknown"


# ---------------------------------------------------------------------------
# classify_state -- the one shared classifier
# ---------------------------------------------------------------------------

_BINDING = dict(
    automerge_label="auto-merge",
    hold_labels=("do-not-merge", "needs-rebase", "wip"),
    wip_title_prefixes=("wip:", "[wip]", "draft:"),
)


def _approved(**kw):
    base = dict(
        pr_state="open", merged=False, head_sha="head", mergeable=True,
        author="alice", reviews=(_rev(1, "APPROVED", commit_id="head"),),
    )
    base.update(kw)
    return pc.PRSnapshot(**base)


class TestClassifyState:
    def test_approved_eligible_applies(self):
        st = pc.classify_state(_approved(), **_BINDING)
        assert st.verdict == "APPROVED"
        assert st.merge_state == "clean"
        assert st.consent_action == "apply"
        assert st.eligible is True

    def test_consent_already_present(self):
        st = pc.classify_state(_approved(labels=("auto-merge",)), **_BINDING)
        assert st.consent_present is True
        assert st.consent_action == "already"
        assert st.eligible is False

    def test_hold_label_skips(self):
        st = pc.classify_state(_approved(labels=("needs-rebase",)), **_BINDING)
        assert st.held == ("needs-rebase",)
        assert st.consent_action == "skip"
        assert "hold label" in st.reason

    def test_wip_title_skips(self):
        st = pc.classify_state(_approved(title="WIP: not ready"), **_BINDING)
        assert st.wip is True
        assert st.consent_action == "skip"

    def test_draft_skips(self):
        st = pc.classify_state(_approved(draft=True), **_BINDING)
        assert st.wip is True
        assert st.consent_action == "skip"
        assert st.reason == "draft"

    def test_conflict_skips(self):
        st = pc.classify_state(_approved(mergeable=False), **_BINDING)
        assert st.conflict is True
        assert st.merge_state == "conflict"
        assert st.consent_action == "skip"

    def test_changes_requested_skips(self):
        snap = _approved(reviews=(_rev(1, "CHANGES_REQUESTED", commit_id="head"),))
        st = pc.classify_state(snap, **_BINDING)
        assert st.verdict == "CHANGES_REQUESTED"
        assert st.consent_action == "skip"

    def test_unapproved_skips(self):
        snap = _approved(reviews=())
        st = pc.classify_state(snap, **_BINDING)
        assert st.verdict == ""
        assert st.reason == "not yet approved"

    def test_merged_skips(self):
        st = pc.classify_state(_approved(merged=True, pr_state="closed"), **_BINDING)
        assert st.merge_state == "merged"
        assert st.consent_action == "skip"

    # -- binding-absent = no-op / no crash --------------------------------

    def test_binding_absent_no_crash(self):
        st = pc.classify_state(_approved())
        # Approved + mergeable, but no consent mechanism configured: not an
        # error, just nothing to apply.
        assert st.verdict == "APPROVED"
        assert st.held == ()
        assert st.wip is False
        assert st.consent_action == "skip"
        assert "no auto-merge label" in st.reason
        assert st.eligible is False

    def test_binding_absent_ignores_hold_and_wip_labels(self):
        # With no hold_labels / wip prefixes bound, those signals are inert.
        snap = _approved(labels=("do-not-merge",), title="WIP: x")
        st = pc.classify_state(snap)
        assert st.held == ()
        assert st.wip is False


# ---------------------------------------------------------------------------
# merge_readiness -- the caller-facing "what to do next" summary
# ---------------------------------------------------------------------------

class TestMergeReadiness:
    def test_approved_needs_consent(self):
        m = pc.merge_readiness(_approved(), **_BINDING)
        assert m["needs_consent"] is True          # caller must add the label
        assert m["consent_action"] == "apply"
        assert m["clear_to_merge"] is True
        assert m["consent_present"] is False
        assert m["consent_label"] == "auto-merge"
        assert m["verdict"] == "APPROVED"

    def test_consent_already_present(self):
        m = pc.merge_readiness(_approved(labels=("auto-merge",)), **_BINDING)
        assert m["needs_consent"] is False
        assert m["consent_action"] == "already"
        assert m["clear_to_merge"] is True

    def test_changes_requested_no_consent(self):
        snap = _approved(reviews=(_rev(1, "CHANGES_REQUESTED", commit_id="head"),))
        m = pc.merge_readiness(snap, **_BINDING)
        assert m["needs_consent"] is False
        assert m["clear_to_merge"] is False
        assert m["consent_action"] == "skip"
        assert m["reason"] == "changes requested"

    def test_binding_absent_degrades_cleanly(self):
        m = pc.merge_readiness(_approved())
        assert m["needs_consent"] is False
        assert m["clear_to_merge"] is False
        assert m["consent_label"] == ""
        assert "no auto-merge label" in m["reason"]


# ---------------------------------------------------------------------------
# PR-flow profile (classify_pr_flow) -- per-repo applicability
# ---------------------------------------------------------------------------

class TestClassifyPRFlow:
    def test_direct_when_pr_disabled(self):
        f = pc.classify_pr_flow(enabled=False)
        assert f.profile == pc.PROFILE_DIRECT
        assert f.requires_pr is False
        assert f.merge_mode == "direct"
        assert f.applicable_verbs == ()
        assert f.applies("pr-merge") is False
        assert f.applies("create-pr") is False

    def test_agent_merge_when_automerge_label_bound(self):
        f = pc.classify_pr_flow(
            enabled=True, required=True, provider="gitea",
            automerge_label="auto-merge",
        )
        assert f.profile == pc.PROFILE_PR_AGENT_MERGE
        assert f.requires_pr is True
        assert f.merge_mode == "agent-consent"
        # Full family applies, including pr-merge (the consent step).
        assert f.applies("pr-merge") is True
        assert f.applies("pr-watch") is True
        assert f.applies("pr-complete") is True
        assert "auto-merge" in f.summary

    def test_human_merge_when_enabled_but_no_label(self):
        f = pc.classify_pr_flow(
            enabled=True, required=True, provider="github", automerge_label="",
        )
        assert f.profile == pc.PROFILE_PR_HUMAN_MERGE
        assert f.merge_mode == "human"
        # Everything BUT pr-merge applies -- a human merges.
        assert f.applies("pr-merge") is False
        assert f.applies("create-pr") is True
        assert f.applies("pr-watch") is True
        assert f.applies("pr-status") is True
        assert f.applies("pr-complete") is True
        assert "human" in f.summary.lower()
        assert "pr-merge does not apply" in f.summary

    def test_required_reflected_even_without_label(self):
        f = pc.classify_pr_flow(enabled=True, required=False, automerge_label="")
        assert f.profile == pc.PROFILE_PR_HUMAN_MERGE
        assert f.requires_pr is False

    def test_self_merge_when_self_approve(self):
        f = pc.classify_pr_flow(
            enabled=True, required=True, provider="github",
            automerge_label="", self_approve=True,
            reviewer="copilot", review_blocking=False,
            review_latency_hint="~2m",
        )
        assert f.profile == pc.PROFILE_PR_SELF_MERGE
        assert f.merge_mode == "self-direct"
        # pr-merge applies here (the --now direct-merge path).
        assert f.applies("pr-merge") is True
        assert f.applies("create-pr") is True
        assert f.reviewer == "copilot"
        assert f.review_latency_hint == "~2m"
        assert f.self_approve is True

    def test_self_merge_when_merge_actor_submitter_direct(self):
        f = pc.classify_pr_flow(
            enabled=True, required=True, automerge_label="",
            merge_actor="submitter-direct",
        )
        assert f.profile == pc.PROFILE_PR_SELF_MERGE

    def test_agent_merge_wins_over_self_approve(self):
        # An explicit consent label keeps the agent-consent shape even if
        # self_approve is also set (label is the stronger signal).
        f = pc.classify_pr_flow(
            enabled=True, required=True, automerge_label="auto-merge",
            self_approve=True,
        )
        assert f.profile == pc.PROFILE_PR_AGENT_MERGE

    def test_matrix_fields_carried(self):
        f = pc.classify_pr_flow(
            enabled=True, required=True, automerge_label="auto-merge",
            reviewer="agent:reviewer", review_blocking=True,
            conflict_retriggers_review=True,
        )
        assert f.reviewer == "agent:reviewer"
        assert f.review_blocking is True
        assert f.conflict_retriggers_review is True
        assert f.rebase_owner == "submitter"

    def test_policy_defaults_carried(self):
        # #225: the merge/update policy is carried onto every non-direct profile.
        f = pc.classify_pr_flow(
            enabled=True, required=True, automerge_label="auto-merge",
        )
        assert f.branch_update_strategy == "rebase"
        assert f.merge_strategy == "squash"
        assert f.prefer_auto_merge is True

    def test_policy_overrides_carried(self):
        f = pc.classify_pr_flow(
            enabled=True, required=True, automerge_label="auto-merge",
            branch_update_strategy="merge", merge_strategy="merge",
            prefer_auto_merge=False,
        )
        assert f.branch_update_strategy == "merge"
        assert f.merge_strategy == "merge"
        assert f.prefer_auto_merge is False


# ---------------------------------------------------------------------------
# Adopt-time research: RepoPolicy -> policy matrix mapping (#225)
# ---------------------------------------------------------------------------

class TestDerivePolicyMatrix:
    def test_unsupported_yields_empty(self):
        assert pc.derive_policy_matrix(pc.RepoPolicy(supported=False)) == {}

    def test_squash_preferred_when_allowed(self):
        m = pc.derive_policy_matrix(pc.RepoPolicy(
            allow_squash=True, allow_merge_commit=True, allow_rebase=True))
        assert m["merge_strategy"] == "squash"

    def test_merge_when_squash_disallowed(self):
        m = pc.derive_policy_matrix(pc.RepoPolicy(
            allow_squash=False, allow_merge_commit=True, allow_rebase=False))
        assert m["merge_strategy"] == "merge"
        # rebase disallowed + merge allowed -> branch update via merge.
        assert m["branch_update_strategy"] == "merge"

    def test_rebase_only(self):
        m = pc.derive_policy_matrix(pc.RepoPolicy(
            allow_squash=False, allow_merge_commit=False, allow_rebase=True))
        assert m["merge_strategy"] == "rebase"

    def test_prefer_auto_merge_mirrors_setting(self):
        assert pc.derive_policy_matrix(
            pc.RepoPolicy(allow_auto_merge=True))["prefer_auto_merge"] is True
        assert pc.derive_policy_matrix(
            pc.RepoPolicy(allow_auto_merge=False))["prefer_auto_merge"] is False

    def test_review_blocking_from_required_reviews_or_checks(self):
        assert pc.derive_policy_matrix(
            pc.RepoPolicy(required_approving_reviews=1))["review_blocking"] is True
        assert pc.derive_policy_matrix(
            pc.RepoPolicy(required_approving_reviews=0,
                          has_required_status_checks=False))["review_blocking"] is False
        assert pc.derive_policy_matrix(
            pc.RepoPolicy(required_approving_reviews=0,
                          has_required_status_checks=True))["review_blocking"] is True

    def test_unknown_settings_omitted(self):
        # All-None settings speak to nothing -> no keys emitted (defaults apply).
        assert pc.derive_policy_matrix(pc.RepoPolicy()) == {}


# ---------------------------------------------------------------------------
# PR-flow reminders (pr_reminder) -- state-aware, stay-on-the-rails guidance
# ---------------------------------------------------------------------------

#: Bypass tokens a reminder must NEVER emit (the module HARD INVARIANT).
_FORBIDDEN = (
    "gh pr", "gh api", "gh repo", "az repos", "az pipelines",
    "--admin", "--force", "--no-verify", "curl ", "git push origin",
)


def _self_merge_flow(**kw):
    base = dict(enabled=True, required=True, provider="github",
                automerge_label="", self_approve=True, reviewer="copilot",
                review_latency_hint="~2m")
    base.update(kw)
    return pc.classify_pr_flow(**base)


class TestPRReminder:
    def test_create_pr_self_merge_points_at_pr_merge_now(self):
        flow = _self_merge_flow()
        r = pc.pr_reminder(flow, "create-pr")
        assert r.ok is True
        assert "pr-merge" in r.next_step and "--now" in r.next_step
        assert "self-approve" not in r.next_step
        assert "cannot approve their own" in r.next_step
        assert r.waiting_on  # waits on the copilot review

    def test_github_submitter_direct_waits_for_blocking_review(self):
        flow = _self_merge_flow(
            self_approve=False,
            merge_actor="submitter-direct",
            reviewer="independent reviewer",
            review_blocking=True,
        )
        r = pc.pr_reminder(flow, "create-pr")
        assert "wait for" in r.next_step
        assert "independent reviewer" in r.next_step
        assert "self-approve" not in r.text()
        assert r.waiting_on

    def test_non_github_explicit_self_approval_uses_neutral_gating(self):
        flow = _self_merge_flow(provider="azure-devops")
        r = pc.pr_reminder(flow, "create-pr")
        assert "required checks/reviews" in r.next_step
        assert "self-approve" not in r.next_step

    def test_blocking_self_merge_reminder_waits_on_approval(self):
        flow = _self_merge_flow(review_blocking=True)
        r = pc.pr_reminder(flow, "pr-merge")
        assert r.waiting_on == ("approved", "merged")
        assert "required checks/reviews" in r.next_step

    def test_blocking_self_merge_refusal_waits_instead_of_retrying(self):
        flow = _self_merge_flow(review_blocking=True)
        r = pc.pr_reminder(
            flow,
            "pr-merge",
            ok=False,
            reason="the direct merge did not complete",
        )
        assert r.use_instead == ("pr-watch", "pr-status")
        assert "pr-merge --now" not in r.use_instead

    def test_pr_merge_refused_on_human_merge_offers_sanctioned_alternatives(self):
        flow = pc.classify_pr_flow(enabled=True, required=True,
                                   provider="github", automerge_label="",
                                   reviewer="agent:reviewer")
        r = pc.pr_reminder(flow, "pr-merge", ok=False,
                           reason="you cannot merge in this repo")
        assert r.ok is False
        assert "pr-watch" in r.use_instead or "pr-status" in r.use_instead
        assert r.next_step  # tells you who merges instead

    def test_pr_merge_refused_on_self_merge_points_at_now(self):
        # A bare pr-merge on a self-merge repo is a no-op -> steer at `--now`,
        # never a raw provider merge.
        flow = _self_merge_flow()
        r = pc.pr_reminder(flow, "pr-merge", ok=False,
                           reason="this repo merges directly (self-merge)")
        assert r.ok is False
        assert "--now" in r.next_step
        assert "pr-merge --now" in r.use_instead

    def test_pr_merge_merged_state_points_at_finalize(self):
        # After the direct self-merge lands, the only sanctioned next step is
        # cleaning up the worktree.
        flow = _self_merge_flow()
        r = pc.pr_reminder(flow, "pr-merge", state=pc.PR_STATE_MERGED, ok=True)
        assert "finalize" in r.next_step
        assert r.waiting_on == ()


    def test_direct_repo_points_at_finalize(self):
        flow = pc.classify_pr_flow(enabled=False)
        r = pc.pr_reminder(flow, "pr-merge")
        assert "finalize" in (r.next_step + " " + " ".join(r.use_instead))

    def test_direct_repo_create_pr_still_points_at_finalize(self):
        # create-pr does NOT start with "pr" -- the blocked/direct reminder
        # must still steer it to the sanctioned finalize verb.
        flow = pc.classify_pr_flow(enabled=False)
        for verb in ("create-pr", "pr-create"):
            r = pc.pr_reminder(flow, verb)
            assert "finalize" in r.use_instead, verb

    def test_conflict_caution_present_when_retriggers_review(self):
        flow = _self_merge_flow(conflict_retriggers_review=True)
        r = pc.pr_reminder(flow, "pr-watch")
        assert any("re-triggers review" in c for c in r.cautions)

    def test_policy_caution_surfaced_at_point_of_action(self):
        # #225: the repo's update/merge policy is surfaced in the reminder so an
        # agent sees it at the moment of action.
        flow = pc.classify_pr_flow(
            enabled=True, required=True, automerge_label="auto-merge",
            branch_update_strategy="rebase", merge_strategy="squash",
            prefer_auto_merge=True,
        )
        for verb in ("create-pr", "pr-watch", "pr-merge", "push-changes"):
            r = pc.pr_reminder(flow, verb)
            assert any("policy:" in c and "rebase" in c and "squash" in c
                       for c in r.cautions), verb
        # prefer_auto_merge is reflected in the phrasing.
        r = pc.pr_reminder(flow, "pr-watch")
        assert any("auto-merge" in c for c in r.cautions)

    def test_policy_caution_absent_on_direct_repo(self):
        flow = pc.classify_pr_flow(enabled=False)
        r = pc.pr_reminder(flow, "pr-merge")
        assert not any("policy:" in c for c in r.cautions)

    def test_create_pr_nudges_manual_reviewer_request(self):
        # #3581 nudge: a reviewer-gated repo (not self-merge) whose PR review is
        # triggered by the repo's own process should remind that *requesting* a
        # reviewer is a manual, org/platform-specific step.
        flow = pc.classify_pr_flow(enabled=True, required=True, provider="gitea",
                                   automerge_label="auto-merge",
                                   reviewer="agent:mantis-counter")
        r = pc.pr_reminder(flow, "create-pr")
        assert any("manual" in c and "reviewer" in c for c in r.cautions)

    def test_blocking_self_merge_nudges_manual_reviewer_request(self):
        flow = _self_merge_flow(
            reviewer="independent reviewer",
            review_blocking=True,
        )
        r = pc.pr_reminder(flow, "create-pr")
        assert any("manual" in c and "reviewer" in c for c in r.cautions)

    def test_create_pr_no_reviewer_nudge_when_no_reviewer(self):
        flow = pc.classify_pr_flow(enabled=True, required=True, provider="gitea",
                                   automerge_label="auto-merge")  # reviewer=""
        r = pc.pr_reminder(flow, "create-pr")
        assert not any("requesting a reviewer" in c for c in r.cautions)

    def test_as_dict_shape(self):
        flow = _self_merge_flow()
        d = pc.pr_reminder(flow, "create-pr").as_dict()
        for k in ("profile", "verb", "ok", "next", "waiting_on",
                  "use_instead", "cautions"):
            assert k in d

    def test_no_bypass_tactics_in_any_reminder(self):
        # Scan every (profile x verb x outcome) reminder's rendered text +
        # structured fields for forbidden bypass tokens.
        flows = [
            pc.classify_pr_flow(enabled=False),
            pc.classify_pr_flow(enabled=True, required=True,
                                automerge_label="auto-merge", reviewer="agent:r",
                                review_blocking=True),
            _self_merge_flow(),
            pc.classify_pr_flow(enabled=True, required=True, automerge_label="",
                                reviewer="agent:reviewer"),
        ]
        verbs = ("create-pr", "pr-watch", "pr-status", "pr-merge",
                 "pr-complete", "push-changes")
        for flow in flows:
            for verb in verbs:
                for ok in (True, False):
                    r = pc.pr_reminder(flow, verb, ok=ok,
                                       reason="blocked" if not ok else "")
                    blob = (r.text() + " " + repr(r.as_dict())).lower()
                    for bad in _FORBIDDEN:
                        assert bad.lower() not in blob, (
                            f"reminder for {flow.profile}/{verb} ok={ok} "
                            f"leaked bypass token {bad!r}: {blob}")


# ---------------------------------------------------------------------------
# approval_required knob (self-complete: eligible without an approval vote)
# ---------------------------------------------------------------------------

class TestApprovalRequired:
    def test_no_reviews_requires_approval_by_default(self):
        snap = pc.PRSnapshot(pr_state="open", mergeable=True, title="ok")
        st = pc.classify_state(snap, automerge_label="auto-complete")
        assert st.consent_action == "skip"
        assert "not yet approved" in st.reason

    def test_no_reviews_eligible_when_approval_not_required(self):
        snap = pc.PRSnapshot(pr_state="open", mergeable=True, title="ok")
        st = pc.classify_state(snap, automerge_label="auto-complete",
                               approval_required=False)
        assert st.consent_action == "apply"

    def test_changes_requested_still_blocks_without_approval(self):
        snap = pc.PRSnapshot(pr_state="open", mergeable=True,
                             reviews=(_rev(1, "CHANGES_REQUESTED"),))
        st = pc.classify_state(snap, automerge_label="auto-complete",
                               approval_required=False)
        assert st.consent_action == "skip"
        assert "changes requested" in st.reason

    def test_approved_eligible_regardless(self):
        snap = pc.PRSnapshot(pr_state="open", mergeable=True,
                             reviews=(_rev(1, "APPROVED"),))
        st = pc.classify_state(snap, automerge_label="auto-complete")
        assert st.consent_action == "apply"

    def test_stale_approval_is_visible_but_blocked_by_default(self):
        snap = pc.PRSnapshot(
            pr_state="open",
            mergeable=True,
            head_sha="new",
            reviews=(_rev(1, "APPROVED", commit_id="old"),),
        )
        st = pc.classify_state(snap, automerge_label="auto-complete")
        assert st.verdict == ""
        assert st.approval_stale is True
        assert st.consent_action == "skip"

    def test_stale_approval_can_authorize_merge_when_policy_permits(self):
        snap = pc.PRSnapshot(
            pr_state="open",
            mergeable=True,
            head_sha="new",
            reviews=(
                _rev(
                    1,
                    "APPROVED",
                    commit_id="old",
                    submitted_at="2026-01-01T00:02:00Z",
                ),
            ),
        )
        readiness = pc.merge_readiness(
            snap,
            automerge_label="auto-complete",
            allow_stale_approval=True,
            stale_approval_head_sha="new",
            stale_approval_head_pushed_at="2026-01-01T00:01:00Z",
        )
        assert readiness["verdict"] == "APPROVED"
        assert readiness["approval_stale"] is True
        assert readiness["approval_stale_authorized"] is True
        assert readiness["consent_action"] == "apply"
        assert readiness["clear_to_merge"] is True
        assert "after current head" in readiness["reason"]

    def test_post_approval_push_remains_unapproved(self):
        snap = pc.PRSnapshot(
            pr_state="open",
            mergeable=True,
            head_sha="new",
            reviews=(
                _rev(
                    1,
                    "APPROVED",
                    commit_id="old",
                    submitted_at="2026-01-01T00:01:00Z",
                ),
            ),
        )
        readiness = pc.merge_readiness(
            snap,
            automerge_label="auto-complete",
            allow_stale_approval=True,
            stale_approval_head_sha="new",
            stale_approval_head_pushed_at="2026-01-01T00:02:00Z",
        )
        assert readiness["verdict"] == ""
        assert readiness["approval_stale"] is True
        assert readiness["approval_stale_authorized"] is False
        assert readiness["consent_action"] == "skip"


class TestThreadTypes:
    def test_thread_active_and_result_helpers(self):
        active = pc.CommentThread(id=1, status="active",
                                  comments=(pc.Comment(author="a", content="x"),))
        resolved = pc.CommentThread(id=2, status="fixed")
        res = pc.ThreadsResult(threads=(active, resolved))
        assert active.is_active is True and resolved.is_active is False
        assert [t.id for t in res.active] == [1]
        assert res.supported is True
