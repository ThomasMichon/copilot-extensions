"""PR obligation claims, confirmed abandonment, and terminal reconciliation."""

from __future__ import annotations

import pytest
from agent_worktrees import claim_history
from agent_worktrees import config as cfg
from agent_worktrees import obligations, pr_ops, tracking

pytestmark = pytest.mark.contract("agent_worktrees.pr_ops.claims")


class TestPrClaimHelpers:
    """pr-merge-obligation-gate defense 2: `_ensure_pr_claim`/`_release_pr_claim`."""

    def _rec(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        rec = tracking.WorktreeRecord(
            worktree_id="wt-c", branch="worktree/wt-c",
            worktree_path=str(tmp_path / "wt"), repo="o/r", machine="m",
            platform="linux", started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
            status="active", completed_at=None, sessions=None,
        )
        tracking.save_record(rec, tmp_path / "wt-c.yaml")
        return rec

    def test_ref_prefers_url_over_shorthand(self):
        pr = tracking.PRRecord(number=9, repo="o/r", url="https://x/pull/9")
        assert pr_ops._pr_claim_ref(pr) == "https://x/pull/9"

    def test_ref_falls_back_to_shorthand(self):
        pr = tracking.PRRecord(number=9, repo="o/r")
        assert pr_ops._pr_claim_ref(pr) == "o/r#9"

    def test_ref_empty_without_number_or_repo(self):
        assert pr_ops._pr_claim_ref(tracking.PRRecord()) == ""

    def test_numberless_creating_pr_is_never_claimed(self, tmp_path, monkeypatch):
        """A failed/`--no-open` create-pr leaves a numberless `creating`
        record -- it must never block finalize forever."""
        rec = self._rec(tmp_path, monkeypatch)
        pr_ops._ensure_pr_claim(rec, tracking.PRRecord(state="creating"))
        assert rec.resources == []

    def test_numbered_but_unconfirmed_state_is_never_claimed(self, tmp_path, monkeypatch):
        """A bare `set-pr`-recorded number with no provider-observed state
        yet must not be blindly trusted (set_pr persists state with NO
        provider read)."""
        rec = self._rec(tmp_path, monkeypatch)
        pr_ops._ensure_pr_claim(rec, tracking.PRRecord(number=5, repo="o/r", state=""))
        assert rec.resources == []

    def test_confirmed_open_is_claimed(self, tmp_path, monkeypatch):
        rec = self._rec(tmp_path, monkeypatch)
        pr = tracking.PRRecord(number=5, repo="o/r", state="open")
        pr_ops._ensure_pr_claim(rec, pr)
        assert len(rec.resources) == 1
        assert rec.resources[0].kind == "pr" and rec.resources[0].state == "active"
        assert rec.resources[0].ref == "o/r#5"

    def test_release_settles_to_released_not_abandoned(self, tmp_path, monkeypatch):
        rec = self._rec(tmp_path, monkeypatch)
        pr = tracking.PRRecord(number=5, repo="o/r", state="open")
        pr_ops._ensure_pr_claim(rec, pr)
        pr_ops._release_pr_claim(rec, pr)
        assert rec.resources[0].state == "released"

    def test_release_without_existing_claim_is_a_no_op(self, tmp_path, monkeypatch):
        rec = self._rec(tmp_path, monkeypatch)
        pr_ops._release_pr_claim(rec, tracking.PRRecord(number=5, repo="o/r"))
        assert rec.resources == []

    def test_ensure_pr_claim_returns_ref_on_a_real_new_claim(self, tmp_path, monkeypatch):
        """Save-ordering contract: `_ensure_pr_claim` never feeds
        claim_history itself (it always runs with `save=False`) -- it
        returns the ref only for a genuinely NEW claim, so the caller can
        record history after ITS OWN save is confirmed."""
        rec = self._rec(tmp_path, monkeypatch)
        pr = tracking.PRRecord(number=5, repo="o/r", state="open")
        assert pr_ops._ensure_pr_claim(rec, pr) == "o/r#5"
        assert claim_history.history_for_ref("o/r#5") == []

    def test_ensure_pr_claim_returns_none_for_an_idempotent_no_op(self, tmp_path, monkeypatch):
        """A reconciliation re-observing an already-active claim is not a
        real transition -- must not be reported as a fresh 'claimed' event
        by the caller."""
        rec = self._rec(tmp_path, monkeypatch)
        pr = tracking.PRRecord(number=5, repo="o/r", state="open")
        pr_ops._ensure_pr_claim(rec, pr)
        assert pr_ops._ensure_pr_claim(rec, pr) is None

    def test_release_pr_claim_returns_ref_on_a_real_release(self, tmp_path, monkeypatch):
        rec = self._rec(tmp_path, monkeypatch)
        pr = tracking.PRRecord(number=5, repo="o/r", state="open")
        pr_ops._ensure_pr_claim(rec, pr)
        assert pr_ops._release_pr_claim(rec, pr) == "o/r#5"
        assert claim_history.history_for_ref("o/r#5") == []

    def test_release_pr_claim_returns_none_for_an_already_released_claim(
        self, tmp_path, monkeypatch,
    ):
        rec = self._rec(tmp_path, monkeypatch)
        pr = tracking.PRRecord(number=5, repo="o/r", state="open")
        pr_ops._ensure_pr_claim(rec, pr)
        pr_ops._release_pr_claim(rec, pr)
        assert pr_ops._release_pr_claim(rec, pr) is None

    def test_release_without_existing_claim_returns_none(self, tmp_path, monkeypatch):
        rec = self._rec(tmp_path, monkeypatch)
        assert pr_ops._release_pr_claim(rec, tracking.PRRecord(number=5, repo="o/r")) is None


class TestAbandonPr:
    """``pr-abandon``'s two-step confirm gate and close/claim-settle contract
    -- the pragmatic #4411 follow-up (friction, not a real identity
    primitive)."""

    def _config(self):
        return cfg.Config(
            srcroot="/s", machine="m", platform="linux", repo_name="ext",
            repos={"ext": cfg.RepoConfig(
                anchor="/a", worktree_root="/w",
                pr=cfg.PRConfig(enabled=True, provider="gitea"),
            )},
        )

    def _record(self, tmp_path, monkeypatch, *, state="open", claimed=True):
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        rec = tracking.WorktreeRecord(
            worktree_id="wt-a", branch="worktree/wt-a",
            worktree_path=str(tmp_path / "wt"), repo="o/r", machine="m",
            platform="linux", started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
            status="active", completed_at=None, sessions=None,
        )
        rec.pr = tracking.PRRecord(
            state=state, number=7, branch="pr/x", provider="gitea", repo="o/r")
        if claimed:
            tracking.add_resource_claim(
                rec, tracking.ResourceClaim(
                    kind="pr", ref=pr_ops._pr_claim_ref(rec.pr),
                    created_at=tracking._now_iso(), state="active"),
                save=False)
        tracking.save_record(rec, tmp_path / "wt-a.yaml")
        return rec

    def _fake_provider(self, *, merged=False, state="open"):
        from agent_worktrees.providers import PullResult

        class _P:
            name = "gitea"

            def __init__(self):
                self.closed = []
                self.close_calls = 0

            def get_pull(self, repo, number, *, api_base="", token=None):
                return PullResult(number=number, state=state, merged=merged)

            def close_pull(self, repo, number, *, api_base="", token=None, comment=""):
                self.close_calls += 1
                self.closed.append((repo, number, comment))
                return ""

        return _P()

    def _patch(self, monkeypatch, fake):
        import agent_worktrees.providers as prov
        monkeypatch.setattr(prov, "get_provider", lambda name: fake)
        monkeypatch.setattr(prov, "account_token_for_slug", lambda slug, prcfg: "t")

    # ── the two-step confirm gate itself ─────────────────────────────────

    def test_first_call_without_confirm_refuses_and_mutates_nothing(
        self, tmp_path, monkeypatch,
    ):
        rec = self._record(tmp_path, monkeypatch)
        fake = self._fake_provider()
        self._patch(monkeypatch, fake)
        result = pr_ops.abandon_pr(
            "wt-a", self._config(), reason="superseded by #99", confirm=False,
        )
        assert result["success"] is False
        assert result["needs_confirm"] is True
        assert "--confirm" in result["error"]
        assert "force-push" in result["error"]
        assert fake.close_calls == 0
        reloaded = tracking.load_record(rec.yaml_path)
        assert reloaded.active_pr().state == "open"
        assert reloaded.resources[0].state == "active"

    def test_reason_required_even_without_confirm(self, tmp_path, monkeypatch):
        self._record(tmp_path, monkeypatch)
        result = pr_ops.abandon_pr("wt-a", self._config(), reason="", confirm=False)
        assert result["success"] is False
        assert "needs_confirm" not in result
        assert "--reason" in result["error"]

    def test_reason_required_even_with_confirm(self, tmp_path, monkeypatch):
        self._record(tmp_path, monkeypatch)
        result = pr_ops.abandon_pr("wt-a", self._config(), reason="  ", confirm=True)
        assert result["success"] is False
        assert "--reason" in result["error"]

    # ── the confirmed path ────────────────────────────────────────────────

    def test_confirm_closes_pr_and_settles_claim_abandoned(self, tmp_path, monkeypatch):
        rec = self._record(tmp_path, monkeypatch)
        fake = self._fake_provider(merged=False, state="open")
        self._patch(monkeypatch, fake)
        result = pr_ops.abandon_pr(
            "wt-a", self._config(), reason="superseded by #99", confirm=True,
        )
        assert result["success"] is True
        assert result["closed"] is True
        assert result["already_closed"] is False
        assert result["claim_released"] is True
        assert fake.close_calls == 1
        closed_repo, closed_number, comment = fake.closed[0]
        assert closed_repo == "o/r" and closed_number == 7
        assert "superseded by #99" in comment
        reloaded = tracking.load_record(rec.yaml_path)
        assert reloaded.active_pr().state == "closed"
        assert reloaded.active_pr().closed_at
        claims = [c for c in reloaded.resources if c.kind == "pr"]
        assert len(claims) == 1
        assert claims[0].state == obligations.ABANDONED
        assert "superseded by #99" in claims[0].note
        events = claim_history.history_for_ref(pr_ops._pr_claim_ref(rec.pr))
        assert [e["event"] for e in events] == ["abandoned"]
        assert events[0]["note"] == "superseded by #99"

    def test_confirm_refuses_if_already_merged_live(self, tmp_path, monkeypatch):
        """Never trust local pr.state alone -- a live-confirmed merge refuses
        the abandon outright. The shared _reconcile_active_pr self-heal
        runs first and settles the claim to `released` (a clean hand-back,
        matching its own merged-PR contract) -- abandon_pr's own refusal is
        on top of that, not instead of it."""
        rec = self._record(tmp_path, monkeypatch)
        fake = self._fake_provider(merged=True, state="closed")
        self._patch(monkeypatch, fake)
        result = pr_ops.abandon_pr(
            "wt-a", self._config(), reason="superseded", confirm=True,
        )
        assert result["success"] is False
        assert "already merged" in result["error"]
        assert fake.close_calls == 0
        reloaded = tracking.load_record(rec.yaml_path)
        assert reloaded.active_pr().state == "merged"
        assert reloaded.resources[0].state == "released"

    def test_confirm_on_already_closed_pr_settles_claim_without_reclosing(
        self, tmp_path, monkeypatch,
    ):
        rec = self._record(tmp_path, monkeypatch)
        fake = self._fake_provider(merged=False, state="closed")
        self._patch(monkeypatch, fake)
        result = pr_ops.abandon_pr(
            "wt-a", self._config(), reason="superseded", confirm=True,
        )
        assert result["success"] is True
        assert result["already_closed"] is True
        assert fake.close_calls == 0  # never re-closed an already-closed PR
        reloaded = tracking.load_record(rec.yaml_path)
        claims = [c for c in reloaded.resources if c.kind == "pr"]
        assert claims[0].state == obligations.ABANDONED

    def test_confirm_with_no_prior_claim_still_closes(self, tmp_path, monkeypatch):
        """A PR tracked without ever having been claimed (e.g. a legacy
        record) is still closeable. The shared reconcile self-heal claims
        it just-in-time (it observes a confirmed-open PR), so abandon_pr's
        own settle finds and abandons THAT claim -- not a precondition
        failure."""
        rec = self._record(tmp_path, monkeypatch, claimed=False)
        fake = self._fake_provider(merged=False, state="open")
        self._patch(monkeypatch, fake)
        result = pr_ops.abandon_pr(
            "wt-a", self._config(), reason="superseded", confirm=True,
        )
        assert result["success"] is True
        assert result["claim_released"] is True
        reloaded = tracking.load_record(rec.yaml_path)
        assert reloaded.active_pr().state == "closed"
        claims = [c for c in reloaded.resources if c.kind == "pr"]
        assert claims[0].state == obligations.ABANDONED

    def test_already_merged_tracked_state_refuses_before_any_network_call(
        self, tmp_path, monkeypatch,
    ):
        self._record(tmp_path, monkeypatch, state="merged")
        fake = self._fake_provider()
        self._patch(monkeypatch, fake)
        result = pr_ops.abandon_pr(
            "wt-a", self._config(), reason="superseded", confirm=True,
        )
        assert result["success"] is False
        assert "already merged" in result["error"]
        assert fake.close_calls == 0

    def test_comment_post_failure_is_a_warning_not_a_fatal_error(
        self, tmp_path, monkeypatch,
    ):
        rec = self._record(tmp_path, monkeypatch)

        class _WarnProvider(self._fake_provider().__class__):
            def close_pull(self, repo, number, *, api_base="", token=None, comment=""):
                return "comment post failed: unauthorized"

        self._patch(monkeypatch, _WarnProvider())
        result = pr_ops.abandon_pr(
            "wt-a", self._config(), reason="superseded", confirm=True,
        )
        assert result["success"] is True
        assert "comment post failed" in result["warning"]
        reloaded = tracking.load_record(rec.yaml_path)
        assert reloaded.active_pr().state == "closed"

    def test_real_close_failure_is_fatal_and_leaves_claim_untouched(
        self, tmp_path, monkeypatch,
    ):
        rec = self._record(tmp_path, monkeypatch)

        class _FailProvider(self._fake_provider().__class__):
            def close_pull(self, repo, number, *, api_base="", token=None, comment=""):
                return "gh pr close failed: network error"

        self._patch(monkeypatch, _FailProvider())
        result = pr_ops.abandon_pr(
            "wt-a", self._config(), reason="superseded", confirm=True,
        )
        assert result["success"] is False
        assert "network error" in result["error"]
        reloaded = tracking.load_record(rec.yaml_path)
        assert reloaded.active_pr().state == "open"
        assert reloaded.resources[0].state == "active"


class TestReconcileActivePrSelfHeal:
    """#1375/#1703: reconcile heals a zombie open PR whose content already merged."""

    def _config(self):
        return cfg.Config(
            srcroot="/s", machine="m", platform="linux", repo_name="ext",
            repos={"ext": cfg.RepoConfig(
                anchor="/a", worktree_root="/w",
                pr=cfg.PRConfig(enabled=True, provider="gitea"),
            )},
        )

    def _record(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        rec = tracking.WorktreeRecord(
            worktree_id="wt-z", branch="worktree/wt-z",
            worktree_path=str(tmp_path / "wt"), repo="o/r", machine="m",
            platform="linux", started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
            status="active", completed_at=None, sessions=None,
        )
        rec.pr = tracking.PRRecord(
            state="open", number=7, branch="pr/x", provider="gitea", repo="o/r")
        tracking.save_record(rec)
        return rec

    def _fake_provider(self, *, merged, contained):
        from agent_worktrees.providers import PullResult

        class _P:
            name = "gitea"

            def __init__(self):
                self.contained_calls = []

            def get_pull(self, repo, number, *, api_base="", token=None):
                return PullResult(number=number, state="open", merged=merged,
                                  head_sha="abc", base_ref="master")

            def head_contained_in_base(self, repo, base, head_sha, *,
                                       api_base="", token=None):
                self.contained_calls.append((base, head_sha))
                return contained

        return _P()

    def _patch(self, monkeypatch, fake):
        import agent_worktrees.providers as prov
        monkeypatch.setattr(prov, "get_provider", lambda name: fake)
        monkeypatch.setattr(prov, "account_token_for_slug", lambda slug, prcfg: "t")

    def test_zombie_heals_to_merged(self, tmp_path, monkeypatch):
        rec = self._record(tmp_path, monkeypatch)
        fake = self._fake_provider(merged=False, contained=True)
        self._patch(monkeypatch, fake)
        pr_ops._reconcile_active_pr(rec, self._config())
        assert rec.active_pr().state == "merged"
        assert fake.contained_calls == [("master", "abc")]
        # Persisted to disk.
        assert tracking.load_record(rec.yaml_path).active_pr().state == "merged"

    def test_still_ahead_stays_open(self, tmp_path, monkeypatch):
        rec = self._record(tmp_path, monkeypatch)
        fake = self._fake_provider(merged=False, contained=False)
        self._patch(monkeypatch, fake)
        pr_ops._reconcile_active_pr(rec, self._config())
        assert rec.active_pr().state == "open"

    def test_reactivated_released_claim_is_persisted_and_feeds_history(
        self, tmp_path, monkeypatch,
    ):
        """A PR claim released then re-observed open must be reactivated,
        SAVED, and feed claim_history -- not silently skipped just because
        a (now-stale) claim entry already exists for that ref."""
        rec = self._record(tmp_path, monkeypatch)
        rec.resources = [
            tracking.ResourceClaim(kind="pr", ref="o/r#7", state=obligations.RELEASED),
        ]
        tracking.save_record(rec)
        fake = self._fake_provider(merged=False, contained=False)
        self._patch(monkeypatch, fake)
        pr_ops._reconcile_active_pr(rec, self._config())
        assert rec.resources[0].state == obligations.ACTIVE
        reloaded = tracking.load_record(rec.yaml_path)
        assert reloaded.resources[0].state == obligations.ACTIVE
        events = claim_history.history_for_ref("o/r#7")
        assert [e["event"] for e in events] == ["claimed"]

    def test_unknown_containment_leaves_open(self, tmp_path, monkeypatch):
        rec = self._record(tmp_path, monkeypatch)
        fake = self._fake_provider(merged=False, contained=None)
        self._patch(monkeypatch, fake)
        pr_ops._reconcile_active_pr(rec, self._config())
        assert rec.active_pr().state == "open"

    def test_provider_merged_flag_short_circuits_probe(self, tmp_path, monkeypatch):
        rec = self._record(tmp_path, monkeypatch)
        fake = self._fake_provider(merged=True, contained=None)
        self._patch(monkeypatch, fake)
        pr_ops._reconcile_active_pr(rec, self._config())
        assert rec.active_pr().state == "merged"
        assert fake.contained_calls == []  # no containment probe when already merged

    def test_best_effort_persists_when_uncontended(self, tmp_path, monkeypatch):
        rec = self._record(tmp_path, monkeypatch)
        fake = self._fake_provider(merged=False, contained=True)
        self._patch(monkeypatch, fake)
        pr_ops._reconcile_active_pr(rec, self._config(), best_effort=True)
        assert tracking.load_record(rec.yaml_path).active_pr().state == "merged"

    # ── pr-merge-obligation-gate defense 2: claim lifecycle ──────────────────

    def test_confirmed_open_creates_active_pr_claim(self, tmp_path, monkeypatch):
        """A provider-confirmed-still-open PR gets an active `pr` claim --
        the structural gate that blocks finalize independent of pr.strategy."""
        rec = self._record(tmp_path, monkeypatch)
        assert rec.resources == []
        fake = self._fake_provider(merged=False, contained=False)
        self._patch(monkeypatch, fake)
        pr_ops._reconcile_active_pr(rec, self._config())
        claims = [c for c in rec.resources if c.kind == "pr"]
        assert len(claims) == 1
        assert claims[0].state == "active"
        assert claims[0].ref == pr_ops._pr_claim_ref(rec.active_pr())
        # Persisted, not just in-memory.
        on_disk = [c for c in tracking.load_record(rec.yaml_path).resources
                   if c.kind == "pr"]
        assert len(on_disk) == 1 and on_disk[0].state == "active"

    def test_confirmed_open_claim_is_idempotent(self, tmp_path, monkeypatch):
        rec = self._record(tmp_path, monkeypatch)
        fake = self._fake_provider(merged=False, contained=False)
        self._patch(monkeypatch, fake)
        pr_ops._reconcile_active_pr(rec, self._config())
        pr_ops._reconcile_active_pr(rec, self._config())
        assert len([c for c in rec.resources if c.kind == "pr"]) == 1

    def test_merge_releases_pr_claim(self, tmp_path, monkeypatch):
        """A merged PR's claim settles to `released` -- a clean hand-back,
        not the sweep's `abandoned` (involuntary reclaim) disposition."""
        rec = self._record(tmp_path, monkeypatch)
        # Pre-existing active claim, as if `create-pr` claimed it earlier.
        tracking.add_resource_claim(
            rec, tracking.ResourceClaim(
                kind="pr", ref=pr_ops._pr_claim_ref(rec.active_pr()),
                created_at=tracking._now_iso(), state="active"),
            save=False)
        fake = self._fake_provider(merged=True, contained=None)
        self._patch(monkeypatch, fake)
        pr_ops._reconcile_active_pr(rec, self._config())
        claims = [c for c in rec.resources if c.kind == "pr"]
        assert len(claims) == 1 and claims[0].state == "released"

    def test_zombie_heal_to_merged_also_releases_claim(self, tmp_path, monkeypatch):
        rec = self._record(tmp_path, monkeypatch)
        tracking.add_resource_claim(
            rec, tracking.ResourceClaim(
                kind="pr", ref=pr_ops._pr_claim_ref(rec.active_pr()),
                created_at=tracking._now_iso(), state="active"),
            save=False)
        fake = self._fake_provider(merged=False, contained=True)
        self._patch(monkeypatch, fake)
        pr_ops._reconcile_active_pr(rec, self._config())
        claims = [c for c in rec.resources if c.kind == "pr"]
        assert len(claims) == 1 and claims[0].state == "released"

    def test_closed_unmerged_never_releases_claim(self, tmp_path, monkeypatch):
        """A closed-without-merge PR is abandoned WORK, not a clean
        hand-back -- its claim stays active (blocking) until an operator
        explicitly abandons it, mirroring sweep.py's own
        never-silently-reclaim-an-unmerged-close contract."""
        rec = self._record(tmp_path, monkeypatch)
        tracking.add_resource_claim(
            rec, tracking.ResourceClaim(
                kind="pr", ref=pr_ops._pr_claim_ref(rec.active_pr()),
                created_at=tracking._now_iso(), state="active"),
            save=False)

        from agent_worktrees.providers import PullResult

        class _ClosedProvider:
            name = "gitea"

            def get_pull(self, repo, number, *, api_base="", token=None):
                return PullResult(number=number, state="closed", merged=False,
                                  head_sha="abc", base_ref="master")

            def head_contained_in_base(self, *a, **k):
                return None

        self._patch(monkeypatch, _ClosedProvider())
        pr_ops._reconcile_active_pr(rec, self._config())
        claims = [c for c in rec.resources if c.kind == "pr"]
        assert len(claims) == 1 and claims[0].state == "active"
