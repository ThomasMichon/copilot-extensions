"""PR association, history, serialization, and concurrent record updates."""

from __future__ import annotations

import types
import pytest
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking
from pr_test_helpers import PRWorkflowSetup, _git

pytestmark = pytest.mark.contract("agent_worktrees.pr_ops.tracking")


class TestSetPRAndStatus:
    def test_status_no_pr(self, pr_repo):
        _config, wid, _wt_path, _ = pr_repo
        res = pr_ops.pr_status(wid)
        assert res["has_pr"] is False

    def test_status_missing_record(self, pr_repo):
        res = pr_ops.pr_status("does-not-exist")
        assert res["has_pr"] is False
        assert "error" in res

    def test_set_pr_creates_block(self, pr_repo):
        _config, wid, _wt_path, _ = pr_repo
        res = pr_ops.set_pr(
            wid, url="https://example/pulls/7", number=7, provider="gitea"
        )
        assert res["success"] is True
        assert res["number"] == 7
        assert res["state"] == "open"  # defaulted
        # Persisted
        st = pr_ops.pr_status(wid)
        assert st["has_pr"] is True
        assert st["url"] == "https://example/pulls/7"
        assert st["number"] == 7

    def test_set_pr_derives_repo_slug_from_github_url(self, pr_repo):
        """Regression test: `set-pr --url ...` (the documented manual-
        registration path for a PR opened outside create-pr's own flow) must
        populate `pr.repo` with the hosting `owner/repo` slug parsed from the
        URL -- without it, every downstream operation needing that slug
        (e.g. pr-nudge's requested_reviewers call) silently fell back to the
        worktree's generic local project name instead, a real 404 whenever
        the PR's actual host repo has a different name/owner than the local
        project."""
        _config, wid, _wt_path, _ = pr_repo
        res = pr_ops.set_pr(
            wid,
            url="https://github.com/SomeOwner/some-other-repo/pull/42",
            number=42,
            provider="github",
        )
        assert res["success"] is True
        assert res["repo"] == "SomeOwner/some-other-repo"
        st = pr_ops.pr_status(wid)
        assert st["repo"] == "SomeOwner/some-other-repo"

    def test_set_pr_derives_repo_slug_from_path_hosted_gitea_url(self, pr_repo):
        """A self-hosted Gitea instance's `api_base` can carry an arbitrary
        path prefix (`providers/gitea.py`'s own `create_pull` produces URLs
        like `https://h/gitea/o/r/pulls/42`) -- a third path segment between
        host and `owner/repo` the plain host-relative pattern never matches.
        Stripping the configured `api_base` as a prefix first must still
        resolve the correct slug."""
        config, wid, _wt_path, _ = pr_repo
        import dataclasses
        repo = config.repos["ext"]
        pr_cfg = dataclasses.replace(repo.pr, api_base="https://h/gitea")
        config = dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr_cfg)},
        )
        res = pr_ops.set_pr(
            wid,
            url="https://h/gitea/o/r/pulls/42",
            number=42,
            provider="gitea",
            config=config,
        )
        assert res["success"] is True
        assert res["repo"] == "o/r"

    def test_set_pr_repo_change_clears_attribution_evidence(self, pr_repo):
        """A parsed repo change (not just a number/provider change) must
        also clear stale attribution/observation evidence -- the create/
        reuse path already does this for an explicit --repo change:
        without it, `refresh_source_attribution` can incorrectly
        short-circuit as already published against the OLD repo's merge
        evidence."""
        _config, wid, _wt_path, _ = pr_repo
        pr_ops.set_pr(
            wid, url="https://github.com/OwnerA/repo-a/pull/1", number=1,
            provider="github",
        )
        rec_path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(rec_path)
        rec.active_pr().attribution_head = "deadbeef"
        rec.active_pr().head_observed_at = "2026-01-01T00:00:00Z"
        rec.active_pr().head_observed_api_base = "https://old-base"
        tracking.save_record(rec)

        res = pr_ops.set_pr(
            wid, url="https://github.com/OwnerB/repo-b/pull/1", number=1,
            provider="github",
        )
        assert res["success"] is True
        assert res["repo"] == "OwnerB/repo-b"
        rec = tracking.load_record(rec_path)
        pr = rec.active_pr()
        assert pr.attribution_head == ""
        assert pr.head_observed_at == ""
        assert pr.head_observed_api_base == ""

    def test_set_pr_leaves_repo_unset_for_an_unparseable_url(self, pr_repo):
        """An ADO-shaped (or any otherwise-unrecognized) URL has no `owner/
        repo` concept this parser can extract -- `pr.repo` must stay unset
        (not raise, not silently guess), same as before this field existed."""
        _config, wid, _wt_path, _ = pr_repo
        res = pr_ops.set_pr(
            wid,
            url="https://dev.azure.com/org/project/_git/repo/pullrequest/123",
            number=123,
            provider="azure_devops",
        )
        assert res["success"] is True
        assert res.get("repo") == ""

    def test_set_pr_freezes_attribution_and_stamps_pr_id(self, pr_repo):
        # codename-attribution-by-default (round-27 finding): manual set-pr
        # is a fresh-construction site too -- the shared stamping helper
        # must be wired into it, not only into create_pr's own construction.
        # pr_repo's config resolves the bare "codename" default with
        # source_attribution_configured False.
        _config, wid, _wt_path, _ = pr_repo
        res = pr_ops.set_pr(
            wid, url="https://example/pulls/7", number=7, provider="gitea"
        )
        assert res["success"] is True
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        pr = rec.active_pr()
        assert pr is not None
        assert pr.attribution_mode == "codename"
        assert pr.attribution_explicit is False
        assert pr.pr_id
        assert pr.pr_revision == 1

    def test_set_pr_freezes_using_resolved_config_not_ambient(self, pr_repo):
        # PR #3037 review finding: cmd_set_pr already resolves its own
        # config (honoring --config/project context), which must be
        # threaded into set_pr's freeze step -- re-loading AMBIENT config
        # here would use the wrong repo's policy from a neutral CWD or
        # when --config targets a different project. Simulate an "ambient"
        # config that would resolve to a DIFFERENT (raw True) policy than
        # the one explicitly passed in, and assert the PASSED-IN config
        # wins.
        import dataclasses
        config, wid, _wt_path, _ = pr_repo
        explicit_config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    config.repos["ext"],
                    pr=dataclasses.replace(
                        config.repos["ext"].pr,
                        source_attribution=True,
                        source_attribution_configured=True,
                    ),
                )
            },
        )
        res = pr_ops.set_pr(
            wid, url="https://example/pulls/7", number=7, provider="gitea",
            config=explicit_config,
        )
        assert res["success"] is True
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        pr = rec.active_pr()
        assert pr is not None
        assert pr.attribution_mode == "true"
        assert pr.attribution_explicit is True

    def test_set_pr_merges_with_create_pr(self, pr_repo):
        config, wid, _wt_path, _ = pr_repo
        created = pr_ops.create_pr(wid, config, title="Add feature")
        assert created["success"]
        res = pr_ops.set_pr(wid, url="https://example/pulls/9", number=9)
        assert res["success"] is True
        # create-pr's branch/head_sha preserved
        assert res["branch"] == "feature/add-feature-aaaa"
        assert res["head_sha"] == created["head_sha"]
        assert res["number"] == 9

    def test_set_pr_identity_change_clears_head_observation(self, pr_repo):
        _config, wid, _wt_path, _ = pr_repo
        pr_ops.set_pr(wid, number=7, provider="gitea")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.active_pr().head_sha = "same-head"
        rec.active_pr().head_observed_at = "2026-09-05T06:01:02+00:00"
        tracking.save_record(rec)

        res = pr_ops.set_pr(wid, number=8)

        assert res["head_sha"] == ""
        persisted = tracking.load_record(
            cfg.tracking_dir() / f"{wid}.yaml"
        ).active_pr()
        assert persisted.head_observed_at == ""

    def test_set_pr_invalid_state(self, pr_repo):
        _config, wid, _wt_path, _ = pr_repo
        res = pr_ops.set_pr(wid, state="bogus")
        assert res["success"] is False
        assert "Invalid PR state" in res["error"]

    def test_set_pr_state_transition(self, pr_repo):
        _config, wid, _wt_path, _ = pr_repo
        pr_ops.set_pr(wid, url="u", number=1)
        res = pr_ops.set_pr(wid, state="merged")
        assert res["success"] is True
        assert res["state"] == "merged"
        assert res["number"] == 1  # preserved

    def test_set_pr_missing_record(self, pr_repo):
        res = pr_ops.set_pr("does-not-exist", number=1)
        assert res["success"] is False
        assert "No tracking record" in res["error"]

    def test_set_pr_backfills_pr_id_before_branch_correction_no_duplicate(
        self, pr_repo,
    ):
        # PR #3037 review finding: a legacy (no-pr_id) on-disk entry whose
        # branch/number is corrected by a manual `set_pr` call must have
        # its `pr_id` backfilled and PERSISTED in its own save BEFORE that
        # correction is applied -- otherwise `_save_record_unlocked`'s
        # merge can't recognize the renamed in-memory entry as the same
        # as the pre-rename on-disk one (both blank `pr_id`, but now
        # different branch values), and duplicate-appends the stale copy.
        _config, wid, _wt_path, _ = pr_repo
        yaml_path = cfg.tracking_dir() / f"{wid}.yaml"
        record = tracking.load_record(yaml_path)
        legacy_pr = tracking.PRRecord(
            branch="legacy/original-branch", number=1, state="open",
        )
        assert not legacy_pr.pr_id
        record.prs.append(legacy_pr)
        tracking.save_record(record)

        res = pr_ops.set_pr(wid, branch="legacy/corrected-branch", number=1)

        assert res["success"] is True
        persisted = tracking.load_record(yaml_path)
        assert len(persisted.prs) == 1
        only_pr = persisted.prs[0]
        assert only_pr.pr_id
        assert only_pr.branch == "legacy/corrected-branch"


