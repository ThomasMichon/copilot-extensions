"""Tests for agent_worktrees.pause -- the non-destructive worktree wrap-up
(sync forward + auto-settle provably-resolved claims + report, never error,
never remove the worktree directory or branch -- see the module docstring
for the full contract this fills alongside finalize's all-or-nothing
obligation gate)."""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import pause as pause_mod
from agent_worktrees import tracking
from agent_worktrees.tracking import ResourceClaim


def _add_claim(tracking_dir: Path, worktree_id: str, claim: ResourceClaim) -> None:
    yaml_path = tracking_dir / f"{worktree_id}.yaml"
    rec = tracking.load_record(yaml_path)
    rec.resources.append(claim)
    tracking.save_record(rec, yaml_path)


class TestPauseWorktree:
    def test_syncs_and_reports_clean_with_no_claims(self, pr_repo):
        config, wid, _wt_path, _remote = pr_repo
        result = pause_mod.pause_worktree(wid, config)
        assert result.synced is True
        assert result.settled == []
        assert result.remaining == []
        assert result.clean is True

    def test_refuses_to_mutate_ledger_on_dirty_tree(self, pr_repo, monkeypatch):
        """A failed sync must abort BEFORE any ledger access at all -- even
        for a claim the reclaim sweep would otherwise happily flip (the
        exact gap a real sync failure must never paper over)."""
        config, wid, wt_path, _remote = pr_repo
        (wt_path / "dirty.txt").write_text("uncommitted\n")

        from agent_worktrees import config as cfg_mod
        from agent_worktrees import sweep

        tracking_dir = Path(cfg_mod.tracking_dir())
        _add_claim(
            tracking_dir, wid,
            ResourceClaim(kind="pr", ref="o/r#42", state="active"),
        )
        # Make the claim provably gone+safe -- a real run would reclaim it.
        # pause must never even reach this check once sync has failed.
        monkeypatch.setattr(
            sweep, "make_resolvers",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("sync failure must abort before any sweep setup")),
        )

        result = pause_mod.pause_worktree(wid, config)

        assert result.synced is False
        assert result.settled == []
        assert result.remaining == []
        # The dirty-tree sync failure must not touch the claim ledger at all --
        # a retry after cleaning the tree should see the exact same state.
        rec = tracking.load_record(tracking_dir / f"{wid}.yaml")
        claim = next(c for c in rec.resources if c.ref == "o/r#42")
        assert claim.state == "active"

    def test_auto_settles_a_provably_merged_pr_claim(self, pr_repo, monkeypatch):
        """A `pr`-kind claim whose PR is provably merged is exactly the shape
        ``sweep.self_heal`` (the obligation gate's own self-heal path) already
        reclaims -- ``pause`` must surface it as settled, not leave it open."""
        config, wid, _wt_path, _remote = pr_repo
        from agent_worktrees import config as cfg_mod
        from agent_worktrees import sweep

        tracking_dir = Path(cfg_mod.tracking_dir())
        _add_claim(
            tracking_dir, wid,
            ResourceClaim(kind="pr", ref="o/r#42", state="active"),
        )

        monkeypatch.setattr(sweep, "claim_gone", lambda claim, config: True)
        monkeypatch.setattr(sweep, "claim_safe", lambda claim, config: True)

        result = pause_mod.pause_worktree(wid, config)

        assert result.synced is True
        assert {c["ref"] for c in result.settled} == {"o/r#42"}
        assert result.remaining == []
        assert result.clean is True

        rec = tracking.load_record(tracking_dir / f"{wid}.yaml")
        claim = next(c for c in rec.resources if c.ref == "o/r#42")
        assert claim.state == "released"

    def test_auto_settled_claim_is_recorded_in_claim_history(
        self, pr_repo, monkeypatch, tmp_path,
    ):
        """A claim pause reclaims must show up in the durable claim-history
        ledger immediately -- same as `claims sweep --apply` -- not just
        flip silently in the YAML (2026-10-03 review finding)."""
        config, wid, _wt_path, _remote = pr_repo
        from agent_worktrees import claim_history, sweep
        from agent_worktrees import config as cfg_mod

        monkeypatch.setattr(
            "agent_worktrees.config.install_dir", lambda: tmp_path / ".agent-worktrees",
        )

        tracking_dir = Path(cfg_mod.tracking_dir())
        _add_claim(
            tracking_dir, wid,
            ResourceClaim(kind="pr", ref="o/r#43", state="active"),
        )

        monkeypatch.setattr(sweep, "claim_gone", lambda claim, config: True)
        monkeypatch.setattr(sweep, "claim_safe", lambda claim, config: True)

        result = pause_mod.pause_worktree(wid, config)
        assert {c["ref"] for c in result.settled} == {"o/r#43"}

        events = claim_history.history_for_ref("o/r#43")
        assert any(e.get("event") == "released" for e in events)

    def test_reports_a_genuinely_open_claim_without_erroring(self, pr_repo, monkeypatch):
        """The whole point: an unsettled, NOT provably-safe claim is reported,
        never forced open or closed, and the call still succeeds overall."""
        config, wid, _wt_path, _remote = pr_repo
        from agent_worktrees import config as cfg_mod
        from agent_worktrees import sweep

        tracking_dir = Path(cfg_mod.tracking_dir())
        _add_claim(
            tracking_dir, wid,
            ResourceClaim(
                kind="task", ref="deadbeef", state="active", note="context handoff",
            ),
        )

        monkeypatch.setattr(sweep, "claim_gone", lambda claim, config: None)
        monkeypatch.setattr(sweep, "claim_safe", lambda claim, config: None)

        result = pause_mod.pause_worktree(wid, config)

        assert result.synced is True
        assert result.settled == []
        assert len(result.remaining) == 1
        assert result.remaining[0]["ref"] == "deadbeef"
        assert result.remaining[0]["note"] == "context handoff"
        assert result.clean is False

    def test_session_claims_never_count_as_remaining(self, pr_repo):
        """Mirrors finalize's own exclusion: a live "session" claim never
        blocks finalize on its own, so it must never show up in a pause
        report as something the operator needs to decide on."""
        config, wid, _wt_path, _remote = pr_repo
        from agent_worktrees import config as cfg_mod

        tracking_dir = Path(cfg_mod.tracking_dir())
        _add_claim(
            tracking_dir, wid,
            ResourceClaim(kind="session", ref=f"m/p/{wid}#sess-1", state="active"),
        )

        result = pause_mod.pause_worktree(wid, config)

        assert result.remaining == []
        assert result.clean is True

    def test_dry_run_never_mutates_the_ledger(self, pr_repo, monkeypatch):
        config, wid, _wt_path, _remote = pr_repo
        from agent_worktrees import config as cfg_mod
        from agent_worktrees import sweep

        tracking_dir = Path(cfg_mod.tracking_dir())
        _add_claim(
            tracking_dir, wid,
            ResourceClaim(kind="pr", ref="o/r#42", state="active"),
        )

        # Even though this claim WOULD be reclaimed by a real run, dry-run
        # must never invoke the reclaim sweep at all.
        monkeypatch.setattr(
            sweep, "make_resolvers",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("dry-run must not invoke the reclaim sweep")),
        )

        result = pause_mod.pause_worktree(wid, config, dry_run=True)

        assert result.synced is True
        assert result.settled == []
        assert len(result.remaining) == 1
        assert result.remaining[0]["ref"] == "o/r#42"

        rec = tracking.load_record(tracking_dir / f"{wid}.yaml")
        claim = next(c for c in rec.resources if c.ref == "o/r#42")
        assert claim.state == "active"
