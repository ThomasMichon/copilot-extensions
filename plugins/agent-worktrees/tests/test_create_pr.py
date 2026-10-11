"""PR creation and snapshot publication."""

from __future__ import annotations

import pytest
from pathlib import Path
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking
from pr_test_helpers import _git

pytestmark = pytest.mark.contract("agent_worktrees.pr_ops.create")


class TestCreatePR:
    def test_disabled_errors(self, pr_repo):
        config, wid, _wt_path, _ = pr_repo
        import dataclasses
        disabled = dataclasses.replace(
            config.repos["ext"], pr=cfg.PRConfig(enabled=False)
        )
        config2 = dataclasses.replace(config, repos={"ext": disabled})
        res = pr_ops.create_pr(wid, config2)
        assert res["success"] is False
        assert "not enabled" in res["error"]

    def test_freezes_attribution_and_stamps_pr_id_at_fresh_construction(
        self, pr_repo,
    ):
        # codename-attribution-by-default (rounds 26-39): create_pr's own
        # fresh-construction site must stamp the frozen pair + pr_id.
        config, wid, _wt_path, _ = pr_repo
        res = pr_ops.create_pr(wid, config, title="Add feature")
        assert res["success"] is True
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        pr = rec.active_pr()
        assert pr is not None
        # pr_repo's bare PRConfig() resolves the implicit "codename" default.
        assert pr.attribution_mode == "codename"
        assert pr.attribution_explicit is False
        assert pr.pr_id
        assert pr.pr_revision == 1

    def test_freezes_explicit_per_call_override_verbatim(self, pr_repo):
        # round-31 finding: the stamp must capture the caller's EFFECTIVE
        # attribution, including a per-call override, stamped VERBATIM --
        # never re-derived from prcfg directly.
        config, wid, _wt_path, _ = pr_repo
        res = pr_ops.create_pr(
            wid, config, title="Add feature", attribution="codename",
        )
        assert res["success"] is True
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        pr = rec.active_pr()
        assert pr is not None
        assert pr.attribution_mode == "codename"
        assert pr.attribution_explicit is True

    def test_required_body_sections_fail_before_branch_publication(self, pr_repo):
        import dataclasses

        config, wid, wt_path, _ = pr_repo
        repo = config.repos["ext"]
        pr = dataclasses.replace(
            repo.pr,
            required_body_sections=("Intent", "Changes", "Validation"),
        )
        config = dataclasses.replace(
            config,
            repos={"ext": dataclasses.replace(repo, pr=pr)},
        )

        result = pr_ops.create_pr(
            wid,
            config,
            title="Add feature",
            body="## Intent\nShip it.\n",
        )

        assert result["success"] is False
        assert "Changes, Validation" in result["error"]
        assert not git_ops.local_branch_exists(
            "feature/add-feature-aaaa",
            cwd=str(wt_path),
        )

    def test_required_body_is_not_repeated_for_existing_pr_rerun(self, pr_repo):
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]
        config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(
                        repo.pr,
                        required_body_sections=("Intent",),
                    ),
                )
            },
        )
        body = "## Intent\nShip it.\n"
        assert pr_ops.create_pr(
            wid, config, title="Add feature", body=body
        )["success"]
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        record.pr.number = 42
        tracking.save_record(record)

        rerun = pr_ops.create_pr(wid, config, title="Add feature")

        assert rerun["success"] is True
        assert rerun["rerun"] is True

    def test_provider_mismatch_fails_before_credential_resolution(
        self, pr_repo, monkeypatch
    ):
        config, wid, _wt_path, _ = pr_repo
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        record.pr = tracking.PRRecord(
            state="open",
            branch="feature/existing",
            number=42,
            provider="github",
            repo="example/project",
        )
        tracking.save_record(record)
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("credentials must not be resolved")
            ),
        )

        result = pr_ops.create_pr(wid, config, title="Add feature")

        assert result["success"] is False
        assert "mismatched credentials" in result["error"]

    def test_creates_and_pushes_feature_branch(self, pr_repo):
        config, wid, wt_path, _remote_dir = pr_repo
        res = pr_ops.create_pr(wid, config, title="Add feature")

        assert res["success"] is True, res
        assert res["squashed"] is True
        assert "squashed 2 surviving commit(s) into one" in res["history_action"]
        assert res["state"] == "open"
        assert res["branch"] == "feature/add-feature-aaaa"
        assert res["provider"] == "gitea"
        assert res["head_sha"]

        # HEAD is returned to the worktree base branch (#1804), not left
        # stranded on the throwaway feature branch.
        head = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path)
        assert head == f"worktree/{wid}"

        # Feature branch is on the remote
        assert git_ops.remote_branch_exists(
            "origin", "feature/add-feature-aaaa", cwd=str(wt_path)
        )

        # The local worktree lands on the squashed commit: worktree/<id> is NOT
        # reset to upstream -- it sits exactly one commit ahead of master (the
        # squashed work) and HEAD stays on it. Only the PUBLISH differs from
        # refspec (a feature/ branch vs a pr/ refspec).
        ahead_wt = git_ops.get_commits_ahead(
            f"worktree/{wid}", "origin/master", cwd=str(wt_path)
        )
        assert len(ahead_wt) == 1

        # The snapshot feature branch is created locally at that same commit.
        assert git_ops.local_branch_exists(
            "feature/add-feature-aaaa", cwd=str(wt_path)
        )
        assert _git("rev-parse", f"worktree/{wid}", cwd=wt_path) == \
            _git("rev-parse", "feature/add-feature-aaaa", cwd=wt_path)

        # Feature branch is exactly one commit ahead of master (squashed)
        ahead = git_ops.get_commits_ahead(
            "feature/add-feature-aaaa", "origin/master", cwd=str(wt_path)
        )
        assert len(ahead) == 1

    def test_records_pr_state_in_tracking(self, pr_repo):
        config, wid, _wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr is not None
        assert rec.pr.state == "open"
        assert rec.pr.branch == "feature/add-feature-aaaa"
        assert rec.pr.provider == "gitea"

    def test_idempotent_rerun(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"]
        # A successful create-pr leaves HEAD on the worktree branch at the
        # squashed commit (#1804); worktree/<id> sits 1 ahead of master.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == \
            f"worktree/{wid}"
        first_head = _git("rev-parse", f"worktree/{wid}", cwd=wt_path)
        # Re-run from that position: the live PR + its feature branch are reused
        # and the head is re-squashed + force-pushed cleanly (no "already
        # exists" guard, no duplicate PR).
        second = pr_ops.create_pr(wid, config, title="Add feature")
        assert second["success"] is True, second
        assert "error" not in second
        assert second["branch"] == "feature/add-feature-aaaa"
        # Still on the worktree branch, still at the same squashed commit.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == \
            f"worktree/{wid}"
        assert _git("rev-parse", f"worktree/{wid}", cwd=wt_path) == first_head
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert len(rec.prs) == 1

    def test_reused_open_pr_keeps_incremental_commits_unsquashed(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"], first
        original_pr_head = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)

        anchor = Path(config.repos["ext"].anchor)
        _git("checkout", "master", cwd=anchor)
        (anchor / "upstream.txt").write_text("unrelated upstream advance\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "advance upstream", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback 1\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback 1", cwd=wt_path)
        (wt_path / "d.txt").write_text("feedback 2\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback 2", cwd=wt_path)

        rerun = pr_ops.create_pr(wid, config, title="Add feature")

        assert rerun["success"] is True, rerun
        assert rerun["rerun"] is True
        assert rerun["squashed"] is False
        assert "without re-squashing" in rerun["history_action"]
        assert "without rebasing it onto newer upstream" in rerun["history_action"]
        ahead = git_ops.get_commits_ahead(
            "origin/feature/add-feature-aaaa", "origin/master", cwd=str(wt_path)
        )
        assert len(ahead) == 3
        assert _git(
            "merge-base", original_pr_head, "origin/feature/add-feature-aaaa",
            cwd=wt_path,
        ) == original_pr_head
        subjects = _git(
            "log", "--format=%s", "-n", "3", "origin/feature/add-feature-aaaa",
            cwd=wt_path,
        ).splitlines()
        assert subjects == ["address feedback 2", "address feedback 1", "Add feature"]

    def test_reused_open_pr_with_empty_base_sha_still_records_patch_id(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"], first

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.base_sha = ""
        tracking.save_record(rec)

        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        rerun = pr_ops.create_pr(wid, config, title="Add feature")

        assert rerun["success"] is True, rerun
        assert rerun["rerun"] is True
        assert rerun["patch_id"]
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.patch_id

    def test_reused_open_pr_refuses_reuse_push_with_no_persisted_head_sha(self, pr_repo):
        """#5298 follow-up: a legacy/manually-registered (`set-pr`) record can
        have no persisted `head_sha` at all. Falling back to a plain bool
        `--force-with-lease` there would adopt whatever this call's own
        just-completed fetch recorded as the remote tip and force past it --
        the same live-requery flaw the reuse-lease guard exists to close,
        just one step removed. Refuse instead of guessing."""
        config, wid, wt_path, _ = pr_repo
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"], first

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.head_sha = ""
        tracking.save_record(rec)

        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        rerun = pr_ops.create_pr(wid, config, title="Add feature")

        assert rerun["success"] is False
        assert "No persisted expected tip" in rerun["error"]

    def test_reused_open_pr_refuses_to_overwrite_divergent_remote_head(self, pr_repo):
        config, wid, wt_path, remote_dir = pr_repo
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"], first

        other = remote_dir.parent / "other-clone"
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

        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "local.txt").write_text("local feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "local update", cwd=wt_path)

        rerun = pr_ops.create_pr(wid, config, title="Add feature")

        assert rerun["success"] is False
        assert "another actor updating the remote branch" in rerun["error"]
        assert _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path) == remote_head

    def test_reused_open_pr_refuses_to_resurrect_deleted_remote_head(self, pr_repo, monkeypatch):
        """#5298: a concurrently merged+auto-pruned PR branch must not be
        silently recreated by a later create-pr call that still believes the
        PR is open. A plain push would read "ref absent" as "create a new
        branch" and happily resurrect it, reporting the merged PR as freshly
        updated -- this must fail instead, leaving the branch deleted.

        The earlier "second line of defense" (#1984) `remote_branch_state`
        preflight would otherwise detect the same deletion first and mark the
        PR terminal before the reuse-lease push is ever reached, making this
        regression pass for the wrong reason. Patch that preflight to report
        "present" so the test actually exercises the lease guard this PR adds,
        with the real deletion still in place for the push itself to hit.
        """
        config, wid, wt_path, remote_dir = pr_repo
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"], first

        # Simulate an external merge + auto-prune: the PR branch is deleted
        # from the bare remote directly (as a host does on merge), while this
        # worktree's own tracking record still believes the PR is open.
        _git(
            "push", "origin", "--delete", "feature/add-feature-aaaa",
            cwd=wt_path,
        )
        monkeypatch.setattr(pr_ops.git_ops, "remote_branch_state", lambda *a, **k: "present")

        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "local.txt").write_text("local feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "local update", cwd=wt_path)

        rerun = pr_ops.create_pr(wid, config, title="Add feature")

        assert rerun["success"] is False
        ls_remote = _git(
            "ls-remote", "--heads", "origin", "feature/add-feature-aaaa",
            cwd=wt_path,
        )
        assert ls_remote == ""  # still deleted, not resurrected

    def test_branch_collision_error_suggests_explicit_distinguishing_suffix(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        _git("branch", "feature/add-feature-aaaa", cwd=wt_path)

        res = pr_ops.create_pr(wid, config, title="Add feature")

        assert res["success"] is False
        assert (
            "Feature branch 'feature/add-feature-aaaa' already exists locally or on 'origin'."
        ) in res["error"]
        assert "--topic <token>" in res["error"]
        assert "--branch" in res["error"]
        assert "'feature/add-feature-aaaa-2'" in res["error"]
        assert "mini-task" in res["error"]

    def test_topic_extends_generated_default_branch_name(self, pr_repo):
        config, wid, wt_path, _remote_dir = pr_repo
        res = pr_ops.create_pr(wid, config, title="Add feature", topic="Hot Fix!!")

        assert res["success"] is True, res
        assert res["branch"] == "feature/add-feature-hot-fix-aaaa"
        assert git_ops.remote_branch_exists(
            "origin", "feature/add-feature-hot-fix-aaaa", cwd=str(wt_path)
        )

    def test_explicit_head_pattern_without_topic_slot_ignores_topic_with_note(self, pr_repo):
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = dataclasses.replace(
            config.repos["ext"],
            pr=cfg.PRConfig(
                enabled=True,
                provider="gitea",
                head_scheme="snapshot",
                branch_prefix="feature",
                head_pattern="submit/{slug}-{suffix}",
            ),
        )
        config = dataclasses.replace(config, repos={"ext": repo})

        res = pr_ops.create_pr(wid, config, title="Add feature", topic="Hot Fix")

        assert res["success"] is True, res
        assert res["branch"] == "submit/add-feature-aaaa"
        assert res["topic_note"] == (
            "Ignoring --topic because explicit pr.head_pattern does not "
            "reference {topic}."
        )

    def test_branch_override_wins_over_topic(self, pr_repo):
        config, wid, _wt_path, _ = pr_repo
        res = pr_ops.create_pr(
            wid,
            config,
            title="Add feature",
            branch="feature/manual-branch",
            topic="Ignored Topic",
            dry_run=True,
        )

        assert res["success"] is True, res
        assert res["branch"] == "feature/manual-branch"
        assert res["topic_note"] == (
            "Ignoring --topic because --branch fully overrides the head name."
        )

    def test_dirty_worktree_blocks(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        (wt_path / "dirty.txt").write_text("uncommitted\n")
        res = pr_ops.create_pr(wid, config, title="x")
        assert res["success"] is False
        assert "uncommitted" in res["error"]

    def test_dry_run_no_side_effects(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        res = pr_ops.create_pr(wid, config, title="Add feature", dry_run=True)
        assert res["success"] is True
        assert res["dry_run"] is True
        # Still on the worktree branch -- nothing happened
        head = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path)
        assert head == f"worktree/{wid}"

    def test_untitled_derives_title_from_commit_subject(self, pr_repo):
        """With no --title and an untitled record, create_pr derives the PR
        title (and persists the worktree title) from the newest commit subject
        instead of the opaque worktree_id -- so the worktree stops reading as
        "(untitled)" and the PR gets a meaningful name."""
        config, wid, _wt_path, _ = pr_repo
        # Fixture's newest worktree commit is "work 2".
        res = pr_ops.create_pr(wid, config)
        assert res["success"] is True, res
        # Branch slug comes from the derived title, not the worktree_id.
        assert res["branch"] == "feature/work-2-aaaa"
        assert wid not in res["branch"]
        # The derived title is persisted onto the worktree record.
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.title == "work 2"

    def test_explicit_title_not_overridden_by_commit(self, pr_repo):
        """An explicit --title always wins over the commit-subject fallback."""
        config, wid, _wt_path, _ = pr_repo
        res = pr_ops.create_pr(wid, config, title="Curated Title")
        assert res["success"] is True, res
        assert res["branch"] == "feature/curated-title-aaaa"
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.title == "Curated Title"

    def test_true_last_resort_requires_explicit_title(self, pr_repo, monkeypatch):
        """When there is no --title, no persisted title, AND no derivable
        commit-subject title (the actual last resort), create_pr must not
        invent one -- it embeds the authoring machine name and creation
        timestamp in worktree_id, and any synthetic placeholder could still
        leak into the PR branch name, the PR title, and the squash commit
        message on a public repo. Require the caller to supply --title
        instead."""
        config, wid, wt_path, _ = pr_repo
        monkeypatch.setattr(pr_ops, "_title_from_commits", lambda *a, **k: None)
        orig_head = _git("rev-parse", "HEAD", cwd=wt_path)

        res = pr_ops.create_pr(wid, config)

        assert res["success"] is False
        assert "title" in res["error"].lower()
        assert wid not in res["error"]
        # Nothing was mutated: no branch created, no commits touched.
        assert _git("rev-parse", "HEAD", cwd=wt_path) == orig_head
        rec_path = cfg.tracking_dir() / f"{wid}.yaml"
        if rec_path.exists():
            rec = tracking.load_record(rec_path)
            assert rec.pr is None

    def test_true_last_resort_succeeds_with_explicit_title(self, pr_repo, monkeypatch):
        """The same no-derivable-title scenario succeeds once the caller
        supplies an explicit --title."""
        config, wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(pr_ops, "_title_from_commits", lambda *a, **k: None)
        res = pr_ops.create_pr(wid, config, title="Add feature")
        assert res["success"] is True, res
        assert res["branch"] == "feature/add-feature-aaaa"

    def test_whitespace_only_title_does_not_bypass_the_requirement(
        self, pr_repo, monkeypatch,
    ):
        """A whitespace-only --title is not a real title -- it must not slip
        past the requirement and reach branch generation or the squash
        message as a blank string. The error must also correctly describe a
        whitespace-only input, not just an omitted one."""
        config, wid, wt_path, _ = pr_repo
        monkeypatch.setattr(pr_ops, "_title_from_commits", lambda *a, **k: None)
        orig_head = _git("rev-parse", "HEAD", cwd=wt_path)

        res = pr_ops.create_pr(wid, config, title="   \n\t  ")

        assert res["success"] is False
        assert "title" in res["error"].lower()
        assert "no --title was given" not in res["error"].lower()
        assert _git("rev-parse", "HEAD", cwd=wt_path) == orig_head

    def test_control_characters_normalized_in_title_and_persisted_record(
        self, pr_repo,
    ):
        """A --title containing control characters (CR, tabs) must not
        survive raw into either the squash commit or the persisted tracking
        record -- both must reflect the same normalized value."""
        config, wid, wt_path, _ = pr_repo
        res = pr_ops.create_pr(wid, config, title="Fix\r\tthe\nbug")
        assert res["success"] is True, res
        subject = _git("log", "-1", "--format=%s", f"worktree/{wid}", cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert subject == rec.title
        assert "\r" not in subject and "\t" not in subject

    def test_whitespace_only_persisted_title_does_not_block_commit_derivation(
        self, pr_repo,
    ):
        """A stale, whitespace-only `record.title` (e.g. persisted by an
        older version of the tool) is truthy but not meaningful -- it must
        not skip the commit-subject derivation fallback the way a genuinely
        curated title would."""
        config, wid, _wt_path, _ = pr_repo
        yaml_path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(yaml_path)
        rec.title = "   "
        tracking.save_record(rec)

        res = pr_ops.create_pr(wid, config)

        assert res["success"] is True, res
        # Fixture's newest worktree commit is "work 2" -- the derivation
        # fallback ran and produced a real title, not a rejection.
        assert res["branch"] == "feature/work-2-aaaa"

    def test_whitespace_only_title_does_not_erase_persisted_title(
        self, pr_repo,
    ):
        """A whitespace-only --title must not overwrite an existing,
        genuinely curated persisted title with a blank one -- it is not a
        real update."""
        config, wid, wt_path, _ = pr_repo
        yaml_path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(yaml_path)
        rec.title = "A real curated title"
        tracking.save_record(rec)

        res = pr_ops.create_pr(wid, config, title="   \n\t  ")

        assert res["success"] is True, res
        rec_after = tracking.load_record(yaml_path)
        assert rec_after.title == "A real curated title"
        subject = _git("log", "-1", "--format=%s", f"worktree/{wid}", cwd=wt_path)
        assert subject == "A real curated title"

    def test_long_title_is_not_truncated_for_publication(self, pr_repo):
        """A long --title must reach the squash commit message verbatim --
        the mux/Picker display cap (tracking.TITLE_MAX) is a UI concern for
        the status bar, not a limit on the actual PR/commit title."""
        config, wid, wt_path, _ = pr_repo
        long_title = "A" * (tracking.TITLE_MAX + 20)
        res = pr_ops.create_pr(wid, config, title=long_title)
        assert res["success"] is True, res
        subject = _git("log", "-1", "--format=%s", f"worktree/{wid}", cwd=wt_path)
        assert subject == long_title