class TestMultiPR:
    def test_serial_re_pr_after_merge_opens_fresh_pr(self, pr_repo):
        """The #1088->#1104 regression: a merged PR must NOT be reused."""
        config, wid, wt_path, _ = pr_repo
        r1 = pr_ops.create_pr(wid, config, title="Add feature")
        assert r1["success"], r1
        assert r1["branch"] == "feature/add-feature-aaaa"
        pr_ops.set_pr(wid, number=1, state="merged")

        # Back to the base branch; do new work for a second PR.
        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "d.txt").write_text("second\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "second work", cwd=wt_path)

        r2 = pr_ops.create_pr(wid, config, title="Second feature")
        assert r2["success"], r2
        assert "rerun" not in r2  # NOT the reuse path
        assert r2["branch"] == "feature/second-feature-aaaa"

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert len(rec.prs) == 2
        assert rec.prs[0].state == "merged"
        assert rec.prs[0].branch == "feature/add-feature-aaaa"
        assert rec.prs[1].state == "open"
        assert rec.prs[1].branch == "feature/second-feature-aaaa"
        assert rec.active_pr().branch == "feature/second-feature-aaaa"
        # Fresh base_sha = current origin/master, not the first PR's stale base.
        assert rec.prs[1].base_sha == _git("rev-parse", "origin/master", cwd=wt_path)

    def test_new_flag_forces_parallel_pr_while_open(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")  # PR #1 open
        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "e.txt").write_text("parallel\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "parallel work", cwd=wt_path)

        r = pr_ops.create_pr(wid, config, title="Parallel feature", new=True)
        assert r["success"], r
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert len(rec.prs) == 2
        assert {p.state for p in rec.prs} == {"open"}
        assert rec.prs[1].branch == "feature/parallel-feature-aaaa"

    def test_create_pr_records_target_repo(self, pr_repo):
        config, wid, _wt, _ = pr_repo
        r = pr_ops.create_pr(wid, config, title="Add feature", target_repo="owner/other")
        assert r["success"], r
        assert r["repo"] == "owner/other"
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.prs[0].repo == "owner/other"

    def test_create_pr_defaults_repo_to_remote_slug(self, pr_repo):
        # Default target repo = the remote's owner/name slug (what the provider
        # API needs), not the local project name.
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        expected = git_ops.remote_slug("origin", cwd=str(wt_path))
        assert expected  # the bare-remote path yields a two-part slug
        assert rec.prs[0].repo == expected

    def test_set_pr_selects_by_number_and_stamps_closed_at(self, pr_repo):
        config, wid, _wt, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        pr_ops.set_pr(wid, number=42, state="open")
        res = pr_ops.set_pr(wid, select_number=42, state="merged")
        assert res["success"], res
        assert res["state"] == "merged"
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.prs[0].closed_at  # terminal -> stamped

    def test_set_pr_unknown_selector_errors(self, pr_repo):
        config, wid, _wt, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        res = pr_ops.set_pr(wid, select_number=999, state="merged")
        assert res["success"] is False
        assert "999" in res["error"]

    def test_pr_status_all_lists_history(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        pr_ops.set_pr(wid, number=1, state="merged")
        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "f.txt").write_text("again\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "more", cwd=wt_path)
        pr_ops.create_pr(wid, config, title="Another feature")

        res = pr_ops.pr_status(wid, all_prs=True)
        assert res["pr_count"] == 2
        assert len(res["prs"]) == 2
        # active = the open one
        assert res["state"] == "open"
        assert res["branch"] == "feature/another-feature-aaaa"


class TestWorktreeToDictPRs:
    def _rec(self, prs):
        return tracking.WorktreeRecord(
            worktree_id="wt-001", branch="worktree/wt-001",
            worktree_path="/tmp/wt", repo="ext", machine="m", platform="wsl",
            started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=None, prs=prs,
        )

    def test_no_prs_omits_pr_keys(self):
        from agent_worktrees.__main__ import _worktree_to_dict
        d = _worktree_to_dict(self._rec([]))
        assert "pr" not in d and "prs" not in d and "pr_count" not in d

    def test_prs_exposed_with_active_and_count(self):
        from agent_worktrees.__main__ import _worktree_to_dict
        from agent_worktrees.tracking import PRRecord
        rec = self._rec([
            PRRecord(state="merged", branch="a", number=1),
            PRRecord(state="open", branch="b", number=2),
        ])
        d = _worktree_to_dict(rec)
        assert d["pr_count"] == 2
        assert d["pr"]["number"] == 2  # active = the open one
        assert [p["number"] for p in d["prs"]] == [1, 2]


class TestWorktreeToDictClaimsSummary:
    def _rec(self, *, prs=None, resources=None):
        return tracking.WorktreeRecord(
            worktree_id="wt-003", branch="worktree/wt-003",
            worktree_path="/tmp/wt3", repo="acme/sample", machine="m",
            platform="wsl", started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
            status="active", completed_at=None, sessions=None,
            prs=prs or [], resources=resources or [],
        )

    def test_no_claims_or_prs_omits_claims_summary(self):
        from agent_worktrees.__main__ import _worktree_to_dict
        d = _worktree_to_dict(self._rec())
        assert "claims_summary" not in d

    def test_ledger_claim_surfaces_in_summary(self):
        from agent_worktrees.__main__ import _worktree_to_dict
        rec = self._rec(resources=[
            tracking.ResourceClaim(kind="codespace", ref="cs-123", state="active"),
        ])
        d = _worktree_to_dict(rec)
        assert d["claims_summary"] == "CS cs-123"

    def test_backfills_pr_claim_from_active_pr_when_ledger_has_none(self):
        """A worktree whose PR predates the create-pr-time auto-claim (no
        matching 'pr' kind in resources) still surfaces it -- no migration
        needed."""
        from agent_worktrees.__main__ import _worktree_to_dict
        from agent_worktrees.tracking import PRRecord
        rec = self._rec(prs=[PRRecord(state="open", branch="b", number=42)])
        d = _worktree_to_dict(rec)
        assert d["claims_summary"] == "#42"

    def test_ledger_pr_claim_takes_precedence_over_backfill(self):
        """A ledger already carrying a live 'pr' claim (the normal,
        post-auto-claim path) is used as-is -- no duplicate/second entry."""
        from agent_worktrees.__main__ import _worktree_to_dict
        from agent_worktrees.tracking import PRRecord
        rec = self._rec(
            prs=[PRRecord(state="open", branch="b", number=42)],
            resources=[tracking.ResourceClaim(
                kind="pr", ref="acme/sample#42", state="active",
            )],
        )
        d = _worktree_to_dict(rec)
        assert d["claims_summary"] == "#42"

    def test_no_backfill_for_merged_pr(self):
        """A merged/closed PR is never backfilled -- summarize_claims would
        filter it as non-live anyway (live_only=True default), so
        backfilling one is pointless; confirms it stays absent."""
        from agent_worktrees.__main__ import _worktree_to_dict
        from agent_worktrees.tracking import PRRecord
        rec = self._rec(prs=[PRRecord(state="merged", branch="b", number=7)])
        d = _worktree_to_dict(rec)
        assert "claims_summary" not in d

    def test_pr_rank_beats_lower_priority_claim(self):
        """PR outranks a codespace claim per the shared pecking order --
        confirms the Worktrees pivot picks up the SAME ranking every
        claims-showing pivot uses, not an invented one."""
        from agent_worktrees.__main__ import _worktree_to_dict
        rec = self._rec(resources=[
            tracking.ResourceClaim(kind="codespace", ref="cs-1", state="active"),
            tracking.ResourceClaim(kind="pr", ref="acme/sample#9", state="active"),
        ])
        d = _worktree_to_dict(rec)
        assert d["claims_summary"] == "#9 \u00b7 CS cs-1"

    def test_claims_links_pairs_label_with_url_own_repo_aware(self):
        """#3307 follow-up: claims_links carries the same ranked claims as
        claims_summary, each with a resolvable URL where available --
        own_repo is threaded from rec.repo, so a same-repo PR never asserts
        cross-repo even though the ref happens to carry a repo segment."""
        from agent_worktrees.__main__ import _worktree_to_dict
        rec = self._rec(resources=[
            tracking.ResourceClaim(kind="pr", ref="acme/sample#9", state="active"),
        ])
        d = _worktree_to_dict(rec)
        assert d["claims_links"] == [
            {"label": "#9", "url": "https://github.com/acme/sample/pull/9"},
        ]


class TestWorktreeToDictState:
    def _rec(self):
        return tracking.WorktreeRecord(
            worktree_id="wt-002", branch="worktree/wt-002",
            worktree_path="/tmp/wt2", repo="ext", machine="m", platform="wsl",
            started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=None, prs=[],
        )

    def test_no_state_info_omits_state_keys(self):
        from agent_worktrees.__main__ import _worktree_to_dict
        d = _worktree_to_dict(self._rec())
        for k in ("state", "ahead", "behind", "dirty"):
            assert k not in d

    def test_state_info_exposes_canonical_state(self):
        from agent_worktrees.__main__ import _worktree_to_dict
        from agent_worktrees.git_ops import WorktreeState, WorktreeStateInfo
        info = WorktreeStateInfo(
            state=WorktreeState.WIP, ahead=3, behind=5, dirty=0,
        )
        d = _worktree_to_dict(self._rec(), state_info=info)
        assert d["state"] == "wip"   # the canonical enum value the picker maps
        assert d["ahead"] == 3
        assert d["behind"] == 5
        assert d["dirty"] == 0


class TestClassifyRecordsConvo:
    """list --json --classify must report the same CONVO state the tmux status
    bar shows: a clean, commit-less worktree whose session held turns."""

    def _wire(self, monkeypatch, *, raw_state):
        from agent_worktrees import __main__ as m
        from agent_worktrees import git_ops
        monkeypatch.setattr(
            m.cfg, "load_config",
            lambda: types.SimpleNamespace(
                default_repo=types.SimpleNamespace(
                    remote="origin", default_branch="master",
                ),
            ),
        )
        monkeypatch.setattr(m, "_build_active_paths", lambda *a, **k: set())
        monkeypatch.setattr(
            m.git_ops, "classify_worktree",
            lambda *a, **k: git_ops.WorktreeStateInfo(state=raw_state),
        )
        monkeypatch.setattr(m, "_apply_tracking_override", lambda r, i: i)
        return m

    def _rec(self, path):
        return tracking.WorktreeRecord(
            worktree_id="wt-003", branch="worktree/wt-003",
            worktree_path=str(path), repo="ext", machine="m", platform="wsl",
            started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=None, prs=[],
        )

    def test_unused_with_turns_classifies_convo(self, monkeypatch, tmp_path):
        from agent_worktrees import sessions
        m = self._wire(monkeypatch, raw_state=git_ops.WorktreeState.UNUSED)
        rec = self._rec(tmp_path)
        ctx = sessions.SessionContext()
        ctx.turn_count[sessions._normalize_path(str(tmp_path))] = 5
        out = m._classify_records([rec], ctx)
        assert out["wt-003"].state == git_ops.WorktreeState.CONVO

    def test_unused_without_turns_stays_unused(self, monkeypatch, tmp_path):
        from agent_worktrees import sessions
        m = self._wire(monkeypatch, raw_state=git_ops.WorktreeState.UNUSED)
        rec = self._rec(tmp_path)
        out = m._classify_records([rec], sessions.SessionContext())
        assert out["wt-003"].state == git_ops.WorktreeState.UNUSED

    def test_non_unused_unaffected_by_turns(self, monkeypatch, tmp_path):
        from agent_worktrees import sessions
        m = self._wire(monkeypatch, raw_state=git_ops.WorktreeState.WIP)
        rec = self._rec(tmp_path)
        ctx = sessions.SessionContext()
        ctx.turn_count[sessions._normalize_path(str(tmp_path))] = 9
        out = m._classify_records([rec], ctx)
        assert out["wt-003"].state == git_ops.WorktreeState.WIP


class TestPRTrackingUpdates(PRWorkflowSetup):
    def test_record_pushed_head_uses_the_locked_push_identity(
        self, pr_repo, monkeypatch,
    ):
        """A remote repointed after push must not change the recorded fork repo."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, fork_dir, _branch = self._fork_headed_rerun(pr_repo)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        expected = pr_publish.push_slug("fork", cwd=str(wt_path))
        other = fork_dir.parent / "elsewhere.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        _git("remote", "set-url", "fork", str(other), cwd=wt_path)
        monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a, **k: "")
        monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a, **k: "")

        pr_publish.record_pushed_head(
            config, rec, wid, rec.pr, "abc123", remote="fork", head_repo=expected,
        )

        saved = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert (saved.pr.remote, saved.pr.head_repo) == ("fork", expected)

    def test_a_stale_writer_never_erases_the_recorded_fork(self, pr_repo):
        """A process that loaded the record before create-pr recorded the PR's
        fork saves an unrelated change later: the fork stays recorded."""
        config, wid, wt_path, _fork_dir, _branch = self._fork_headed_rerun(pr_repo)
        path = cfg.tracking_dir() / f"{wid}.yaml"
        stale = tracking.load_record(path)
        stale.pr.remote = stale.pr.head_repo = ""
        fresh = tracking.load_record(path)
        fresh.pr.remote, fresh.pr.head_repo = "fork", "alice/ext"
        tracking.save_record(fresh)
        stale.title = "an unrelated update"
        tracking.save_record(stale)
        rec = tracking.load_record(path)
        assert (rec.pr.remote, rec.pr.head_repo, rec.title) == ("fork", "alice/ext", "an unrelated update")

    def test_a_stale_writer_never_erases_a_backfilled_fork_identity(self, pr_repo):
        """A legacy record (fork recorded, no host identity yet) loaded by one process;
        another push backfills the identity; the first saves an unrelated change: the
        identity stays."""
        config, wid, wt_path, _fork_dir, _branch = self._fork_headed_rerun(pr_repo)
        path = cfg.tracking_dir() / f"{wid}.yaml"
        legacy = tracking.load_record(path)
        legacy.pr.remote, legacy.pr.head_repo, legacy.pr.head_identity = "fork", "alice/ext", ""
        tracking.save_record(legacy)
        stale = tracking.load_record(path)
        fresh = tracking.load_record(path)
        fresh.pr.head_identity = "github.com/alice/ext"
        tracking.save_record(fresh)
        stale.title = "an unrelated update"
        tracking.save_record(stale)
        rec = tracking.load_record(path)
        assert (rec.pr.remote, rec.pr.head_repo, rec.pr.head_identity) == ("fork", "alice/ext", "github.com/alice/ext")

    def test_set_pr_reassigning_a_record_clears_its_old_fork_target(self, pr_repo):
        """Correcting a tracked entry to a different PR drops the old PR's fork target;
        attaching the first number keeps it."""
        config, wid, wt_path, _fork_dir, _branch = self._fork_headed_rerun(pr_repo)
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.number, rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner = None, "fork", "alice/ext", "alice"
        original_tip = (rec.pr.base_sha, rec.pr.head_sha, rec.pr.patch_id)
        tracking.save_record(rec)
        pr_ops.set_pr(wid, number=7, config=config)  # first number: still that PR
        rec = tracking.load_record(path)
        assert (rec.pr.number, rec.pr.remote, rec.pr.head_owner) == (7, "fork", "alice")
        assert (rec.pr.base_sha, rec.pr.head_sha, rec.pr.patch_id) == original_tip
        pr_ops.set_pr(wid, number=7, config=config)
        rec = tracking.load_record(path)
        assert (rec.pr.base_sha, rec.pr.head_sha, rec.pr.patch_id) == original_tip
        # A different PR: set_pr saves over the on-disk record that still names the old fork;
        # the save's field merge must not put it back.
        pr_ops.set_pr(wid, number=99, config=config)
        rec = tracking.load_record(path)
        assert (rec.pr.number, rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner) == (99, "", "", "")

    @pytest.mark.parametrize("identity", ["number", "repo", "provider"])
    @pytest.mark.parametrize("competing_revision_increment", [0, 1])
    def test_set_pr_reassignment_discards_previous_tip_and_lifecycle(
        self, pr_repo: tuple[cfg.Config, str, Path, Path], identity: str,
        competing_revision_increment: int,
    ) -> None:
        config, wid, _wt_path, _fork_dir, _branch = self._fork_headed_rerun(pr_repo)
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.number, rec.pr.repo, rec.pr.provider = 7, "acme/ext", "github"
        rec.pr.base_sha, rec.pr.head_sha, rec.pr.patch_id = "old-base", "old-head", "old-patch"
        rec.pr.state, rec.pr.opened_at, rec.pr.closed_at = "merged", "old-open", "old-close"
        rec.pr.pr_revision += 1
        tracking.save_record(rec)
        stale = tracking.load_record(path)
        stale.pr.pr_revision += competing_revision_increment
        kwargs = {
            "number": {"number": 99},
            "repo": {"url": "https://github.com/other-org/other-repo/pull/7", "number": 7},
            "provider": {"provider": "ado"},
        }[identity]
        assert pr_ops.set_pr(wid, config=config, **kwargs)["success"]
        updated = tracking.load_record(path).pr
        assert (updated.base_sha, updated.head_sha, updated.patch_id) == ("", "", "")
        assert updated.state == "open"
        assert updated.opened_at and updated.opened_at != "old-open"
        assert updated.closed_at == ""
        stale.title = "unrelated stale update"
        tracking.save_record(stale)
        updated = tracking.load_record(path).pr
        assert (updated.base_sha, updated.head_sha, updated.patch_id) == ("", "", "")
        assert updated.state == "open"
        assert updated.opened_at and updated.opened_at != "old-open"
        assert updated.closed_at == ""

    def test_set_pr_reassigning_a_numberless_record_to_another_repo_clears_its_fork(self, pr_repo):
        """A numberless record moved to a PR in another repository: the on-disk copy still
        has no number, but it's a different PR -- the save must not merge its fork back."""
        config, wid, _wt_path, _fork_dir, _branch = self._fork_headed_rerun(pr_repo)
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.number, rec.pr.repo = None, "acme/ext"
        rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner = "fork", "alice/ext", "alice"
        tracking.save_record(rec)
        pr_ops.set_pr(wid, url="https://github.com/other-org/other-repo/pull/7", number=7, config=config)
        rec = tracking.load_record(path)
        assert (rec.pr.repo, rec.pr.number) == ("other-org/other-repo", 7)
        assert (rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner) == ("", "", "")

    def test_set_pr_correcting_an_established_provider_clears_its_fork(self, pr_repo):
        """Another provider is another PR: its old fork target is dropped (and the
        save's field merge doesn't restore it), while attaching a first provider
        keeps the PR's fork."""
        config, wid, _wt_path, _fork_dir, _branch = self._fork_headed_rerun(pr_repo)
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.provider, rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner = "", "fork", "alice/ext", "alice"
        tracking.save_record(rec)
        pr_ops.set_pr(wid, provider="github", config=config)  # first provider: still that PR
        rec = tracking.load_record(path)
        assert (rec.pr.provider, rec.pr.remote, rec.pr.head_owner) == ("github", "fork", "alice")
        pr_ops.set_pr(wid, provider="ado", config=config)
        rec = tracking.load_record(path)
        assert (rec.pr.provider, rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner) == ("ado", "", "", "")

    def test_a_stale_writer_of_another_provider_never_restores_its_fork(self, pr_repo):
        """The save's field merge only fills fork fields from the on-disk copy while both
        describe the same PR: one under another established provider doesn't."""
        _config, wid, _wt_path, _fork_dir, _branch = self._fork_headed_rerun(pr_repo)
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.provider, rec.pr.head_repo = "github", "alice/ext"
        rec.pr.pr_revision += 1
        tracking.save_record(rec)
        moved = tracking.load_record(path)
        moved.pr.provider = "ado"
        moved.pr.remote = moved.pr.head_repo = moved.pr.head_identity = moved.pr.head_owner = ""
        moved.pr.pr_revision += 1
        tracking.save_record(moved)
        rec = tracking.load_record(path)
        assert (rec.pr.provider, rec.pr.remote, rec.pr.head_repo) == ("ado", "", "")

    @pytest.mark.parametrize("change", [{"provider": "ado"}, {"number": 99}])
    def test_a_stale_writer_saving_after_a_reassignment_never_restores_the_old_pr(self, pr_repo, change):
        """A process that loaded the record before set_pr reassigned it saves an unrelated
        change afterwards: the reassignment (a newer revision) wins, fork target included."""
        config, wid, _wt_path, _fork_dir, _branch = self._fork_headed_rerun(pr_repo)
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.provider, rec.pr.number, rec.pr.head_repo = "github", 7, "alice/ext"
        rec.pr.pr_revision += 1
        tracking.save_record(rec)
        stale = tracking.load_record(path)
        pr_ops.set_pr(wid, config=config, **change)
        stale.title = "an unrelated update"
        tracking.save_record(stale)
        rec = tracking.load_record(path)
        assert (rec.pr.provider, rec.pr.number) == (change.get("provider", "github"), change.get("number", 7))
        assert (rec.pr.remote, rec.pr.head_repo, rec.title) == ("", "", "an unrelated update")

    def test_set_pr_correcting_only_the_repo_slug_case_keeps_the_fork(self, pr_repo):
        """GitHub slugs are case-insensitive: re-entering the same PR's URL in another case
        is the same PR, so its fork target and its head observation stay."""
        config, wid, _wt_path, _fork_dir, _branch = self._fork_headed_rerun(pr_repo)
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.number, rec.pr.repo, rec.pr.head_repo, rec.pr.head_owner = 7, "acme/ext", "alice/ext", "alice"
        rec.pr.head_observed_at = "2026-10-06T00:00:00Z"
        rec.pr.pr_revision += 1
        tracking.save_record(rec)
        pr_ops.set_pr(wid, url="https://github.com/ACME/Ext/pull/7", number=7, config=config)
        rec = tracking.load_record(path)
        assert (rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner) == ("fork", "alice/ext", "alice")
        assert rec.pr.head_observed_at == "2026-10-06T00:00:00Z"
