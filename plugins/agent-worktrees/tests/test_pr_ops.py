"""Tests for agent_worktrees.pr_ops -- PR-workflow git operations."""

from __future__ import annotations

import pytest
from pathlib import Path
from pr_test_helpers import _git

from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking

# ---------------------------------------------------------------------------
# create_pr -- role resolution drives pr.roles (not a forced pr.fork.enabled)
#
# The fork-flow tests force ``pr.fork.enabled`` directly on the base PRConfig.
# These instead exercise the actual integration seam create_pr relies on in
# practice: a repo that only sets ``pr.roles`` (fork disabled at the base),
# with the caller's role resolved from a *live* (here, faked) GitHub viewer
# permission via ``_resolve_caller_role`` -> ``providers.actor_viewer_permission``
# -> ``cfg.resolve_role_pr_config``. Closes the one seam
# efforts/active/role-aware-fork-pr-flow's Phase 3 left unit-untested.
# ---------------------------------------------------------------------------

class TestCreatePRRoleResolution:
    def _roles_config(self, config, **role_overrides):
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(
            repo.pr, provider="github", roles=dict(role_overrides),
        )
        return dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)},
        )

    def _fake_permission_provider(self, viewer_permission: str, *, supported=True):
        from types import SimpleNamespace
        from agent_worktrees.providers import PullResult

        class _FakeProvider:
            name = "github"

            def __init__(self):
                self.policy_calls = 0
                self.created_head = "a" * 40
                self.exception_calls = []

            def authority_endpoint(self, api_base=""):
                return "github.com"

            def get_repo_policy(self, repo, *, api_base="", token=None):
                self.policy_calls += 1
                return SimpleNamespace(
                    supported=supported, viewer_permission=viewer_permission,
                )

            def create_pull(self, scope, *, token=None):
                return PullResult(
                    url="https://example/pulls/17",
                    number=17,
                    state="open",
                    head_sha=self.created_head,
                )

            def pull_review_exception(
                self, repo, number, *, expected_head_sha, hold_labels=(),
                wip_title_prefixes=(), api_base="", token=None,
            ):
                self.exception_calls.append((repo, number, expected_head_sha))
                return True, False, ""

        return _FakeProvider()

    def _patch_live_permission(self, monkeypatch, viewer_permission: str, **kw):
        fake = self._fake_permission_provider(viewer_permission, **kw)
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        return fake

    def test_live_write_permission_resolves_to_roles_write_fork(
        self, pr_repo, monkeypatch,
    ):
        """A caller whose live GitHub permission reads 'write' gets the
        `pr.roles.write` override applied -- including its `fork.enabled` --
        purely from the live permission read, with no `pr.fork.enabled` set
        anywhere on the base config."""
        config, wid, wt_path, _remote_dir = pr_repo
        config = self._roles_config(
            config,
            write=cfg.PRRoleOverride(
                merge_actor="", fork=cfg.ForkConfig(enabled=True),
            ),
        )
        self._patch_live_permission(monkeypatch, "write")

        res = pr_ops.create_pr(wid, config, target_repo="acme/ext")

        assert res["success"] is False
        assert res["needs_confirmation"] == "fork_setup"
        assert res["repo"] == "acme/ext"
        # Nothing mutated: the base config never set pr.fork.enabled itself.
        assert not git_ops.has_remote("fork", cwd=str(wt_path))

    def test_live_maintain_permission_keeps_direct_push(
        self, pr_repo, monkeypatch,
    ):
        """A caller whose live permission reads 'maintain' resolves to the
        `pr.roles.maintain` override (fork disabled, direct push unchanged) --
        proceeds without any fork confirmation."""
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._roles_config(
            config,
            write=cfg.PRRoleOverride(
                merge_actor="", fork=cfg.ForkConfig(enabled=True),
            ),
            maintain=cfg.PRRoleOverride(
                merge_actor="submitter-direct",
                fork=cfg.ForkConfig(enabled=False),
            ),
        )
        self._patch_live_permission(monkeypatch, "maintain")

        res = pr_ops.create_pr(wid, config, target_repo="acme/ext")

        assert res["success"] is True, res
        assert "needs_confirmation" not in res
        assert res["remote"] == "origin"

    def test_maintain_auto_open_uses_effective_self_merge_flow(
        self, pr_repo, monkeypatch,
    ):
        """Auto-open must use the already-resolved Maintain flow for its live
        bypass note, not reclassify the conservative base config."""
        import dataclasses

        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._roles_config(
            config,
            maintain=cfg.PRRoleOverride(
                merge_actor="submitter-direct",
            ),
        )
        repo = config.repos["ext"]
        config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(repo.pr, auto_open=True),
                ),
            },
        )
        fake = self._patch_live_permission(monkeypatch, "maintain")

        res = pr_ops.create_pr(wid, config, target_repo="acme/ext")

        assert res["pr_opened"] is True
        assert "Maintainer bypass" in res["self_merge_note"]
        assert fake.policy_calls == 1
        assert fake.exception_calls == [("acme/ext", 17, fake.created_head)]

    def test_live_permission_with_no_matching_role_is_unaffected(
        self, pr_repo, monkeypatch,
    ):
        """A live permission that resolves to a role absent from `pr.roles`
        (e.g. 'read') leaves the base PRConfig untouched -- no fork, no
        confirmation gate -- exactly like an unconfigured repo."""
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._roles_config(
            config,
            write=cfg.PRRoleOverride(
                merge_actor="", fork=cfg.ForkConfig(enabled=True),
            ),
        )
        self._patch_live_permission(monkeypatch, "read")

        res = pr_ops.create_pr(wid, config, target_repo="acme/ext")

        assert res["success"] is True, res
        assert "needs_confirmation" not in res

    def test_unresolvable_live_permission_falls_back_to_base_config(
        self, pr_repo, monkeypatch,
    ):
        """A provider read that can't determine `viewer_permission`
        (`supported=False`) must fail OPEN to the base, non-role-scoped
        config -- never treated as a denial or as any particular role."""
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._roles_config(
            config,
            write=cfg.PRRoleOverride(
                merge_actor="", fork=cfg.ForkConfig(enabled=True),
            ),
        )
        self._patch_live_permission(monkeypatch, "", supported=False)

        res = pr_ops.create_pr(wid, config, target_repo="acme/ext")

        assert res["success"] is True, res
        assert "needs_confirmation" not in res


# ---------------------------------------------------------------------------
# PR-aware finalize + push-changes (#586)
# ---------------------------------------------------------------------------


