"""Live PR status, head observations, review threads, and merge-gate notes."""

from __future__ import annotations

import pytest
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking

pytestmark = pytest.mark.contract("agent_worktrees.pr_ops.status")


class TestRefreshHeadObservation:
    def _config(self):
        return cfg.Config(
            srcroot="/s", machine="m", platform="linux", repo_name="ext",
            repos={"ext": cfg.RepoConfig(
                anchor="/a", worktree_root="/w",
                pr=cfg.PRConfig(
                    enabled=True,
                    provider="gitea",
                    api_base="https://gitea.example",
                ),
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
            state="open", number=7, branch="pr/x", provider="gitea", repo="o/r"
        )
        tracking.save_record(rec)
        return rec

    def test_records_matching_provider_clock_observation(self, tmp_path, monkeypatch):
        from agent_worktrees.providers import PullResult
        import agent_worktrees.providers as providers

        rec = self._record(tmp_path, monkeypatch)

        class _Provider:
            def authority_endpoint(self, api_base=""):
                return api_base.rstrip("/")

            def observe_head(self, repo, number, *, api_base="", token=None):
                return PullResult(
                    number=number,
                    head_sha="abc",
                    observed_at="2026-09-05T06:01:02+00:00",
                )

        monkeypatch.setattr(providers, "get_provider", lambda name: _Provider())
        monkeypatch.setattr(
            providers, "account_token_for_slug", lambda slug, prcfg: "tok"
        )

        assert pr_ops.refresh_head_observation(
            self._config(), rec, rec.active_pr(), "abc"
        ) == ""
        persisted = tracking.load_record(rec.yaml_path).active_pr()
        assert persisted.head_sha == "abc"
        assert persisted.head_observed_at == "2026-09-05T06:01:02+00:00"
        assert persisted.head_observed_api_base == "https://gitea.example"

    def test_mismatched_observation_fails_closed(self, tmp_path, monkeypatch):
        from agent_worktrees.providers import PullResult
        import agent_worktrees.providers as providers

        rec = self._record(tmp_path, monkeypatch)
        rec.active_pr().head_observed_at = "old-evidence"

        class _Provider:
            def authority_endpoint(self, api_base=""):
                return api_base.rstrip("/")

            def observe_head(self, repo, number, *, api_base="", token=None):
                return PullResult(
                    number=number,
                    head_sha="different",
                    observed_at="2026-09-05T06:01:02+00:00",
                )

        monkeypatch.setattr(providers, "get_provider", lambda name: _Provider())
        monkeypatch.setattr(
            providers, "account_token_for_slug", lambda slug, prcfg: "tok"
        )

        error = pr_ops.refresh_head_observation(
            self._config(), rec, rec.active_pr(), "abc"
        )
        assert "instead of pushed head abc" in error
        persisted = tracking.load_record(rec.yaml_path).active_pr()
        assert persisted.head_sha == "abc"
        assert persisted.head_observed_at == ""
        assert persisted.head_observed_api_base == ""

    def test_concurrent_reassociation_rejects_returned_observation(
        self, tmp_path, monkeypatch
    ):
        from agent_worktrees.providers import PullResult
        import agent_worktrees.providers as providers

        rec = self._record(tmp_path, monkeypatch)

        class _Provider:
            def authority_endpoint(self, api_base=""):
                return api_base.rstrip("/")

            def observe_head(self, repo, number, *, api_base="", token=None):
                current = tracking.load_record(rec.yaml_path)
                current.active_pr().number = 8
                tracking.save_record(current)
                return PullResult(
                    number=number,
                    head_sha="abc",
                    observed_at="2026-09-05T06:01:02+00:00",
                )

        monkeypatch.setattr(providers, "get_provider", lambda name: _Provider())
        monkeypatch.setattr(
            providers, "account_token_for_slug", lambda slug, prcfg: "tok"
        )

        error = pr_ops.refresh_head_observation(
            self._config(), rec, rec.active_pr(), "abc"
        )

        assert "changed during provider observation" in error
        persisted = tracking.load_record(rec.yaml_path).active_pr()
        assert persisted.number == 8
        assert persisted.head_observed_at == ""
        assert persisted.head_observed_api_base == ""


class TestSelfMergeBypassNote:
    def _flow(self, *, merge_actor="submitter-direct"):
        from agent_worktrees import pr_contract as pc
        return pc.classify_pr_flow(
            enabled=True, required=True, provider="github",
            automerge_label="", merge_actor=merge_actor,
        )

    class _GhLikeProvider:
        def __init__(self, gate_result):
            self._gate_result = gate_result
            self.calls = []

        def pull_review_gate(self, repo, number, *, api_base="", token=None):
            self.calls.append((repo, number))
            return self._gate_result

    def test_none_when_not_self_merge_profile(self):
        provider = self._GhLikeProvider((True, True))
        flow = self._flow(merge_actor="")  # not pr-self-merge
        note = pr_ops.self_merge_bypass_note(flow, provider, "o/r", 7)
        assert note is None
        assert provider.calls == []  # never even consulted

    def test_none_when_no_pr_number_yet(self):
        provider = self._GhLikeProvider((True, True))
        note = pr_ops.self_merge_bypass_note(self._flow(), provider, "o/r", None)
        assert note is None
        assert provider.calls == []

    def test_none_when_provider_lacks_pull_review_gate(self):
        class _NoGate:
            pass

        note = pr_ops.self_merge_bypass_note(self._flow(), _NoGate(), "o/r", 7)
        assert note is None

    def test_note_when_review_required_and_bypassable(self):
        provider = self._GhLikeProvider((True, True))
        note = pr_ops.self_merge_bypass_note(self._flow(), provider, "o/r", 7)
        assert note is not None
        assert "Maintainer bypass" in note
        assert "pr-merge" in note
        assert provider.calls == [("o/r", 7)]

    def test_none_when_review_required_but_not_bypassable(self):
        provider = self._GhLikeProvider((True, False))
        note = pr_ops.self_merge_bypass_note(self._flow(), provider, "o/r", 7)
        assert note is None

    def test_none_when_no_review_required(self):
        provider = self._GhLikeProvider((False, None))
        note = pr_ops.self_merge_bypass_note(self._flow(), provider, "o/r", 7)
        assert note is None

    def test_none_when_bypassability_unknown(self):
        provider = self._GhLikeProvider((True, None))
        note = pr_ops.self_merge_bypass_note(self._flow(), provider, "o/r", 7)
        assert note is None

    def test_none_when_gate_read_raises(self):
        class _Boom:
            def pull_review_gate(self, repo, number, **kw):
                raise RuntimeError("gh api failed")

        note = pr_ops.self_merge_bypass_note(self._flow(), _Boom(), "o/r", 7)
        assert note is None


class TestPRStatusLive:
    def _config_with_binding(self, base_config, **pr_kwargs):
        """Clone the fixture config with a merge-consent binding on its repo."""
        repo = base_config.default_repo
        defaults = dict(
            enabled=True, provider="gitea", branch_prefix="feature",
            head_scheme="snapshot", auto_open=False,
            api_base="https://h/gitea",
            automerge_label="auto-merge",
            allow_stale_approval=True,
            hold_labels=("do-not-merge", "needs-rebase", "wip"),
            wip_title_prefixes=("wip:",),
        )
        defaults.update(pr_kwargs)
        pr = cfg.PRConfig(**defaults)
        new_repo = cfg.RepoConfig(
            anchor=repo.anchor, worktree_root=repo.worktree_root,
            default_branch=repo.default_branch, remote=repo.remote, pr=pr,
        )
        return cfg.Config(
            srcroot=base_config.srcroot, machine=base_config.machine,
            platform=base_config.platform, repo_name=base_config.repo_name,
            repos={base_config.repo_name: new_repo},
        )

    def _mock_provider(self, monkeypatch, snap):
        from agent_worktrees import providers

        class _Prov:
            name = "gitea"

            def authority_endpoint(self, api_base=""):
                return api_base.rstrip("/")

            def get_snapshot(self, repo, number, *, api_base="", token=None):
                return snap

        monkeypatch.setattr(providers, "get_provider", lambda name: _Prov())
        monkeypatch.setattr(providers, "resolve_token", lambda prcfg: "tok")

    def test_live_block_present_when_provider_ok(self, pr_repo, monkeypatch):
        from agent_worktrees import pr_contract as pc
        config, wid, _wt, _ = pr_repo
        config = self._config_with_binding(config)
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")
        snap = pc.PRSnapshot(
            pr_state="open", merged=False, head_sha="h", base_ref="master",
            author="alice", mergeable=True, title="Feature",
            reviews=(pc.Review(1, "APPROVED", "bob", commit_id="h"),),
        )
        self._mock_provider(monkeypatch, snap)
        res = pr_ops.pr_status(wid, config=config)
        assert "live" in res
        assert res["live"]["verdict"] == "APPROVED"
        assert res["live"]["merge_state"] == "clean"
        assert res["live"]["eligible"] is True
        assert res["live"]["reviews"] == 1
        assert res["live"]["occupancy"] == "needs-consent"

    def test_live_verdict_reports_comment_when_review_blocking_false(
        self, pr_repo, monkeypatch
    ):
        """A repo config'd review_blocking=False (e.g. Copilot code review on
        a pr-self-merge repo, which can only COMMENT) reports that comment as
        the "COMMENTED" verdict instead of no verdict at all."""
        from agent_worktrees import pr_contract as pc
        config, wid, _wt, _ = pr_repo
        config = self._config_with_binding(config, review_blocking=False)
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")
        snap = pc.PRSnapshot(
            pr_state="open", merged=False, head_sha="h", base_ref="master",
            author="alice", mergeable=True, title="Feature",
            reviews=(pc.Review(1, "COMMENT", "bob"),),
        )
        self._mock_provider(monkeypatch, snap)
        res = pr_ops.pr_status(wid, config=config)
        assert res["live"]["verdict"] == "COMMENTED"

    def test_cli_evidence_lookup_uses_configured_provider_for_manual_pr(
        self, pr_repo, monkeypatch
    ):
        from agent_worktrees import __main__ as main
        from agent_worktrees import providers

        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        tracked = rec.active_pr()
        tracked.head_sha = "head"
        tracked.head_observed_at = "2026-01-01T00:01:00Z"
        tracked.head_observed_api_base = "https://h/gitea"
        tracking.save_record(rec)

        class _Prov:
            def authority_endpoint(self, api_base=""):
                return api_base.rstrip("/")

        monkeypatch.setattr(main, "_infer_worktree_id_from_cwd", lambda config: wid)
        monkeypatch.setattr(providers, "get_provider", lambda name: _Prov())

        assert main._tracked_pr_head_evidence(
            config,
            rec.repo,
            7,
            "gitea",
            "https://h/gitea",
        ) == ("head", "2026-01-01T00:01:00Z")

    def test_tracked_pr_pushed_head_ignores_confirmation_state(
        self, pr_repo, monkeypatch
    ):
        """``_tracked_pr_pushed_head`` answers "what did we just push?" --
        unlike ``_tracked_pr_head_evidence``, it must return the recorded
        head_sha even when the provider never independently confirmed it
        (``head_observed_at``/``head_observed_api_base`` still blank). This is
        the exact state a push leaves behind when the provider's PR object is
        stuck stale (ThomasMichon/copilot-extensions#4949) -- the one case
        where a pre-merge safety check (``--match-head-commit``) matters most.
        """
        from agent_worktrees import __main__ as main

        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        tracked = rec.active_pr()
        tracked.head_sha = "just-pushed-sha"
        tracked.head_observed_at = ""
        tracked.head_observed_api_base = ""
        tracking.save_record(rec)

        monkeypatch.setattr(main, "_infer_worktree_id_from_cwd", lambda config: wid)

        assert main._tracked_pr_pushed_head(
            config, rec.repo, 7, "gitea",
        ) == "just-pushed-sha"

    def test_tracked_pr_pushed_head_returns_empty_with_no_tracked_record(
        self, pr_repo, monkeypatch
    ):
        from agent_worktrees import __main__ as main

        config, _wid, _wt, _ = pr_repo
        monkeypatch.setattr(main, "_infer_worktree_id_from_cwd", lambda config: None)

        assert main._tracked_pr_pushed_head(config, "o/r", 7, "github") == ""

    def test_tracked_pr_pushed_head_matches_repo_case_insensitively(
        self, pr_repo, monkeypatch
    ):
        """GitHub (and most other provider) repo slugs are case-insensitive,
        so an explicit ``Owner/Repo`` operand must still match a tracked
        ``owner/repo`` record -- an exact string comparison would silently
        omit the stale-head safeguard for the very PR it's meant to protect
        (ThomasMichon/copilot-extensions#5034)."""
        from agent_worktrees import __main__ as main

        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.active_pr().head_sha = "just-pushed-sha"
        tracking.save_record(rec)

        monkeypatch.setattr(main, "_infer_worktree_id_from_cwd", lambda config: wid)

        # rec.repo is lower-case ("o/r"); query with a differently-cased slug.
        queried_repo = rec.repo.upper()
        assert queried_repo != rec.repo
        assert main._tracked_pr_pushed_head(
            config, queried_repo, 7, "gitea",
        ) == "just-pushed-sha"

    def test_tracked_pr_pushed_head_falls_back_to_scanning_when_no_cwd_worktree(
        self, pr_repo, monkeypatch
    ):
        """Mirrors a supported `--project <name> pr-merge <repo> <n> --now`
        invocation from a neutral CWD: the process moves to the project's
        anchor (never a tracked worktree), so CWD-based inference legitimately
        finds nothing even though the project's tracking directory holds the
        matching record under a different worktree's YAML file."""
        from agent_worktrees import __main__ as main

        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.active_pr().head_sha = "just-pushed-sha"
        tracking.save_record(rec)

        monkeypatch.setattr(main, "_infer_worktree_id_from_cwd", lambda config: None)

        assert main._tracked_pr_pushed_head(
            config, rec.repo, 7, "gitea",
        ) == "just-pushed-sha"

    def test_adopt_pushed_head_takes_only_the_worktrees_own_head(self, pr_repo):
        """A plain `git push` of the worktree's HEAD leaves the record a head
        behind, so `pr-merge` refused with "Head branch was modified". The
        provider's head is adopted (and recorded) only when it is that HEAD:
        a head someone else pushed is never taken."""
        from agent_worktrees import pr_cli

        config, wid, wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.active_pr().head_sha = "recorded-before-a-manual-push"
        tracking.save_record(rec)
        head = git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip()

        assert pr_cli._adopt_pushed_head(config, rec.repo, 7, "gitea", "f" * 40) == ""
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.active_pr().head_sha == "recorded-before-a-manual-push"

        rec.active_pr().head_observed_api_base = "https://stale.example"
        tracking.save_record(rec)
        assert pr_cli._adopt_pushed_head(config, rec.repo, 7, "gitea", head) == head
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.active_pr().head_sha == head
        # The whole provider-observation tuple goes with the old head.
        assert (rec.active_pr().head_observed_at, rec.active_pr().head_observed_api_base) == ("", "")

    @pytest.mark.parametrize("vanishes", ["record", "worktree"])
    def test_adopt_pushed_head_keeps_the_expectation_when_local_state_vanishes(
            self, pr_repo, monkeypatch, vanishes):
        """The record or the worktree disappears mid-call: nothing raises, so
        pr-merge still goes ahead against the recorded head."""
        from agent_worktrees import pr_cli

        config, wid, wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open")
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.active_pr().head_sha = "recorded-before-a-manual-push"
        tracking.save_record(rec)
        head = git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip()
        real_git = git_ops.git

        def git(*args, **kw):
            if vanishes == "record":
                path.unlink()
                return real_git(*args, **kw)
            raise OSError(2, "No such file or directory")

        monkeypatch.setattr(git_ops, "git", git)
        assert pr_cli._adopt_pushed_head(config, rec.repo, 7, "gitea", head) == ""

    def test_adopt_pushed_head_rereads_the_record_under_its_lock(self, pr_repo, monkeypatch):
        """Another process updates the record between the scan and the write:
        the write is a fresh read-modify-write that sees it, never a stale save."""
        from agent_worktrees import pr_cli

        config, wid, wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open")
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.active_pr().head_sha = "recorded-before-a-manual-push"
        tracking.save_record(rec)
        head = git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip()
        real_git = git_ops.git

        def git(*args, **kw):  # meanwhile, another process retitles the worktree
            other = tracking.load_record(path)
            other.title = "changed elsewhere"
            tracking.save_record(other)
            return real_git(*args, **kw)

        monkeypatch.setattr(git_ops, "git", git)
        assert pr_cli._adopt_pushed_head(config, rec.repo, 7, "gitea", head) == head
        rec = tracking.load_record(path)
        assert rec.title == "changed elsewhere" and rec.active_pr().head_sha == head

    def test_tracked_pr_pushed_head_falls_back_when_cwd_worktree_is_wrong_project(
        self, pr_repo, monkeypatch
    ):
        """Mirrors a cross-project `--config <other>` invocation run from
        INSIDE a different project's own worktree: CWD inference returns that
        ambient worktree's id, which naturally has no record under the
        explicitly supplied project's tracking directory -- this must still
        fall through to the scan rather than returning '' outright."""
        from agent_worktrees import __main__ as main

        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.active_pr().head_sha = "just-pushed-sha"
        tracking.save_record(rec)

        monkeypatch.setattr(
            main, "_infer_worktree_id_from_cwd",
            lambda config: "some-other-projects-worktree-id",
        )

        assert main._tracked_pr_pushed_head(
            config, rec.repo, 7, "gitea",
        ) == "just-pushed-sha"

    def test_tracked_pr_pushed_head_scan_fallback_refuses_ambiguous_match(
        self, pr_repo, monkeypatch
    ):
        """Two tracked records claiming the same (repo, number, provider) is
        a genuinely ambiguous state -- "no evidence" is the honest answer,
        never a guess."""
        from agent_worktrees import __main__ as main

        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.active_pr().head_sha = "sha-one"
        tracking.save_record(rec)

        other_id = "test-wt-20260618-bbbb"
        import copy
        other = copy.deepcopy(rec)
        other.worktree_id = other_id
        other.prs = [
            tracking.PRRecord(
                repo=rec.repo, number=7, provider="gitea", head_sha="sha-two",
            )
        ]
        tracking.save_record(other, cfg.tracking_dir() / f"{other_id}.yaml")

        monkeypatch.setattr(main, "_infer_worktree_id_from_cwd", lambda config: None)

        assert main._tracked_pr_pushed_head(config, rec.repo, 7, "gitea") == ""

    def test_tracked_pr_pushed_head_refuses_ambiguous_even_when_cwd_matches(
        self, pr_repo, monkeypatch
    ):
        """A CWD-derived record match must not short-circuit the ambiguity
        check: if another tracked record claims the same (repo, number,
        provider) with a *different* head_sha, that is still genuinely
        ambiguous and must not resolve to the CWD record's (possibly stale)
        value (ThomasMichon/copilot-extensions#5034)."""
        from agent_worktrees import __main__ as main

        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.active_pr().head_sha = "stale-sha"
        tracking.save_record(rec)

        other_id = "test-wt-20260618-cccc"
        import copy
        other = copy.deepcopy(rec)
        other.worktree_id = other_id
        other.prs = [
            tracking.PRRecord(
                repo=rec.repo, number=7, provider="gitea", head_sha="fresh-sha",
            )
        ]
        tracking.save_record(other, cfg.tracking_dir() / f"{other_id}.yaml")

        # CWD inference points squarely at the stale record.
        monkeypatch.setattr(main, "_infer_worktree_id_from_cwd", lambda config: wid)

        assert main._tracked_pr_pushed_head(config, rec.repo, 7, "gitea") == ""

    def test_live_block_rejects_observation_from_other_endpoint(
        self, pr_repo, monkeypatch
    ):
        from agent_worktrees import pr_contract as pc

        config, wid, _wt, _ = pr_repo
        config = self._config_with_binding(config)
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.active_pr().head_sha = "new"
        rec.active_pr().head_observed_at = "2026-01-01T00:01:00Z"
        rec.active_pr().head_observed_api_base = "https://other/gitea"
        tracking.save_record(rec)
        snap = pc.PRSnapshot(
            pr_state="open", merged=False, head_sha="new", base_ref="master",
            author="alice", mergeable=True, title="Feature",
            reviews=(
                pc.Review(
                    1,
                    "APPROVED",
                    "bob",
                    submitted_at="2026-01-01T00:02:00Z",
                    commit_id="old",
                ),
            ),
        )
        self._mock_provider(monkeypatch, snap)

        live = pr_ops.pr_status(wid, config=config)["live"]

        assert live["approval_stale"] is True
        assert live["approval_stale_authorized"] is False
        assert live["verdict"] == ""

    def test_live_disabled_skips_provider(self, pr_repo, monkeypatch):
        config, wid, _wt, _ = pr_repo
        config = self._config_with_binding(config)
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")

        monkeypatch.setattr(
            pr_ops,
            "_reconcile_active_pr",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("reconciliation must not run when live=False")
            ),
        )
        res = pr_ops.pr_status(wid, live=False, config=config)
        assert "live" not in res

    def test_live_enabled_still_reconciles(self, pr_repo, monkeypatch):
        config, wid, _wt, _ = pr_repo
        config = self._config_with_binding(config)
        pr_ops.set_pr(
            wid,
            url="https://example/pulls/unresolved",
            state="open",
            provider="gitea",
        )
        calls = []
        monkeypatch.setattr(
            pr_ops,
            "_reconcile_active_pr",
            lambda record, loaded: calls.append(loaded),
        )

        pr_ops.pr_status(wid, live=True, config=config)

        assert calls == [config]

    def test_live_best_effort_on_provider_error(self, pr_repo, monkeypatch):
        config, wid, _wt, _ = pr_repo
        config = self._config_with_binding(config)
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")
        from agent_worktrees import providers
        from agent_worktrees.providers import ProviderError

        class _Prov:
            name = "gitea"

            def get_snapshot(self, repo, number, *, api_base="", token=None):
                raise ProviderError("unreachable", transient=True)

        monkeypatch.setattr(providers, "get_provider", lambda name: _Prov())
        monkeypatch.setattr(providers, "resolve_token", lambda prcfg: "tok")
        res = pr_ops.pr_status(wid, config=config)
        # tracked metadata still present; the live block is simply omitted
        assert res["has_pr"] is True
        assert "live" not in res

    def test_live_omitted_when_no_number(self, pr_repo, monkeypatch):
        config, wid, _wt, _ = pr_repo
        config = self._config_with_binding(config)
        pr_ops.set_pr(wid, url="https://h/pulls/x", provider="gitea")  # no number
        from agent_worktrees import providers
        monkeypatch.setattr(
            providers, "get_provider",
            lambda name: (_ for _ in ()).throw(AssertionError("should not fetch")),
        )
        res = pr_ops.pr_status(wid, config=config)
        assert "live" not in res

    def test_live_self_merge_note_surfaced_when_bypassable(self, pr_repo, monkeypatch):
        # #3296 follow-up: a pr-self-merge repo with a live, actor-bypassable
        # required review must surface that explicitly in the live block --
        # not just a bare eligible=False an agent has to puzzle out.
        from agent_worktrees import pr_contract as pc

        config, wid, _wt, _ = pr_repo
        config = self._config_with_binding(
            config, merge_actor="submitter-direct", automerge_label="",
        )
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")
        snap = pc.PRSnapshot(
            pr_state="open", merged=False, head_sha="h", base_ref="master",
            author="alice", mergeable=True, title="Feature", reviews=(),
        )

        class _GhLikeProv:
            name = "gitea"

            def authority_endpoint(self, api_base=""):
                return api_base.rstrip("/")

            def get_snapshot(self, repo, number, *, api_base="", token=None):
                return snap

            def pull_review_gate(self, repo, number, *, api_base="", token=None):
                return (True, True)

        from agent_worktrees import providers
        monkeypatch.setattr(providers, "get_provider", lambda name: _GhLikeProv())
        monkeypatch.setattr(providers, "resolve_token", lambda prcfg: "tok")

        res = pr_ops.pr_status(wid, config=config)
        assert "Maintainer bypass" in res["live"]["self_merge_note"]

    def test_live_self_merge_note_absent_when_not_bypassable(
        self, pr_repo, monkeypatch,
    ):
        from agent_worktrees import pr_contract as pc

        config, wid, _wt, _ = pr_repo
        config = self._config_with_binding(
            config, merge_actor="submitter-direct", automerge_label="",
        )
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")
        snap = pc.PRSnapshot(
            pr_state="open", merged=False, head_sha="h", base_ref="master",
            author="alice", mergeable=True, title="Feature", reviews=(),
        )

        class _GhLikeProv:
            name = "gitea"

            def authority_endpoint(self, api_base=""):
                return api_base.rstrip("/")

            def get_snapshot(self, repo, number, *, api_base="", token=None):
                return snap

            def pull_review_gate(self, repo, number, *, api_base="", token=None):
                return (True, False)

        from agent_worktrees import providers
        monkeypatch.setattr(providers, "get_provider", lambda name: _GhLikeProv())
        monkeypatch.setattr(providers, "resolve_token", lambda prcfg: "tok")

        res = pr_ops.pr_status(wid, config=config)
        assert "self_merge_note" not in res["live"]

    def test_live_self_merge_note_absent_on_non_self_merge_repo(
        self, pr_repo, monkeypatch,
    ):
        # A gitea repo without submitter-direct merge_actor never surfaces the
        # note even if the (hypothetical) provider had pull_review_gate.
        from agent_worktrees import pr_contract as pc

        config, wid, _wt, _ = pr_repo
        config = self._config_with_binding(config)  # no merge_actor override
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")
        snap = pc.PRSnapshot(
            pr_state="open", merged=False, head_sha="h", base_ref="master",
            author="alice", mergeable=True, title="Feature", reviews=(),
        )

        class _GhLikeProv:
            name = "gitea"

            def authority_endpoint(self, api_base=""):
                return api_base.rstrip("/")

            def get_snapshot(self, repo, number, *, api_base="", token=None):
                return snap

            def pull_review_gate(self, repo, number, *, api_base="", token=None):
                return (True, True)

        from agent_worktrees import providers
        monkeypatch.setattr(providers, "get_provider", lambda name: _GhLikeProv())
        monkeypatch.setattr(providers, "resolve_token", lambda prcfg: "tok")

        res = pr_ops.pr_status(wid, config=config)
        assert "self_merge_note" not in res["live"]


class TestPRThreads:
    def _mock_provider(self, monkeypatch, threads_result, *, resolve_err=""):
        from agent_worktrees import providers

        state = {"resolved": False}

        class _Prov:
            name = "azure-devops"

            def get_comment_threads(self, repo, number, *, api_base="", token=None):
                return threads_result

            def resolve_threads(self, repo, number, *, api_base="", token=None,
                                 thread_ids=()):
                state["resolved"] = True
                return resolve_err

        monkeypatch.setattr(providers, "get_provider", lambda name: _Prov())
        monkeypatch.setattr(providers, "resolve_token", lambda prcfg: "tok")
        return state

    def test_threads_listed(self, pr_repo, monkeypatch):
        from agent_worktrees import pr_contract as pc
        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open", provider="azure-devops")
        tr = pc.ThreadsResult(threads=(
            pc.CommentThread(id=1, status="active",
                             comments=(pc.Comment(author="rev", content="fix this"),)),
            pc.CommentThread(id=2, status="fixed",
                             comments=(pc.Comment(author="rev", content="done"),)),
        ))
        self._mock_provider(monkeypatch, tr)
        res = pr_ops.pr_threads(wid, config=config)
        assert res["has_pr"] is True and res["supported"] is True
        assert res["active_count"] == 1
        assert len(res["threads"]) == 2

    def test_threads_resolve(self, pr_repo, monkeypatch):
        from agent_worktrees import pr_contract as pc
        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open", provider="azure-devops")
        tr = pc.ThreadsResult(threads=(
            pc.CommentThread(id=1, status="active",
                             comments=(pc.Comment(author="r", content="x"),)),
        ))
        prov = self._mock_provider(monkeypatch, tr)
        res = pr_ops.pr_threads(wid, resolve=True, config=config)
        assert res["resolved"] is True
        assert prov["resolved"] is True

    def test_threads_unsupported_degrades(self, pr_repo, monkeypatch):
        from agent_worktrees import pr_contract as pc
        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")
        tr = pc.ThreadsResult(supported=False, error="no token")
        self._mock_provider(monkeypatch, tr)
        res = pr_ops.pr_threads(wid, config=config)
        assert res["supported"] is False and res["threads"] == []

    def test_threads_no_pr(self, pr_repo):
        config, wid, _wt, _ = pr_repo
        res = pr_ops.pr_threads(wid, config=config)
        assert res["has_pr"] is False