class TestPRFinalizeAndPush:
    def test_precondition_fails_before_push(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        # Record a pr.branch that was never pushed.
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr = tracking.PRRecord(state="creating", branch="feature/never-pushed-aaaa")
        tracking.save_record(rec)
        import dataclasses
        repo = config.default_repo
        repo = dataclasses.replace(repo, pr=dataclasses.replace(repo.pr, strategy="detach"))
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is False
        assert "not upstream" in err

    def test_precondition_ok_after_create_pr(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        import dataclasses
        repo = config.default_repo
        repo = dataclasses.replace(repo, pr=dataclasses.replace(repo.pr, strategy="detach"))
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is True, err
        assert err is None

    def test_precondition_detects_unpushed(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        # Add a local commit on the feature branch without pushing (create-pr
        # returns HEAD to the base branch (#1804), so check out the feature
        # branch first to add a feedback commit to it).
        _git("checkout", "feature/add-feature-aaaa", cwd=wt_path)
        (wt_path / "c.txt").write_text("more\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "feedback", cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        import dataclasses
        repo = config.default_repo
        repo = dataclasses.replace(repo, pr=dataclasses.replace(repo.pr, strategy="detach"))
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is False
        assert "unpushed" in err

    def test_push_changes_updates_feature_branch(self, pr_repo, capsys):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")

        before = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)
        anchor = Path(config.repos["ext"].anchor)
        _git("checkout", "master", cwd=anchor)
        (anchor / "upstream.txt").write_text("unrelated upstream advance\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "advance upstream", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

        # New feedback commit directly on the feature branch. create-pr returns
        # HEAD to the base branch (#1804), so check out the feature branch to
        # add feedback commits that push-changes then pushes to the PR branch.
        _git("checkout", "feature/add-feature-aaaa", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)
        assert ok is True
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Preserved the published PR tip" in combined
        assert "incremental updates" in combined

        after = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)
        assert after != before  # remote feature branch advanced
        assert _git(
            "merge-base", before, "origin/feature/add-feature-aaaa", cwd=wt_path,
        ) == before

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        local_head = _git("rev-parse", "HEAD", cwd=wt_path)
        assert rec.pr.head_sha == local_head
        assert rec.pr.state == "open"

    def test_push_changes_refuses_divergent_remote_feature_branch(self, pr_repo):
        """push-changes' lease safety (#5298) was only integration-tested
        through create_pr; add parametrized-in-spirit coverage for the
        snapshot scheme too -- a divergent remote head must reject the push,
        not force-overwrite it."""
        from agent_worktrees import finalize as fin
        config, wid, wt_path, remote_dir = pr_repo
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"], first

        other = remote_dir.parent / "other-clone-push-changes"
        _git("clone", str(remote_dir), str(other), cwd=remote_dir.parent)
        _git("config", "user.email", "other@example.com", cwd=other)
        _git("config", "user.name", "Other", cwd=other)
        _git(
            "checkout", "-B", "feature/add-feature-aaaa",
            "origin/feature/add-feature-aaaa", cwd=other,
        )
        (other / "remote.txt").write_text("other actor\n")
        _git("add", "-A", cwd=other)
        _git("commit", "-m", "remote update", cwd=other)
        _git("push", "origin", "feature/add-feature-aaaa", cwd=other)
        remote_head = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=other)

        _git("checkout", "feature/add-feature-aaaa", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)

        assert ok is False
        assert _git(
            "rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path
        ) == remote_head  # not overwritten

    def test_push_changes_is_blocked_by_a_real_client_side_pre_push_hook(self, pr_repo):
        """#3561: push() must not silently disable a repo's own release-guard
        pre-push hook (e.g. this repo's check-changefile-presence.py). Install
        a real, unconditionally-failing pre-push hook -- standing in for any
        such guard -- and confirm push_changes is genuinely blocked by it, the
        same way a raw ``git push`` already was before this fix."""
        import stat

        config, wid, wt_path, _remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")

        # Install a real client-side pre-push hook in the anchor's common git
        # dir (shared by every worktree, including wt_path) that always
        # refuses -- standing in for a real release guard.
        common_dir = Path(_git("rev-parse", "--git-common-dir", cwd=wt_path))
        if not common_dir.is_absolute():
            common_dir = wt_path / common_dir
        hooks_dir = common_dir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path = hooks_dir / "pre-push"
        hook_path.write_text(
            "#!/bin/sh\n"
            "echo 'BLOCKED: missing changefile (simulated release guard)' >&2\n"
            "exit 1\n"
        )
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

        before = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)

        _git("checkout", "feature/add-feature-aaaa", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        from agent_worktrees import finalize as fin
        ok = fin.push_changes(wid, config)
        assert ok is False  # the hook must actually block the push

        after = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)
        assert after == before  # remote feature branch did NOT advance

    def test_push_changes_blocks_a_leaking_recorded_branch(self, pr_repo):
        # pr-attribution-codenames Phase 5 (round 2): create-pr's guard only
        # covers the branch IT chooses -- a leaking name recorded some other
        # way (e.g. `set-pr --branch`) must still be blocked when
        # push-changes later republishes `record.pr.branch` directly.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")

        leaking_branch = f"worktree/{wid}"
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.branch = leaking_branch
        tracking.save_record(rec)

        # push-changes' snapshot-mode "on worktree/<id>" path resolves the
        # branch to (re)snapshot from the active PR's recorded branch --
        # simulate the ordinary post-create-pr state (HEAD back on the
        # worktree branch with a feedback commit) rather than checking out
        # the leaking name itself.
        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)
        assert ok is False
        assert not git_ops.remote_branch_exists(
            "origin", leaking_branch, cwd=str(wt_path)
        )

    def test_push_changes_blocks_recorded_machine_after_rename(self, pr_repo):
        # config.machine (live) vs record.machine (frozen) -- push-changes
        # must check both, exactly like create_pr (review round 3).
        import dataclasses
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.machine == "test"
        rec.pr.branch = "user/test/reused-head"
        tracking.save_record(rec)

        renamed_config = dataclasses.replace(config, machine="new-machine-name")
        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, renamed_config)
        assert ok is False
        assert not git_ops.remote_branch_exists(
            "origin", "user/test/reused-head", cwd=str(wt_path)
        )

    def test_push_changes_refreshes_marker_and_preserves_body(
        self, pr_repo, monkeypatch
    ):
        import dataclasses

        from agent_worktrees import finalize as fin
        from agent_worktrees.providers import attribution

        config, wid, wt_path, _ = pr_repo
        repo = config.repos["ext"]
        config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(repo.pr, source_attribution=True),
                )
            },
        )
        created = pr_ops.create_pr(
            wid, config, title="Add feature", target_repo="example/project",
        )
        assert created["success"], created
        associated = pr_ops.set_pr(
            wid,
            url="https://gitea.example.com/example/project/pulls/42",
            number=42,
            provider="gitea",
            config=config,
        )
        assert associated["success"], associated
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert (record.pr.repo, record.pr.number, record.pr.provider) == (
            "example/project", 42, "gitea",
        )
        assert record.pr.head_sha == created["head_sha"]
        captured: dict[str, str] = {}
        publishes = {"count": 0}

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                captured["marker"] = marker
                publishes["count"] += 1
                return ""

        provider = FakeProvider()
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: captured.setdefault("provider", name) and provider,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda slug, prcfg: None,
        )
        wt_path.joinpath("c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        assert fin.push_changes(wid, config) is True

        fields = attribution.parse_marker(captured["marker"])
        assert fields is not None
        assert captured["provider"] == "gitea"
        assert fields["head"] == _git("rev-parse", "HEAD", cwd=wt_path)
        assert "old-head" not in captured["marker"]
        refreshed = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert refreshed.pr.attribution_head == fields["head"]
        assert pr_ops.refresh_source_attribution(
            wid,
            config,
            refreshed,
            refreshed.pr,
            fields["head"],
        ) == ""
        assert publishes["count"] == 1
        refreshed.pr.provider = "github"
        refreshed.pr.attribution_head = ""
        mismatch = pr_ops.refresh_source_attribution(
            wid,
            config,
            refreshed,
            refreshed.pr,
            fields["head"],
        )
        assert "credentials cannot be resolved safely" in mismatch
        assert publishes["count"] == 1

    def test_refresh_source_attribution_codename_mode_encrypts_raw_fields(
        self, pr_repo, monkeypatch, keyed_identity_payload,
    ):
        """Refresh publishes identity through the cipher, never as plaintext fields."""
        import dataclasses

        from agent_worktrees.providers import attribution

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]
        config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(repo.pr, source_attribution="codename"),
                )
            },
        )
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        record.codename = "harbor-lattice"
        # codename-attribution-by-default: may_publish_codename requires a
        # known codename_source before publishing -- this test is about
        # marker content/format, not provenance, so stamp a safe "built-in".
        record.codename_source = "built-in"
        tracking.save_record(record)
        pr = tracking.PRRecord(state="open", branch="feature/x", provider="gitea",
                       repo="example/project", number=42)
        record.prs = [pr]
        tracking.save_record(record)
        captured: dict[str, str] = {}

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                captured["marker"] = marker
                return ""

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: FakeProvider(),
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda slug, prcfg: None,
        )

        error = pr_ops.refresh_source_attribution(
            wid, config, record, pr, "deadbeef" * 5,
        )

        assert error == ""
        fields = attribution.parse_marker(captured["marker"])
        assert fields["codename"] == "harbor-lattice"
        assert set(fields) == {"codename", "enc"}
        assert keyed_identity_payload["worktree_id"] == wid
        assert keyed_identity_payload["machine"] == record.machine
        assert keyed_identity_payload["head"] == "deadbeef" * 5

    def test_refresh_source_attribution_codename_mode_skips_without_codename(
        self, pr_repo, monkeypatch,
    ):
        """No assigned codename -> skip the refresh publish entirely, never
        downgrade to the raw marker."""
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]
        config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(repo.pr, source_attribution="codename"),
                )
            },
        )
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert record.codename is None
        pr = tracking.PRRecord(state="open", branch="feature/x", provider="gitea",
                       repo="example/project", number=42)
        record.prs = [pr]
        tracking.save_record(record)
        publishes = {"count": 0}

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                publishes["count"] += 1
                return ""

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: FakeProvider(),
        )

        error = pr_ops.refresh_source_attribution(
            wid, config, record, pr, "deadbeef" * 5,
        )

        assert error == ""
        assert publishes["count"] == 0

    def test_refresh_source_attribution_codename_mode_skips_for_malformed_codename(
        self, pr_repo, monkeypatch,
    ):
        """A tampered/corrupted codename must never be interpolated into
        the refreshed marker as-is -- skip publishing instead."""
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]
        config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(repo.pr, source_attribution="codename"),
                )
            },
        )
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        record.codename = "not a handle --> <script>"
        pr = tracking.PRRecord(state="open", branch="feature/x", provider="gitea",
                       repo="example/project", number=42)
        record.prs = [pr]
        tracking.save_record(record)
        publishes = {"count": 0}

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                publishes["count"] += 1
                return ""

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: FakeProvider(),
        )

        error = pr_ops.refresh_source_attribution(
            wid, config, record, pr, "deadbeef" * 5,
        )

        assert error == ""
        assert publishes["count"] == 0

    def test_refresh_source_attribution_codename_mode_provenance_gating(
        self, pr_repo, monkeypatch,
    ):
        """codename-attribution-by-default: the refresh path must apply the
        SAME provenance gating as the initial create-pr publish (round-17
        finding -- prior to this effort only one of the two paths checked
        codename_source at all)."""
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]

        def _config_with(source_attribution_configured: bool):
            return dataclasses.replace(
                config,
                repos={
                    "ext": dataclasses.replace(
                        repo,
                        pr=dataclasses.replace(
                            repo.pr, source_attribution="codename",
                            source_attribution_configured=(
                                source_attribution_configured
                            ),
                        ),
                    )
                },
            )

        _pr_counter = {"n": 0}

        def _refresh(codename_source, *, source_attribution_configured):
            _pr_counter["n"] += 1
            # A distinct SYNTHETIC worktree id per scenario -- each call
            # simulates an INDEPENDENT worktree/PR from scratch. Reusing
            # the same on-disk record across scenarios would let the
            # codename-provenance merge (correctly) preserve an
            # already-known codename_source from an earlier scenario,
            # which is exactly this effort's point but defeats this
            # test's intent to exercise each provenance value in
            # isolation.
            scenario_wid = f"{wid}-scenario-{_pr_counter['n']}"
            tracking.create_new_record(
                scenario_wid, f"worktree/{scenario_wid}", "/tmp/" + scenario_wid,
                "ext", "test", "linux", cfg.tracking_dir(),
            )
            record = tracking.load_record(cfg.tracking_dir() / f"{scenario_wid}.yaml")
            record.codename = "harbor-lattice"
            record.codename_source = codename_source
            pr = tracking.PRRecord(
                state="open", branch=f"feature/x-{_pr_counter['n']}",
                provider="gitea", repo="example/project",
                number=42 + _pr_counter["n"],
            )
            record.prs = [pr]
            tracking.save_record(record)
            publishes = {"count": 0}

            class FakeProvider:
                def publish_source_marker(
                    self, repo, number, marker, *, api_base="", token=None
                ):
                    publishes["count"] += 1
                    return ""

            monkeypatch.setattr(
                "agent_worktrees.providers.get_provider",
                lambda name: FakeProvider(),
            )
            error = pr_ops.refresh_source_attribution(
                scenario_wid, _config_with(source_attribution_configured),
                record, pr,
                "deadbeef" * 5,
            )
            assert error == ""
            return publishes["count"]

        # (a) codename_source "custom" under the IMPLICIT default fails
        # closed -- the legacy-drift scenario (round-8 finding).
        assert _refresh(
            "custom", source_attribution_configured=False,
        ) == 0
        # (b) an EXPLICIT opt-in publishes a KNOWN "custom" record.
        assert _refresh(
            "custom", source_attribution_configured=True,
        ) == 1
        # (b2) that SAME explicit opt-in does NOT publish for a MISSING
        # codename_source (round-32 finding: explicit opt-in narrows the
        # allocation-vs-publish distinction, it does not bypass provenance
        # verification).
        assert _refresh(
            None, source_attribution_configured=True,
        ) == 0
        # (c) codename_source "built-in" publishes under the IMPLICIT
        # default.
        assert _refresh(
            "built-in", source_attribution_configured=False,
        ) == 1
        # (d) an unrecognized stored value fails closed exactly like
        # "custom" would.
        assert _refresh(
            "not-a-real-value", source_attribution_configured=True,
        ) == 0

    def test_refresh_source_attribution_freezes_before_metadata_validation(
        self, pr_repo,
    ):
        # PR #3037 review finding: the freeze-on-first-touch must run
        # BEFORE the number/repo metadata-validation early return too, not
        # only before the live-config guard -- a legacy PR with missing
        # provider metadata (e.g. before `set-pr` has supplied it) must
        # still be frozen on this touch; if `set-pr` supplies the metadata
        # LATER after config has changed, that later touch must not be
        # treated as the true first one.
        config, wid, _wt_path, _ = pr_repo
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        pr = tracking.PRRecord(state="creating", branch="feature/x")
        assert pr.number is None and not pr.repo  # incomplete metadata
        assert pr.attribution_mode == ""
        record.prs = [pr]
        tracking.save_record(record)

        error = pr_ops.refresh_source_attribution(
            wid, config, record, pr, "deadbeef" * 5,
        )
        assert error == "active PR has no provider repo/number"

        reloaded = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        frozen_pr = reloaded.active_pr()
        assert frozen_pr is not None
        # Frozen despite the incomplete metadata -- pr_repo's bare
        # PRConfig() resolves the implicit "codename" default.
        assert frozen_pr.attribution_mode == "codename"

    def test_refresh_source_attribution_legacy_freeze_runs_before_live_config_guard(
        self, pr_repo, monkeypatch,
    ):
        # round-37 finding: the freeze-on-first-touch must run BEFORE any
        # early return that depends on live config -- a legacy PR (no
        # attribution_mode stored) touched while source_attribution is
        # LIVE `False` must be frozen to the false state on THAT touch
        # (not left unmigrated because the function used to return early
        # before ever reaching the freeze), and a LATER touch under a
        # DIFFERENT live config must still honor the ORIGINAL frozen
        # state, not the newer one.
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]

        def _config_with(source_attribution):
            return dataclasses.replace(
                config,
                repos={
                    "ext": dataclasses.replace(
                        repo,
                        pr=dataclasses.replace(
                            repo.pr, source_attribution=source_attribution,
                        ),
                    )
                },
            )

        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        pr = tracking.PRRecord(
            state="open", branch="feature/x", provider="gitea",
            repo="example/project", number=42,
        )
        assert pr.attribution_mode == ""  # genuinely legacy/unset
        record.prs = [pr]
        tracking.save_record(record)
        publishes = {"count": 0}

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                publishes["count"] += 1
                return ""

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: FakeProvider(),
        )

        # First touch under a LIVE False config.
        error = pr_ops.refresh_source_attribution(
            wid, _config_with(False), record, pr, "deadbeef" * 5,
        )
        assert error == ""
        assert publishes["count"] == 0
        reloaded = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        frozen_pr = reloaded.active_pr()
        assert frozen_pr is not None
        assert frozen_pr.attribution_mode == "false"  # frozen, not still ""

        # Second touch (a NEW head, so publish would proceed if the mode
        # allowed it) under a config that has since changed to "codename".
        # The ORIGINAL frozen "false" must still govern -- no marker.
        error2 = pr_ops.refresh_source_attribution(
            wid, _config_with("codename"), reloaded, frozen_pr,
            "cafebabe" * 5,
        )
        assert error2 == ""
        assert publishes["count"] == 0
        assert frozen_pr.attribution_mode == "false"

    def test_frozen_codename_marker_survives_config_flipping_to_false(
        self, pr_repo, monkeypatch,
    ):
        # Validation Plan (round-26/27 findings): the REVERSE direction of
        # the test above -- a PR frozen under "codename" (built-in
        # provenance, so it publishes) must KEEP publishing on later
        # pushes even after the repo's live config changes to "false".
        # attribution_explicit must also stay frozen (False, an implicit
        # decision), never re-derived from the live config.
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]

        def _config_with(source_attribution):
            return dataclasses.replace(
                config,
                repos={
                    "ext": dataclasses.replace(
                        repo,
                        pr=dataclasses.replace(
                            repo.pr, source_attribution=source_attribution,
                        ),
                    )
                },
            )

        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        record.codename = "steady-anchor"
        record.codename_source = "built-in"
        pr = tracking.PRRecord(
            state="open", branch="feature/x", provider="gitea",
            repo="example/project", number=42,
        )
        assert pr.attribution_mode == ""  # genuinely legacy/unset
        record.prs = [pr]
        tracking.save_record(record)
        publishes: list[str] = []

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                publishes.append(marker)
                return ""

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: FakeProvider(),
        )

        # First touch under a LIVE "codename" (implicit) config -- freezes
        # to codename mode, publishes the marker.
        error = pr_ops.refresh_source_attribution(
            wid, _config_with("codename"), record, pr, "deadbeef" * 5,
        )
        assert error == ""
        assert len(publishes) == 1
        assert "steady-anchor" in publishes[0]
        reloaded = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        frozen_pr = reloaded.active_pr()
        assert frozen_pr is not None
        assert frozen_pr.attribution_mode == "codename"
        assert frozen_pr.attribution_explicit is False

        # Second touch (a NEW head) after the live config flips to False.
        # The ORIGINAL frozen "codename" pair must still govern -- the
        # marker keeps publishing, not suppressed by the newer config.
        error2 = pr_ops.refresh_source_attribution(
            wid, _config_with(False), reloaded, frozen_pr, "cafebabe" * 5,
        )
        assert error2 == ""
        assert len(publishes) == 2
        assert "steady-anchor" in publishes[1]
        assert frozen_pr.attribution_mode == "codename"
        assert frozen_pr.attribution_explicit is False

    def test_explicit_config_addition_does_not_unsuppress_a_frozen_custom_source(
        self, pr_repo, monkeypatch,
    ):
        # Validation Plan (round-27 finding): a PR opened under an
        # IMPLICIT "codename" default against a "custom"-sourced record is
        # correctly suppressed at open (round-22 gate: an implicit default
        # never publishes a custom-vocabulary codename). Adding an
        # EXPLICIT `source_attribution: codename` to the repo's config
        # afterward must NOT retroactively unsuppress it -- `PRRecord.
        # attribution_explicit` was frozen False at open time and is never
        # re-derived from the live, now-True `source_attribution_configured`.
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]

        def _config_with(*, configured: bool):
            return dataclasses.replace(
                config,
                repos={
                    "ext": dataclasses.replace(
                        repo,
                        pr=dataclasses.replace(
                            repo.pr,
                            source_attribution="codename",
                            source_attribution_configured=configured,
                        ),
                    )
                },
            )

        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        record.codename = "quiet-harbor"
        record.codename_source = "custom"  # unreviewed custom vocabulary
        pr = tracking.PRRecord(
            state="open", branch="feature/x", provider="gitea",
            repo="example/project", number=42,
        )
        record.prs = [pr]
        tracking.save_record(record)
        publishes: list[str] = []

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                publishes.append(marker)
                return ""

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: FakeProvider(),
        )

        # First touch: implicit codename default (not configured) against
        # a custom-sourced codename -- suppressed, freezes explicit=False.
        error = pr_ops.refresh_source_attribution(
            wid, _config_with(configured=False), record, pr, "deadbeef" * 5,
        )
        assert error == ""
        assert publishes == []
        reloaded = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        frozen_pr = reloaded.active_pr()
        assert frozen_pr is not None
        assert frozen_pr.attribution_mode == "codename"
        assert frozen_pr.attribution_explicit is False

        # Second touch (a NEW head): the repo now EXPLICITLY sets
        # source_attribution: codename. The frozen attribution_explicit
        # must stay False -- the marker stays suppressed.
        error2 = pr_ops.refresh_source_attribution(
            wid, _config_with(configured=True), reloaded, frozen_pr,
            "cafebabe" * 5,
        )
        assert error2 == ""
        assert publishes == []
        assert frozen_pr.attribution_explicit is False

    def test_finish_auto_open_rerun_always_invokes_refresh_regardless_of_live_config(
        self, pr_repo, monkeypatch,
    ):
        # round-38 finding: the CALLER GATE deciding whether to invoke
        # refresh_source_attribution at all must not itself branch on live
        # config -- a PR frozen under "true" whose live config later flips
        # to "false" must still reach the frozen-pair logic inside the
        # function on a create-pr re-run.
        config, wid, _wt_path, _ = pr_repo
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        pr = tracking.PRRecord(
            state="open", branch="feature/x", provider="gitea",
            repo="example/project", number=42,
            attribution_mode="true", attribution_explicit=True, pr_id="abc",
            pr_revision=1,
        )
        record.prs = [pr]
        tracking.save_record(record)
        calls = {"count": 0}
        real_refresh = pr_ops.refresh_source_attribution

        def _spy(*a, **k):
            calls["count"] += 1
            return real_refresh(*a, **k)

        monkeypatch.setattr(pr_ops, "refresh_source_attribution", _spy)

        result: dict = {}
        # Live config now says False -- the caller gate must NOT skip the
        # call just because this snapshot says publication is off.
        import dataclasses
        repo = config.repos["ext"]
        false_config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(
                        repo.pr, source_attribution=False, auto_open=True,
                    ),
                )
            },
        )
        pr_ops._finish_auto_open(
            result, false_config, record, pr, title="Add feature", body=None,
            worktree_id=wid, head_sha="deadbeef" * 5, open_pr=None,
            draft=False, attribution=None,
            prcfg=false_config.default_repo.pr,
        )
        assert calls["count"] == 1


        # New primary flow: create-pr leaves HEAD on worktree/<id> at the
        # squashed commit, so feedback commits land there. push-changes rebases
        # worktree/<id>, re-snapshots the feature branch to its tip, and pushes
        # it -- HEAD never leaves the worktree branch.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == \
            f"worktree/{wid}"
        before = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)

        # Feedback commit directly on worktree/<id> (no checkout).
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)
        assert ok is True

        after = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)
        assert after != before  # remote feature branch advanced
        # HEAD stayed on the worktree branch; the pushed head is its tip.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == \
            f"worktree/{wid}"
        assert _git("rev-parse", f"worktree/{wid}", cwd=wt_path) == \
            _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.head_sha == _git("rev-parse", f"worktree/{wid}", cwd=wt_path)
        assert rec.pr.state == "open"

    def test_push_changes_rejects_wrong_branch(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        # HEAD on worktree/<id> (or a tracked feature branch) is valid now, so
        # push-changes only refuses a genuinely unrelated branch.
        _git("checkout", "-b", "unrelated-branch", cwd=wt_path)
        ok = fin.push_changes(wid, config)
        assert ok is False

    # --- #1815: refspec-mode push-changes updates the PR head from wt_branch --

    def _refspec_config(self, config):
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(repo.pr, head_scheme="refspec")
        return dataclasses.replace(config, repos={"ext": dataclasses.replace(repo, pr=pr)})

    def test_push_changes_refspec_updates_head_ref(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        # Refspec: HEAD stayed on the worktree branch; PR head is a remote ref.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == f"worktree/{wid}"
        before = _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path)
        anchor = Path(config.repos["ext"].anchor)
        _git("checkout", "master", cwd=anchor)
        (anchor / "upstream.txt").write_text("unrelated upstream advance\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "advance upstream", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

        # A feedback commit lands directly on worktree/<id> -- no checkout needed.
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)
        assert ok is True

        after = _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path)
        assert after != before  # remote PR head advanced
        assert _git(
            "merge-base", before, "origin/pr/add-feature-aaaa", cwd=wt_path,
        ) == before
        # HEAD never left the worktree branch; the head ref is its tip.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == f"worktree/{wid}"
        assert _git("rev-parse", f"worktree/{wid}", cwd=wt_path) == \
            _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.head_sha == _git("rev-parse", "HEAD", cwd=wt_path)
        assert rec.pr.state == "open"

    def test_push_changes_refspec_refuses_divergent_remote_head(self, pr_repo):
        """Refspec-scheme counterpart of
        test_push_changes_refuses_divergent_remote_feature_branch above (#5298)."""
        from agent_worktrees import finalize as fin
        config, wid, wt_path, remote_dir = pr_repo
        config = self._refspec_config(config)
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"], first

        other = remote_dir.parent / "other-clone-push-changes-refspec"
        _git("clone", str(remote_dir), str(other), cwd=remote_dir.parent)
        _git("config", "user.email", "other@example.com", cwd=other)
        _git("config", "user.name", "Other", cwd=other)
        _git(
            "checkout", "-B", "pr/add-feature-aaaa", "origin/pr/add-feature-aaaa",
            cwd=other,
        )
        (other / "remote.txt").write_text("other actor\n")
        _git("add", "-A", cwd=other)
        _git("commit", "-m", "remote update", cwd=other)
        _git("push", "origin", "pr/add-feature-aaaa", cwd=other)
        remote_head = _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=other)

        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)

        assert ok is False
        assert _git(
            "rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path
        ) == remote_head  # not overwritten

    def test_push_changes_refspec_rejects_wrong_branch(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        # Move HEAD off the worktree branch -- refspec push-changes must refuse.
        _git("checkout", "-b", "sidebar", cwd=wt_path)
        ok = fin.push_changes(wid, config)
        assert ok is False

    # --- a fork-headed PR: its head lives on the fork remote, not origin ------

    def _fork_config(self, config):
        """pr.fork enabled (after create-pr, which then took the plain path)."""
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(repo.pr, fork=cfg.ForkConfig(enabled=True, remote="fork"))
        return dataclasses.replace(config, repos={"ext": dataclasses.replace(repo, pr=pr)})

    def _move_head_to_fork(self, wt_path, remote_dir, branch, record):
        """The PR's head branch lives only on a fork (as the fork-PR flow
        publishes it): a bare 'fork' remote has it, origin doesn't."""
        fork_dir = remote_dir.parent / "fork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "fork", str(fork_dir), cwd=wt_path)
        _git("push", "fork", f"refs/remotes/origin/{branch}:refs/heads/{branch}", cwd=wt_path)
        _git("push", "origin", "--delete", branch, cwd=wt_path)
        rec = tracking.load_record(record.yaml_path)
        rec.pr.rewrite_owner = rec.pr.rewrite_identity = ""
        tracking.save_record(rec)
        return fork_dir

    @pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
    def test_push_changes_updates_a_fork_headed_pr_on_the_fork(self, pr_repo, scheme):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, remote_dir = pr_repo
        if scheme == "refspec":
            config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        fork_dir = self._move_head_to_fork(wt_path, remote_dir, branch, rec)
        config = self._fork_config(config)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        assert fin.push_changes(wid, config) is True

        tip = _git("rev-parse", f"worktree/{wid}", cwd=wt_path)
        on_fork = git_ops.git("--git-dir", str(fork_dir), "rev-parse", f"refs/heads/{branch}")
        assert on_fork.stdout.strip() == tip  # the PR moved
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)  # no stray copy
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.head_sha == tip
        assert rec.pr.remote == "fork"  # found once, recorded for the next push

    def _branch_on_both(self, config, wid, wt_path, remote_dir):
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        fork_dir = remote_dir.parent / "fork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "fork", str(fork_dir), cwd=wt_path)
        _git("push", "fork", f"refs/remotes/origin/{rec.pr.branch}:refs/heads/{rec.pr.branch}",
             cwd=wt_path)
        return rec

    def test_a_branch_on_both_remotes_goes_where_the_prs_head_is(self, pr_repo, monkeypatch):
        """The state the old push-changes left behind: create-pr published the PR
        to the fork, a later push recreated its branch on origin and moved it. The
        provider's PR head (still the fork's tip) picks the fork."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = self._branch_on_both(config, wid, wt_path, remote_dir)
        pr_head = _git("rev-parse", f"refs/remotes/origin/{rec.pr.branch}", cwd=wt_path)
        (wt_path / "stray.txt").write_text("pushed to origin by the old push-changes\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "stray push", cwd=wt_path)
        _git("push", "origin", f"HEAD:refs/heads/{rec.pr.branch}", cwd=wt_path)
        repo = self._fork_config(config).repos["ext"]
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: pr_head)
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == "fork"
        origin_tip = _git("rev-parse", "HEAD", cwd=wt_path)
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: origin_tip)
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == "origin"

    def test_a_branch_on_both_remotes_without_a_deciding_head_refuses(self, pr_repo, monkeypatch):
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = self._branch_on_both(config, wid, wt_path, remote_dir)
        config = self._fork_config(config)
        repo = config.repos["ext"]
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: "")  # unreadable
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        same = _git("rev-parse", f"refs/remotes/origin/{rec.pr.branch}", cwd=wt_path)
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: same)  # both match
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        assert fin.push_changes(wid, config) is False

    def test_without_a_fork_remote_the_repo_remote_is_used(self, pr_repo):
        from agent_worktrees import pr_publish
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) == "origin"

    def test_an_unreadable_fork_remote_refuses_rather_than_guessing(self, pr_repo):
        """A fork that can't be read might hold the PR's head: pushing to origin
        instead would recreate the stray branch. Nothing is pushed."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        _git("remote", "add", "fork", str(remote_dir.parent / "no-such-fork.git"), cwd=wt_path)
        _git("push", "origin", "--delete", branch, cwd=wt_path)
        config = self._fork_config(config)
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) is None
        assert fin.push_changes(wid, config) is False
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)

    def test_a_disabled_fork_config_ignores_a_local_fork_remote(self, pr_repo):
        """`pr.fork.remote` defaults to "fork" even when forking is off: an
        unrelated (here unreachable) local `fork` remote must not stop a plain
        repo's push-changes."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        _git("remote", "add", "fork", str(remote_dir.parent / "no-such-fork.git"), cwd=wt_path)
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) == "origin"
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)
        assert fin.push_changes(wid, config) is True

    def test_a_recorded_fork_remote_that_is_gone_refuses(self, pr_repo):
        """The PR was published to a fork (recorded at create-pr); that remote
        was renamed or removed since. Its updates must never fall back to origin."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        self._move_head_to_fork(wt_path, remote_dir, branch, rec)
        rec = tracking.load_record(rec.yaml_path)
        rec.pr.remote = "fork"
        tracking.save_record(rec)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.remote == "fork"  # round-trips through the record
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) == "fork"
        _git("remote", "remove", "fork", cwd=wt_path)
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) is None
        assert fin.push_changes(wid, config) is False
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)

    def test_a_legacy_fork_pr_whose_configured_fork_remote_is_gone_refuses(self, pr_repo):
        """A PR recorded before its remote was kept, published to the configured
        fork, whose `fork` remote was removed or renamed since: falling back to
        origin would recreate the stray branch, so nothing is pushed."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        self._move_head_to_fork(wt_path, remote_dir, branch, rec)
        rec.pr.remote = ""
        rec.pr.head_repo = ""
        tracking.save_record(rec)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        config = self._fork_config(config)
        _git("remote", "remove", "fork", cwd=wt_path)
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) is None
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)
        assert fin.push_changes(wid, config) is False
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)

    @staticmethod
    def _blocked_while(wt_path):
        """Start a stand-in for another worktree's fork setup (it takes the
        publication lock to repoint): True when it's still waiting."""
        import threading

        from agent_worktrees import pr_publish

        def repoint():
            with pr_publish.publish_lock(str(wt_path)):
                pass

        waiter = threading.Thread(target=repoint, daemon=True)
        waiter.start()
        waiter.join(0.3)
        return waiter.is_alive()

    def test_a_legacy_targets_tips_and_identity_are_one_locked_snapshot(self, pr_repo, monkeypatch):
        """A PR with no recorded remote is resolved by comparing branch tips, then
        reading the chosen fork's identity: a repoint can't land between them."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        self._move_head_to_fork(wt_path, remote_dir, rec.pr.branch, rec)
        rec.pr.remote = ""
        rec.pr.head_repo = ""
        config = self._fork_config(config)
        real_tip = pr_publish._tip
        seen = []

        def tip(remote, branch, cwd):
            seen.append(self._blocked_while(wt_path))
            return real_tip(remote, branch, cwd)

        monkeypatch.setattr(pr_publish, "_tip", tip)
        target = pr_publish.push_target(config.repos["ext"], rec.pr, str(wt_path))
        assert target is not None and target.remote == "fork"
        assert target.head_identity == pr_publish.push_identity("fork", cwd=str(wt_path))
        assert seen and all(seen)

    def test_retiring_a_pruned_pr_reads_its_remote_and_branch_in_one_locked_snapshot(
            self, pr_repo, monkeypatch):
        """create-pr marks a live PR merged once its branch is gone from the remote
        holding it: a repoint between choosing that remote and checking the branch
        would find it "absent" on another fork and retire a live PR."""
        config, wid, wt_path, _remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)
        from agent_worktrees import pr_publish
        real_tip = pr_publish._tip
        seen = []

        def tip(remote, branch, cwd):
            seen.append(self._blocked_while(wt_path))
            return real_tip(remote, branch, cwd)

        monkeypatch.setattr(pr_publish, "_tip", tip)
        assert pr_ops.create_pr(wid, config, title="Add feature").get("success")
        assert seen and all(seen)

    def test_a_deleted_head_is_gone_even_beside_an_archived_branch_of_the_same_name(self, pr_repo):
        """`ls-remote <branch>` is a tail glob: `archive/<branch>` must not keep
        a deleted PR head looking present to the retirement check."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, _remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        repo = config.repos["ext"]
        assert not pr_publish.head_branch_gone(repo, rec.pr, str(wt_path))
        _git("push", "origin", f"refs/remotes/origin/{branch}:refs/heads/archive/{branch}", cwd=wt_path)
        _git("push", "origin", "--delete", branch, cwd=wt_path)
        assert pr_publish.head_branch_gone(repo, rec.pr, str(wt_path))

    def test_the_publication_lock_is_reentrant_within_a_thread(self, pr_repo, monkeypatch):
        from concurrent.futures import ThreadPoolExecutor

        from agent_worktrees import pr_publish
        config, wid, wt_path, _remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        monkeypatch.setattr(pr_publish, "PUBLISH_LOCK_ACQUIRE_TIMEOUT_S", 0.05)
        with pr_publish.publish_lock(str(wt_path)):
            assert pr_publish.push_target(config.repos["ext"], rec.pr, str(wt_path)) is not None
            with pr_publish.publish_lock(str(wt_path)):
                pass
        def acquire_after_release():
            with pr_publish.publish_lock(str(wt_path)):
                return True

        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(acquire_after_release).result(timeout=30)

    def test_a_missing_fork_remote_decides_nothing_even_when_origin_matches(self, pr_repo, monkeypatch):
        """A legacy PR whose configured fork remote is gone: origin holding the
        provider's head SHA isn't proof the head lives there (a stray copy can
        hold the same commit), so nothing is pushed until the remote is back."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.remote = ""
        repo = self._fork_config(config).repos["ext"]  # fork configured, no `fork` remote here
        origin_tip = _git("rev-parse", f"refs/remotes/origin/{rec.pr.branch}", cwd=wt_path)
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: origin_tip)
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: "")  # unreadable
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: "0" * 40)  # elsewhere
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None

    def test_a_fork_remote_without_the_branch_resolves_a_usable_target(self, pr_repo):
        """The fork is present but doesn't hold the branch: origin, as a target."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.remote = ""
        fork_dir = remote_dir.parent / "empty-fork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "fork", str(fork_dir), cwd=wt_path)
        repo = self._fork_config(config).repos["ext"]
        target = pr_publish.push_target(repo, rec.pr, str(wt_path))
        assert isinstance(target, pr_publish.PushTarget) and target.remote == "origin"
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == "origin"

    def test_the_reusing_fast_path_forwards_the_forks_identity(self, pr_repo, monkeypatch):
        """A live PR re-run with nothing new to squash re-pushes its feature branch;
        that push must carry the fork's immutable identity like the other re-run."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        seen = {}
        monkeypatch.setattr(pr_publish, "update_remote",
                            lambda *a, **k: ("fork", "alice", "alice/ext", "github.com/alice/ext"))
        # The identity is the one read when the target was chosen; a later read of the shared
        # remote (another worktree may have repointed it) must never replace it.
        monkeypatch.setattr(pr_publish, "push_identity", lambda *a, **k: "elsewhere.example/alice/ext")
        monkeypatch.setattr(git_ops, "get_commits_ahead", lambda *a, **k: [])
        monkeypatch.setattr(pr_ops, "_push_existing_feature",
                            lambda *a, **k: seen.update(k) or {"success": True})
        assert pr_ops.create_pr(wid, config, title="Add feature").get("success")
        assert seen.get("fork_head_repo") == "alice/ext"
        assert seen.get("fork_head_identity") == "github.com/alice/ext"
        assert seen.get("pr_head", "").startswith("alice:")

    def _fork_headed_rerun(self, pr_repo):
        """A live PR published to a fork (recorded), under a config that no
        longer forks, with a new commit for create-pr to re-squash."""
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        fork_dir = self._move_head_to_fork(wt_path, remote_dir, rec.pr.branch, rec)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.remote = "fork"
        tracking.save_record(rec)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)
        return config, wid, wt_path, fork_dir, rec.pr.branch

    def test_create_pr_rerun_updates_a_fork_headed_pr_on_its_fork(self, pr_repo, monkeypatch):
        """Today's config resolves origin, but the live PR's head is on the fork
        it was published to: the re-squashed head goes there, stays recorded, and
        the PR head is the fork owner's (so a not-yet-opened PR opens from it)."""
        from agent_worktrees import pr_publish
        real_slug = pr_publish.push_slug
        monkeypatch.setattr(pr_publish, "push_slug", lambda r, *, cwd: (
            "alice/ext" if r == "fork" else real_slug(r, cwd=cwd)))
        config, wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        result = pr_ops.create_pr(wid, config, title="Add feature")
        assert result.get("success"), result
        assert result["remote"] == "fork"
        assert result["pr_head"] == f"alice:{branch}"
        on_fork = git_ops.git("--git-dir", str(fork_dir), "rev-parse", f"refs/heads/{branch}")
        assert on_fork.stdout.strip() == result["head_sha"]
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert (rec.pr.remote, rec.pr.head_repo) == ("fork", "alice/ext")

    def test_a_fork_repointed_to_the_same_slug_on_another_host_refuses(self, pr_repo, monkeypatch):
        """`owner/name` alone isn't the fork: the same slug on another host (here,
        another destination the slug check can't tell apart) is another repository.
        The recorded host/owner/name identity refuses it, both when choosing the
        remote and under the publication lock."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        real_slug = pr_publish.push_slug
        monkeypatch.setattr(pr_publish, "push_slug", lambda r, *, cwd: (
            "alice/ext" if r == "fork" else real_slug(r, cwd=cwd)))
        config, wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        assert pr_ops.create_pr(wid, config, title="Add feature").get("success")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.head_repo == "alice/ext" and rec.pr.head_identity  # recorded, with its host
        other = fork_dir.parent / "same-slug-elsewhere.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        _git("remote", "set-url", "fork", str(other), cwd=wt_path)
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) is None
        pushed = []
        real_push = git_ops.push
        monkeypatch.setattr(git_ops, "push", lambda *a, **k: pushed.append(a) or real_push(*a, **k))
        result = pr_publish.push_checked(rec, "fork", f"HEAD:refs/heads/{branch}", cwd=str(wt_path))
        assert not result and result.stderr == pr_publish.REPOINTED and pushed == []
        (wt_path / "d.txt").write_text("more\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "more feedback", cwd=wt_path)
        assert fin.push_changes(wid, config) is False
        assert pushed == []

    def test_a_recorded_fork_remote_repointed_elsewhere_refuses(self, pr_repo, monkeypatch):
        """Fork setup repoints `fork` when the identity changes: a remote that now
        names another repo than the PR's head repo isn't the PR's fork."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, _fork_dir, branch = self._fork_headed_rerun(pr_repo)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.head_repo = "alice/ext"
        tracking.save_record(rec)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.head_repo == "alice/ext"  # round-trips through the record
        repo = config.repos["ext"]
        monkeypatch.setattr(pr_publish, "push_slug", lambda r, *, cwd: "Alice/Ext")
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == "fork"
        monkeypatch.setattr(pr_publish, "push_slug", lambda r, *, cwd: "bob/ext")
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        result = pr_ops.create_pr(wid, config, title="Add feature")
        assert result.get("error") == pr_publish.UNREADABLE_REMOTE
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)

    def test_a_fork_remote_whose_push_url_names_another_repo_refuses(self, pr_repo):
        """`git push fork` honors `remote.fork.pushurl`: a fork fetched from the
        PR's head repo but pushing to another one would publish the update (and
        lease it on a tip read) elsewhere. Recorded or discovered, it's refused."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        other = fork_dir.parent / "other.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.head_repo = git_ops.remote_slug("fork", cwd=wt_path)
        repo = self._fork_config(config).repos["ext"]
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == "fork"
        _git("remote", "set-url", "--push", "fork", str(other), cwd=wt_path)
        assert pr_publish.push_slug("fork", cwd=wt_path) is None
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        rec.pr.remote = rec.pr.head_repo = ""  # an older record: found from the fork's tip
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        tracking.save_record(rec)
        assert fin.push_changes(wid, self._fork_config(config)) is False
        assert not git_ops.git("--git-dir", str(other), "rev-parse", "--verify", "-q",
                               f"refs/heads/{branch}", check=False).stdout.strip()

    def test_a_fork_repointed_after_it_was_chosen_is_refused_at_push_time(self, pr_repo, monkeypatch):
        """Another worktree's fork setup repoints the shared remote between
        resolution and push: the push re-checks under the publication lock,
        and a mid-push repoint is safe because transport uses the pinned URL."""
        import threading

        from agent_worktrees import pr_publish
        config, wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.head_repo = pr_publish.push_slug("fork", cwd=str(wt_path))
        rec.pr.head_identity = pr_publish.push_identity("fork", cwd=str(wt_path))  # every fork push knows it
        pushed = []

        def fake_push(remote, refspec, **kw):
            def repoint():  # stands in for another worktree's fork setup
                with pr_publish.publish_lock(str(wt_path)):
                    pass

            waiter = threading.Thread(target=repoint, daemon=True)
            waiter.start()
            waiter.join(10)
            pushed.append((remote, refspec, waiter.is_alive()))
            return git_ops.PushResult(ok=True)

        monkeypatch.setattr(git_ops, "push", fake_push)
        assert pr_publish.push_checked(rec, "fork", branch, cwd=str(wt_path))
        assert pushed == [("fork", branch, False)]
        other = fork_dir.parent / "elsewhere.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        _git("remote", "set-url", "fork", str(other), cwd=wt_path)  # repointed since it was chosen
        result = pr_publish.push_checked(rec, "fork", f"HEAD:refs/heads/{branch}", cwd=str(wt_path))
        assert not result and result.stderr == pr_publish.REPOINTED
        assert len(pushed) == 1

    def test_a_fresh_fork_push_repointed_before_the_lock_is_refused(
        self, pr_repo, monkeypatch,
    ):
        """A fork target chosen for a fresh/unrecorded PR must still be checked
        under the publication lock; an empty recorded head_repo is not a bypass."""
        from agent_worktrees import pr_publish
        _config, _wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        rec = tracking.load_record(cfg.tracking_dir() / f"{_wid}.yaml")
        expected = pr_publish.push_slug("fork", cwd=str(wt_path))
        other = fork_dir.parent / "elsewhere.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        _git("remote", "set-url", "fork", str(other), cwd=wt_path)
        pushed = []
        monkeypatch.setattr(git_ops, "push", lambda *a, **k: pushed.append(a) or git_ops.PushResult(ok=True))

        result = pr_publish.push_checked(
            rec, "fork", f"HEAD:refs/heads/{branch}", cwd=str(wt_path),
            expected_head_repo=expected,
        )

        assert not result and result.stderr == pr_publish.REPOINTED
        assert pushed == []

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

    @pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
    def test_an_unreachable_recorded_fork_fails_push_changes_cleanly(self, pr_repo, monkeypatch, scheme):
        """The recorded fork is chosen from local identity alone; when it can't be
        fetched (gone, auth, timeout), push-changes returns False -- nothing is
        rebased or pushed -- instead of raising."""
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _fork_dir, _branch = self._fork_headed_rerun(pr_repo)
        if scheme == "refspec":
            config = self._refspec_config(config)
        real_fetch = git_ops.fetch

        def fetch(remote, *a, **k):
            if remote == "fork":
                raise git_ops.GitError(["fetch", remote], 128, "fatal: could not read from remote repository")
            return real_fetch(remote, *a, **k)

        pushed = []
        monkeypatch.setattr(git_ops, "fetch", fetch)
        monkeypatch.setattr(git_ops, "push", lambda *a, **k: pushed.append(a) or git_ops.PushResult(ok=True))
        assert fin.push_changes(wid, config) is False
        assert pushed == []

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

    def test_a_fresh_fork_target_is_checked_by_its_full_identity_under_the_lock(self, pr_repo, monkeypatch):
        """A fresh/legacy target has no recorded identity: the one read when the target
        was chosen is required, so a same-slug repoint before the lock is refused, and
        a fork push with no identity at all is refused too."""
        from agent_worktrees import pr_publish
        real_slug = pr_publish.push_slug
        monkeypatch.setattr(pr_publish, "push_slug", lambda r, *, cwd: (
            "alice/ext" if r == "fork" else real_slug(r, cwd=cwd)))
        _config, wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.remote = rec.pr.head_repo = rec.pr.head_identity = ""  # fresh/legacy: nothing recorded
        chosen = pr_publish.push_identity("fork", cwd=str(wt_path))
        other = fork_dir.parent / "same-slug-elsewhere.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        _git("remote", "set-url", "fork", str(other), cwd=wt_path)  # repointed after it was chosen
        pushed = []
        monkeypatch.setattr(git_ops, "push", lambda *a, **k: pushed.append(a) or git_ops.PushResult(ok=True))
        for identity in (chosen, ""):
            result = pr_publish.push_checked(rec, "fork", f"HEAD:refs/heads/{branch}", cwd=str(wt_path),
                                             expected_head_repo="alice/ext", expected_head_identity=identity)
            assert not result and result.stderr == pr_publish.REPOINTED, identity
        assert pushed == []
        now = pr_publish.push_identity("fork", cwd=str(wt_path))
        ok = pr_publish.push_checked(rec, "fork", f"HEAD:refs/heads/{branch}", cwd=str(wt_path),
                                     expected_head_repo="alice/ext", expected_head_identity=now)
        assert ok and ok.head_identity == now and len(pushed) == 1

    def test_a_plaintext_push_url_is_never_the_same_destination_as_a_secure_fetch_url(self, tmp_path):
        """git_ops.push's credential header is built for the fetch URL: an http:// (or
        git://) push URL with the same host/path would carry it over plaintext."""
        from agent_worktrees import pr_publish
        _git("init", "-q", str(tmp_path), cwd=tmp_path)
        _git("remote", "add", "r", "https://github.com/alice/ext.git", cwd=tmp_path)
        for plain in ("http://github.com/alice/ext.git", "git://github.com/alice/ext.git"):
            _git("remote", "set-url", "--push", "r", plain, cwd=tmp_path)
            assert pr_publish.push_slug("r", cwd=str(tmp_path)) is None, plain
        _git("remote", "set-url", "--push", "r", "git@github.com:alice/ext.git", cwd=tmp_path)
        assert pr_publish.push_slug("r", cwd=str(tmp_path)) == "alice/ext"  # ssh and https still agree

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

    def test_the_repo_remote_fallbacks_refuse_a_push_url_naming_another_repo(self, pr_repo):
        """The repo's own remote is the target of a PR with no fork configured, and of
        one no configured fork holds: like a fork, it must push where it fetches from,
        since the push's lease and credential come from the fetch URL."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        assert pr_ops.create_pr(wid, config, title="Add feature").get("success")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        repo, fork_repo = config.repos["ext"], self._fork_config(config).repos["ext"]
        fork_dir = remote_dir.parent / "fork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "fork", str(fork_dir), cwd=wt_path)  # configured, without the branch
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == repo.remote
        assert pr_publish.push_remote(fork_repo, rec.pr, str(wt_path)) == repo.remote
        other = remote_dir.parent / "elsewhere.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        _git("remote", "set-url", "--push", repo.remote, str(other), cwd=wt_path)
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        assert pr_publish.push_remote(fork_repo, rec.pr, str(wt_path)) is None
        (wt_path / "e.txt").write_text("more\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "more", cwd=wt_path)
        assert fin.push_changes(wid, config) is False
        assert not git_ops.git("--git-dir", str(other), "rev-parse", "--verify", "-q",
                               f"refs/heads/{rec.pr.branch}", check=False).stdout.strip()

    def test_an_explicit_fork_owner_survives_a_rerun(self, pr_repo, monkeypatch):
        """The PR head's owner recorded at publish (an explicit pr.fork.owner) wins over
        the fork repository's owner on a rerun, e.g. after opening was deferred."""
        from agent_worktrees import pr_publish
        real_slug = pr_publish.push_slug
        monkeypatch.setattr(pr_publish, "push_slug", lambda r, *, cwd: (
            "alice/ext" if r == "fork" else real_slug(r, cwd=cwd)))
        config, wid, wt_path, _fork_dir, branch = self._fork_headed_rerun(pr_repo)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.head_owner = "alice-org"
        tracking.save_record(rec)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        where, owner, slug, _identity = pr_publish.update_remote(config.repos["ext"], rec, branch, str(wt_path), "origin")
        assert (where, owner, slug) == ("fork", "alice-org", "alice/ext")

    def test_a_push_url_must_name_the_same_host_and_repo_as_the_fetch_url(self, tmp_path):
        """Same ``owner/name`` on another host is another destination; a repo's
        ssh and https forms are the same one."""
        from agent_worktrees import pr_publish
        _git("init", "-q", str(tmp_path), cwd=tmp_path)
        _git("remote", "add", "r", "https://github.com/alice/ext.git", cwd=tmp_path)
        assert pr_publish.push_slug("r", cwd=str(tmp_path)) == "alice/ext"
        for same in ("git@github.com:alice/ext.git", "ssh://git@github.com/Alice/ext",
                     "https://user@github.com/alice/ext/", "https://github.com:443/alice/ext",
                     "ssh://git@github.com:22/alice/ext.git"):
            _git("remote", "set-url", "--push", "r", same, cwd=tmp_path)
            assert pr_publish.push_slug("r", cwd=str(tmp_path)) == "alice/ext", same
        for other in ("https://gitlab.example.com/alice/ext.git", "git@example.com:alice/ext.git",
                      "https://github.com:8443/alice/ext.git", "ssh://git@github.com:2222/alice/ext",
                      str(tmp_path / "alice" / "ext.git")):
            _git("remote", "set-url", "--push", "r", other, cwd=tmp_path)
            assert pr_publish.push_slug("r", cwd=str(tmp_path)) is None, other
        _git("remote", "add", "l", str(tmp_path / "forks" / "ext.git"), cwd=tmp_path)
        assert pr_publish.push_slug("l", cwd=str(tmp_path)) is not None
        _git("remote", "set-url", "--push", "l", str(tmp_path / "other" / "forks" / "ext.git"),
             cwd=tmp_path)
        assert pr_publish.push_slug("l", cwd=str(tmp_path)) is None  # same tail, another repo

    def test_file_url_identity_preserves_non_local_authority(self, tmp_path):
        """UNC-style file authorities are part of the destination identity."""
        from agent_worktrees import pr_publish
        cwd = str(tmp_path)

        assert pr_publish._identity("file:///x", cwd) == pr_publish._identity(
            "file://localhost/x", cwd,
        )
        assert pr_publish._identity("file://SERVER-A/share/repo.git", cwd) == (
            pr_publish._identity("file://server-a/share/repo.git", cwd)
        )
        assert pr_publish._identity("file://server-a/share/repo.git", cwd) != (
            pr_publish._identity("file://server-b/share/repo.git", cwd)
        )

    def test_publication_lock_timeout_is_controlled(self, tmp_path, monkeypatch):
        """A contended publication lock fails as a PushResult, not a traceback."""
        import subprocess
        import sys
        import textwrap
        import time

        from agent_worktrees import pr_publish, push_timeout

        # A holder's push may retry once without its auth override, each attempt bounded.
        assert pr_publish.PUBLISH_LOCK_ACQUIRE_TIMEOUT_S > 2 * push_timeout.DEFAULT_PUSH_TIMEOUT
        _git("init", "-q", str(tmp_path), cwd=tmp_path)
        marker = tmp_path / "locked"
        script = textwrap.dedent(
            """
            import sys, time
            from pathlib import Path
            from agent_worktrees import pr_publish

            with pr_publish.publish_lock(sys.argv[1]):
                Path(sys.argv[2]).write_text("locked", encoding="utf-8")
                time.sleep(5)
            """
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), str(marker)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not marker.exists() and holder.poll() is None:
                time.sleep(0.01)
            assert marker.exists(), holder.communicate(timeout=1)
            monkeypatch.setattr(pr_publish, "PUBLISH_LOCK_ACQUIRE_TIMEOUT_S", 0.05)
            monkeypatch.setattr(pr_publish, "PUBLISH_LOCK_RETRY_INTERVAL_S", 0.01)

            result = pr_publish.push_checked(
                None, "origin", "HEAD:refs/heads/main", cwd=str(tmp_path),
            )

            assert not result
            assert "Timed out waiting 0.05s for the PR publication lock" in result.stderr
            assert "Nothing was pushed." in result.stderr
        finally:
            if holder.poll() is None:
                holder.terminate()
                try:
                    holder.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    holder.kill()
                    holder.wait(timeout=5)

    def test_create_pr_rerun_never_overwrites_a_fork_head_another_checkout_pushed(self, pr_repo):
        """The fork head moved since this checkout published it (another checkout
        pushed): the re-squash's push is leased against the PR tip this checkout
        last observed (#5298), so it's refused and the other push survives."""
        config, wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        other = wt_path.parent / "second-checkout"
        _git("clone", "-q", "-b", branch, str(fork_dir), str(other), cwd=wt_path.parent)
        _git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q",
             "--allow-empty", "-m", "pushed from another checkout", cwd=other)
        _git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=other)
        theirs = _git("rev-parse", "HEAD", cwd=other)
        result = pr_ops.create_pr(wid, config, title="Add feature")
        assert not result.get("success") and "Failed to push" in result.get("error", ""), result
        on_fork = git_ops.git("--git-dir", str(fork_dir), "rev-parse", f"refs/heads/{branch}")
        assert on_fork.stdout.strip() == theirs

    def test_create_pr_rerun_refuses_when_the_recorded_fork_is_gone(self, pr_repo):
        from agent_worktrees import pr_publish
        config, wid, wt_path, _fork_dir, branch = self._fork_headed_rerun(pr_repo)
        _git("remote", "remove", "fork", cwd=wt_path)
        result = pr_ops.create_pr(wid, config, title="Add feature")
        assert result.get("error") == pr_publish.UNREADABLE_REMOTE
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.remote == "fork"

    def test_a_probe_matches_the_exact_branch_not_a_suffix(self, pr_repo):
        """ls-remote patterns are tail globs: a fork holding only
        `archive/<branch>` doesn't hold the PR's branch."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        fork_dir = remote_dir.parent / "fork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "fork", str(fork_dir), cwd=wt_path)
        _git("push", "fork", f"refs/remotes/origin/{branch}:refs/heads/archive/{branch}",
             cwd=wt_path)
        assert pr_publish._tip("fork", branch, str(wt_path)) == ""
        _git("push", "fork", f"refs/remotes/origin/{branch}:refs/heads/{branch}", cwd=wt_path)
        tip = _git("rev-parse", f"refs/remotes/origin/{branch}", cwd=wt_path)
        assert pr_publish._tip("fork", branch, str(wt_path)) == tip

    def test_a_probe_that_times_out_counts_as_unreadable(self, pr_repo, monkeypatch):
        import subprocess

        from agent_worktrees import pr_publish
        config, wid, wt_path, _ = pr_repo

        real_git = pr_publish.git_ops.git

        def hang(*args, **kw):
            if "ls-remote" not in args:
                return real_git(*args, **kw)
            assert kw.get("timeout") == pr_publish.PROBE_TIMEOUT
            raise subprocess.TimeoutExpired(args, kw["timeout"])

        monkeypatch.setattr(pr_publish.git_ops, "git", hang)
        assert pr_publish._tip("fork", "some-branch", str(wt_path)) is None

    def test_a_role_overrides_fork_remote_is_found_too(self, pr_repo):
        """create-pr publishes through the role-resolved fork remote, which a
        role override can rename: push-changes must look there as well."""
        import dataclasses

        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        fork_dir = remote_dir.parent / "myfork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "myfork", str(fork_dir), cwd=wt_path)
        _git("push", "myfork", f"refs/remotes/origin/{branch}:refs/heads/{branch}", cwd=wt_path)
        _git("push", "origin", "--delete", branch, cwd=wt_path)
        repo = config.repos["ext"]
        role = cfg.PRRoleOverride(fork=cfg.ForkConfig(enabled=True, remote="myfork"))
        repo = dataclasses.replace(repo, pr=dataclasses.replace(repo.pr, roles={"write": role}))
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == "myfork"
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) == "origin"

    def test_push_changes_refspec_blocks_a_leaking_recorded_branch(self, pr_repo):
        # Same guard as the snapshot-mode test above, exercised on the
        # refspec publish path.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")

        leaking_branch = f"worktree/{wid}"
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.branch = leaking_branch
        tracking.save_record(rec)

        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)
        assert ok is False
        assert not git_ops.remote_branch_exists(
            "origin", leaking_branch, cwd=str(wt_path)
        )

    def test_push_changes_refspec_dirty_refused(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        (wt_path / "dirty.txt").write_text("uncommitted\n")
        ok = fin.push_changes(wid, config)
        assert ok is False

    # --- #1045: finalize must not false-block once the PR is merged -----------

    def _simulate_squash_merge(self, config, wid, feature):
        """Squash-merge *feature* into origin/master (mimics a Gitea merge).

        Leaves ``origin/<feature>`` at its stale pre-merge head -- the exact
        condition that tripped the old precondition (#1045).
        """
        anchor = config.default_repo.anchor
        _git("fetch", "origin", cwd=anchor)
        _git("checkout", "master", cwd=anchor)
        _git("merge", "--squash", f"origin/{feature}", cwd=anchor)
        _git("commit", "-m", f"Squash merge {feature}", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

    def test_precondition_ok_after_merge(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        # origin/<feature> is stale (pre-merge); the OLD check would false-block.
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.state = "merged"
        tracking.save_record(rec)
        repo = config.default_repo
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is True, err
        assert err is None

    def test_finalize_refuses_dirty_worktree_at_merged_head(self, pr_repo):
        # #4400 review finding: _pr_finalize_precondition only inspects
        # commits, never the working tree -- a modified/untracked file at an
        # otherwise-safe merged head must still block finalize, or the
        # destructive cleanup that follows would silently discard it.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.state = "merged"
        tracking.save_record(rec)
        (wt_path / "uncommitted.txt").write_text("real work, never committed\n")

        ok = fin.validate_and_finalize(wid, config)
        assert ok is False
        assert (wt_path / "uncommitted.txt").exists()

    def test_finalize_reruns_precondition_after_lock_acquired(self, pr_repo, monkeypatch):
        # #4400 round 14: the merged-head precondition previously ran only
        # BEFORE the finalize lock was acquired -- a commit landing in that
        # TOCTOU window (between the preflight and the destructive cleanup)
        # would be silently discarded. validate_and_finalize must re-run the
        # SAME precondition immediately after the lock is held.
        from agent_worktrees import finalize as fin
        from agent_worktrees import finalize_lock
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.state = "merged"
        tracking.save_record(rec)

        real_acquire = finalize_lock.FinalizeLock.acquire

        def racing_acquire(lock_self):
            real_acquire(lock_self)
            # Simulate a commit landing in the TOCTOU window, after the
            # preflight validated safety but before lock-protected cleanup.
            (wt_path / "raced_in.txt").write_text("landed after preflight\n")
            git_ops.git("add", "-A", cwd=str(wt_path))
            git_ops.git("commit", "-m", "race window commit", cwd=str(wt_path))

        monkeypatch.setattr(finalize_lock.FinalizeLock, "acquire", racing_acquire)

        ok = fin.validate_and_finalize(wid, config)
        assert ok is False
        assert (wt_path / "raced_in.txt").exists()

    def test_finalize_catches_race_commit_landing_during_process_termination(
        self, pr_repo, monkeypatch,
    ):
        # #4400 round 15: round 14's re-check ran ONCE, immediately after the
        # finalize lock was acquired -- but further code (record reload,
        # pointer reconciliation, live-session checks, process termination)
        # still ran AFTER that point and BEFORE the actual destructive
        # removal, leaving a residual window. The re-check must run as the
        # LAST possible step, immediately before the destructive git
        # operation -- proven here by racing a commit in AFTER process
        # termination, later than round 14's check point.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.state = "merged"
        tracking.save_record(rec)

        real_terminate = fin.procs.terminate_processes_under

        def racing_terminate(worktree_path):
            result = real_terminate(worktree_path)
            (wt_path / "raced_in_late.txt").write_text("landed during cleanup\n")
            git_ops.git("add", "-A", cwd=str(wt_path))
            git_ops.git("commit", "-m", "late race window commit", cwd=str(wt_path))
            return result

        monkeypatch.setattr(fin.procs, "terminate_processes_under", racing_terminate)

        ok = fin.validate_and_finalize(wid, config)
        assert ok is False
        assert (wt_path / "raced_in_late.txt").exists()

    def test_finalize_rechecks_precondition_before_reconciliation(
        self, pr_repo, monkeypatch,
    ):
        # #4400 round 16: reconciliation (`_reconcile_merged_pointers`)
        # rebases the worktree branch onto upstream -- and git's rebase
        # silently DROPS a commit whose patch is already reachable/applied
        # upstream, exactly the kind of commit a race could land right
        # before this call, with no trace left to detect afterward. The
        # precondition must re-run immediately before reconciliation is
        # invoked, not only once after lock acquisition and once at the
        # very end. Verified deterministically via call-order spies (a
        # full git-level reproduction would need to reconstruct git's own
        # patch-id "already applied" heuristic, which is unreliable to pin
        # down across git versions) rather than assuming a specific git
        # rebase outcome.
        from agent_worktrees import finalize as fin
        config, wid, _wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.state = "merged"
        tracking.save_record(rec)

        from agent_worktrees import finalize_open_pr_gate

        call_order: list[str] = []
        real_reconcile = fin._reconcile_merged_pointers
        real_precheck = finalize_open_pr_gate.pr_precondition_recheck

        def spy_reconcile(*a, **k):
            call_order.append("reconcile")
            return real_reconcile(*a, **k)

        def spy_precheck(*a, **k):
            call_order.append("precheck")
            return real_precheck(*a, **k)

        monkeypatch.setattr(fin, "_reconcile_merged_pointers", spy_reconcile)
        monkeypatch.setattr(
            finalize_open_pr_gate, "pr_precondition_recheck", spy_precheck,
        )

        ok = fin.validate_and_finalize(wid, config)
        assert ok is True
        assert "precheck" in call_order and "reconcile" in call_order
        assert call_order.index("precheck") < call_order.index("reconcile"), (
            f"precondition recheck must run BEFORE reconciliation, got {call_order}"
        )

    def test_precondition_ok_after_merge_remote_branch_deleted(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        # Provider deleted the remote feature branch on merge.
        _git("push", "origin", "--delete", feature, cwd=config.default_repo.anchor)
        _git("fetch", "origin", "--prune", cwd=str(wt_path))
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        repo = config.default_repo
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is True, err

    # --- #1106: reconcile merged branch pointers so the picker isn't diverged -

    def test_reconcile_aligns_worktree_base_after_merge(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        # Drift scenario: HEAD checked out on the feature branch. create-pr
        # returns HEAD to the base branch (#1804), so establish the drift here.
        _git("checkout", feature, cwd=wt_path)
        self._simulate_squash_merge(config, wid, feature)
        _git("fetch", "origin", cwd=str(wt_path))
        repo = config.default_repo
        wt_branch = f"worktree/{wid}"

        # HEAD is on the feature branch (drift); worktree/<id> is a free pointer.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == feature

        fin._reconcile_merged_pointers(repo, str(wt_path), repo.anchor, wt_branch)

        # worktree/<id> now aligns with origin/master -> 0 ahead in the picker.
        wt_sha = _git("rev-parse", wt_branch, cwd=wt_path)
        up_sha = _git("rev-parse", "origin/master", cwd=wt_path)
        assert wt_sha == up_sha
        # The live feature checkout is untouched.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == feature

    def test_reconcile_fast_forwards_anchor_default_branch(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        repo = config.default_repo
        anchor = repo.anchor
        # Rewind the anchor's local master behind origin to prove the FF.
        _git("reset", "--hard", "HEAD~1", cwd=anchor)
        assert _git("rev-parse", "master", cwd=anchor) != \
            _git("rev-parse", "origin/master", cwd=anchor)

        fin._reconcile_merged_pointers(repo, str(wt_path), anchor, f"worktree/{wid}")

        assert _git("rev-parse", "master", cwd=anchor) == \
            _git("rev-parse", "origin/master", cwd=anchor)

    def test_reconcile_fast_forwards_base_when_head_on_base(self, pr_repo):
        """#1804: create-pr leaves HEAD on worktree/<id> at the squashed commit,
        so after the squash-merge the base is *diverged* (1 ahead + behind).
        Reconcile realigns it in place to origin/master once the content is
        confirmed on upstream -- HEAD never leaves worktree/<id>, no work lost.
        (Same in-place path the refspec scheme uses.)"""
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        wt_branch = f"worktree/{wid}"
        # create-pr leaves HEAD on the worktree base branch.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == wt_branch

        self._simulate_squash_merge(config, wid, feature)
        _git("fetch", "origin", cwd=str(wt_path))
        repo = config.default_repo
        # The merge advanced origin/master, so the base branch is now behind.
        assert _git("rev-parse", wt_branch, cwd=wt_path) != \
            _git("rev-parse", "origin/master", cwd=wt_path)

        fin._reconcile_merged_pointers(repo, str(wt_path), repo.anchor, wt_branch)

        # Fast-forwarded in place: base branch == origin/master, HEAD unchanged.
        assert _git("rev-parse", wt_branch, cwd=wt_path) == \
            _git("rev-parse", "origin/master", cwd=wt_path)
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == wt_branch

    def test_precondition_ok_refspec_after_create_pr(self, pr_repo):
        # #1815: finalize precondition passes in refspec -- the tracked PR head
        # (pr/<slug>) is a remote ref, and content-on-upstream / remote-exists
        # both hold; no local feature branch is required.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        import dataclasses
        repo = config.default_repo
        repo = dataclasses.replace(repo, pr=dataclasses.replace(repo.pr, strategy="detach"))
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is True, err
        assert err is None

    def test_reconcile_refspec_realigns_after_merge_in_place(self, pr_repo):
        # #1815: refspec worktree/<id> sits ahead while the PR is open, so after
        # the squash-merge it is diverged (ahead+behind) and a plain FF can't
        # align it. Reconcile realigns it in place once content is confirmed on
        # upstream -- HEAD stays on worktree/<id>, no work lost.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        wt_branch = f"worktree/{wid}"
        # refspec: worktree/<id> carries the squashed commit (1 ahead of master).
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == wt_branch
        assert len(git_ops.get_commits_ahead(
            wt_branch, "origin/master", cwd=str(wt_path))) == 1

        self._simulate_squash_merge(config, wid, "pr/add-feature-aaaa")
        _git("fetch", "origin", cwd=str(wt_path))
        # Diverged now: 1 ahead (pre-merge squash) + behind (the merge commit).
        repo = config.default_repo
        assert _git("rev-parse", wt_branch, cwd=wt_path) != \
            _git("rev-parse", "origin/master", cwd=wt_path)

        fin._reconcile_merged_pointers(repo, str(wt_path), repo.anchor, wt_branch)

        # Realigned in place to origin/master; HEAD never left worktree/<id>.
        assert _git("rev-parse", wt_branch, cwd=wt_path) == \
            _git("rev-parse", "origin/master", cwd=wt_path)
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == wt_branch

    def test_reconcile_refspec_leaves_open_pr_ahead(self, pr_repo):
        # An OPEN refspec PR must NOT be realigned -- its content is not yet on
        # upstream, so reconcile leaves worktree/<id> ahead (honest #1815/#5).
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        wt_branch = f"worktree/{wid}"
        before = _git("rev-parse", wt_branch, cwd=wt_path)
        repo = config.default_repo
        fin._reconcile_merged_pointers(repo, str(wt_path), repo.anchor, wt_branch)
        # Unchanged -- the PR is still open (content not on upstream).
        assert _git("rev-parse", wt_branch, cwd=wt_path) == before
        assert len(git_ops.get_commits_ahead(
            wt_branch, "origin/master", cwd=str(wt_path))) == 1
    """``pr.required`` blocks the direct-to-master path entirely."""

    def _required_config(self, config):
        repo = config.default_repo
        return cfg.Config(
            srcroot=config.srcroot, machine=config.machine,
            platform=config.platform, repo_name=config.repo_name,
            repos={config.repo_name: cfg.RepoConfig(
                anchor=repo.anchor, worktree_root=repo.worktree_root,
                default_branch=repo.default_branch, remote=repo.remote,
                pr=cfg.PRConfig(
                    enabled=True, required=True,
                    provider="gitea", branch_prefix="feature",
                    head_scheme="snapshot",
                ),
            )},
        )

    def test_push_changes_refuses_direct_to_master(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, remote_dir = pr_repo
        req_config = self._required_config(config)

        before = _git("ls-remote", str(remote_dir), "master", cwd=wt_path)
        # No create-pr was run -> no PR record -> direct push must be refused.
        ok = fin.push_changes(wid, req_config)
        assert ok is False
        after = _git("ls-remote", str(remote_dir), "master", cwd=wt_path)
        assert after == before  # remote master untouched

    def test_finalize_refuses_unmerged_direct(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, _wt_path, _ = pr_repo
        req_config = self._required_config(config)
        # Unmerged work, no PR -> finalize must refuse (not prune).
        ok = fin.validate_and_finalize(wid, req_config)
        assert ok is False

    def test_create_pr_path_still_works_when_required(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        req_config = self._required_config(config)
        # The PR path remains available: create-pr then push-changes updates
        # the feature branch, never master.
        pr_ops.create_pr(wid, req_config, title="Add feature")
        # create-pr returns HEAD to the base branch (#1804); check out the
        # feature branch to add a feedback commit that push-changes pushes.
        _git("checkout", "feature/add-feature-aaaa", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)
        ok = fin.push_changes(wid, req_config)
        assert ok is True
