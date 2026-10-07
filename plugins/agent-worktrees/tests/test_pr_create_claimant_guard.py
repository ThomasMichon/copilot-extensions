"""``create-pr``'s claimant-CWD guard and foreign-``--repo`` refusal.

Owning project is the CWD; a different, also-registered ``--repo`` can be
*recorded* on the PR but create-pr has no mechanism to push into it (no
local checkout) -- it must refuse rather than silently pushing/opening
against the CALLER's own repo while mislabeling the PR as the foreign one.
"""

from __future__ import annotations

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import pr_config, pr_foreign_create
from agent_worktrees import worktree_identity


def _args(argv):
    return m.build_parser().parse_args(argv)


class TestCreatePrClaimantGuard:
    def test_refuses_when_no_worktree_id_and_cwd_has_no_claimant(
        self, pr_repo, monkeypatch,
    ):
        config, _wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: None
        )
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda config=None: None)

        called = {"create_pr": False}
        monkeypatch.setattr(
            m.pr_ops, "create_pr",
            lambda *a, **k: called.__setitem__("create_pr", True) or {},
        )

        args = _args(["create-pr", "--title", "x"])
        rc = m.cmd_create_pr(args)

        assert rc == 2
        assert called["create_pr"] is False

    def test_proceeds_when_cwd_resolves_to_a_claimant(self, pr_repo, monkeypatch):
        config, wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: wid
        )
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda config=None: wid)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)

        captured = {}

        def _fake_create_pr(worktree_id, _config, **kwargs):
            captured["worktree_id"] = worktree_id
            return {
                "success": True, "branch": "b", "remote": "origin",
                "provider": "gitea", "base_sha": "a" * 40, "head_sha": "b" * 40,
                "draft": False,
            }

        monkeypatch.setattr(m.pr_ops, "create_pr", _fake_create_pr)

        args = _args(["create-pr", "--title", "x"])
        rc = m.cmd_create_pr(args)

        assert rc == 0
        assert captured["worktree_id"] == wid

    def test_explicit_worktree_id_bypasses_the_cwd_claimant_check(
        self, pr_repo, monkeypatch,
    ):
        """An explicit worktree_id positional is its own sanctioned pattern
        (operate on a named worktree without cd-ing into it) and is not
        re-validated against CWD."""
        config, wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: None
        )
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: candidate)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)

        monkeypatch.setattr(
            m.pr_ops, "create_pr",
            lambda *a, **k: {
                "success": True, "branch": "b", "remote": "origin",
                "provider": "gitea", "base_sha": "a" * 40, "head_sha": "b" * 40,
                "draft": False,
            },
        )

        args = _args(["create-pr", wid, "--title", "x"])
        rc = m.cmd_create_pr(args)

        assert rc == 0


class TestCreatePrForeignRepoRefusal:
    def test_refuses_a_different_registered_repo_without_a_push_mechanism(
        self, pr_repo, monkeypatch,
    ):
        config, wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: wid)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)
        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: wid
        )
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda cfg_, slug: pr_config.ForeignRepoResolution(
                config.repos["ext"], "other-repo", same_as_active=False
            ),
        )

        called = {"create_pr": False}
        monkeypatch.setattr(
            m.pr_ops, "create_pr",
            lambda *a, **k: called.__setitem__("create_pr", True) or {},
        )

        args = _args(["create-pr", wid, "--repo", "owner/other-repo", "--title", "x"])
        rc = m.cmd_create_pr(args)

        assert rc == 2
        assert called["create_pr"] is False

    def test_same_active_repo_is_not_refused(self, pr_repo, monkeypatch):
        config, wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: wid)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)
        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: wid
        )
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda cfg_, slug: pr_config.ForeignRepoResolution(
                config.repos["ext"], "ext", same_as_active=True
            ),
        )

        monkeypatch.setattr(
            m.pr_ops, "create_pr",
            lambda *a, **k: {
                "success": True, "branch": "b", "remote": "origin",
                "provider": "gitea", "base_sha": "a" * 40, "head_sha": "b" * 40,
                "draft": False,
            },
        )

        args = _args(["create-pr", wid, "--repo", "owner/ext", "--title", "x"])
        rc = m.cmd_create_pr(args)

        assert rc == 0


class TestCreatePrFromBranch:
    """``--repo <foreign> --from-branch <branch>`` dispatches to
    ``pr_foreign_create.create_foreign_pr_from_branch`` instead of refusing
    (Phase 2d of the ``pull-request-capability`` effort)."""

    def test_dispatches_to_foreign_create_and_reports_success(
        self, pr_repo, monkeypatch, capsys,
    ):
        config, wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: wid)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)
        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: wid
        )
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda cfg_, slug: pr_config.ForeignRepoResolution(
                config.repos["ext"], "other-repo", same_as_active=False
            ),
        )

        captured = {}

        def _fake_from_branch(worktree_id, _config, **kwargs):
            captured["worktree_id"] = worktree_id
            captured["kwargs"] = kwargs
            return {
                "repo": kwargs["target_repo"], "url": "https://example/pr/1",
                "number": 1, "state": "open", "pr_opened": True,
                "head": kwargs["from_branch"], "base": "dev", "claimed": True,
            }

        monkeypatch.setattr(
            pr_foreign_create, "create_foreign_pr_from_branch", _fake_from_branch,
        )
        called = {"create_pr": False}
        monkeypatch.setattr(
            m.pr_ops, "create_pr",
            lambda *a, **k: called.__setitem__("create_pr", True) or {},
        )

        args = _args([
            "create-pr", wid, "--repo", "owner/other-repo",
            "--from-branch", "user/topic-wt", "--title", "Fix thing",
        ])
        rc = m.cmd_create_pr(args)

        assert rc == 0
        assert called["create_pr"] is False  # the local-checkout path never ran
        assert captured["worktree_id"] == wid
        assert captured["kwargs"]["target_repo"] == "owner/other-repo"
        assert captured["kwargs"]["from_branch"] == "user/topic-wt"
        out = capsys.readouterr().out
        assert "https://example/pr/1" in out

    def test_reports_the_claim_warning_when_unclaimed(self, pr_repo, monkeypatch, capsys):
        config, wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: wid)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)
        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: wid
        )
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda cfg_, slug: pr_config.ForeignRepoResolution(
                config.repos["ext"], "other-repo", same_as_active=False
            ),
        )
        monkeypatch.setattr(
            pr_foreign_create, "create_foreign_pr_from_branch",
            lambda worktree_id, _config, **kwargs: {
                "repo": kwargs["target_repo"], "url": "https://example/pr/2",
                "number": 2, "pr_opened": True, "head": kwargs["from_branch"],
                "base": "dev", "claimed": False, "claim_warning": "no record",
            },
        )

        args = _args([
            "create-pr", wid, "--repo", "owner/other-repo",
            "--from-branch", "topic", "--title", "x",
        ])
        rc = m.cmd_create_pr(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "no record" in out

    def test_propagates_a_foreign_create_error(self, pr_repo, monkeypatch):
        config, wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: wid)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)
        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: wid
        )
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda cfg_, slug: pr_config.ForeignRepoResolution(
                config.repos["ext"], "other-repo", same_as_active=False
            ),
        )
        monkeypatch.setattr(
            pr_foreign_create, "create_foreign_pr_from_branch",
            lambda worktree_id, _config, **kwargs: {"error": "provider exploded"},
        )

        args = _args([
            "create-pr", wid, "--repo", "owner/other-repo",
            "--from-branch", "topic", "--title", "x",
        ])
        rc = m.cmd_create_pr(args)

        assert rc == 2

    def test_without_from_branch_still_refuses_with_updated_guidance(
        self, pr_repo, monkeypatch,
    ):
        """Unchanged refusal for the plain foreign --repo case (no
        --from-branch) -- now also mentions the new escape hatch."""
        config, wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: wid)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)
        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: wid
        )
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda cfg_, slug: pr_config.ForeignRepoResolution(
                config.repos["ext"], "other-repo", same_as_active=False
            ),
        )

        args = _args(["create-pr", wid, "--repo", "owner/other-repo", "--title", "x"])
        rc = m.cmd_create_pr(args)

        assert rc == 2


    def test_from_branch_without_a_resolved_foreign_repo_is_refused(
        self, pr_repo, monkeypatch,
    ):
        """--from-branch with no --repo (or an unresolved/active one) has no
        meaning for the local-checkout path and must never silently fall
        through to it."""
        config, wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: wid)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)
        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: wid
        )
        called = {"create_pr": False, "foreign": False}
        monkeypatch.setattr(
            m.pr_ops, "create_pr",
            lambda *a, **k: called.__setitem__("create_pr", True) or {},
        )
        monkeypatch.setattr(
            pr_foreign_create, "create_foreign_pr_from_branch",
            lambda *a, **k: called.__setitem__("foreign", True) or {},
        )

        args = _args(["create-pr", wid, "--from-branch", "topic", "--title", "x"])
        rc = m.cmd_create_pr(args)

        assert rc == 2
        assert called["create_pr"] is False
        assert called["foreign"] is False

    def test_from_branch_rejects_dry_run(self, pr_repo, monkeypatch):
        config, wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: wid)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)
        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: wid
        )
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda cfg_, slug: pr_config.ForeignRepoResolution(
                config.repos["ext"], "other-repo", same_as_active=False
            ),
        )
        called = {"foreign": False}
        monkeypatch.setattr(
            pr_foreign_create, "create_foreign_pr_from_branch",
            lambda *a, **k: called.__setitem__("foreign", True) or {},
        )

        args = _args([
            "create-pr", wid, "--repo", "owner/other-repo",
            "--from-branch", "topic", "--title", "x", "--dry-run",
        ])
        rc = m.cmd_create_pr(args)

        assert rc == 2
        assert called["foreign"] is False

    def test_from_branch_requires_a_non_blank_title(self, pr_repo, monkeypatch):
        config, wid, _wt_path, _ = pr_repo
        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: wid)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)
        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: wid
        )
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda cfg_, slug: pr_config.ForeignRepoResolution(
                config.repos["ext"], "other-repo", same_as_active=False
            ),
        )
        called = {"foreign": False}
        monkeypatch.setattr(
            pr_foreign_create, "create_foreign_pr_from_branch",
            lambda *a, **k: called.__setitem__("foreign", True) or {},
        )

        args = _args([
            "create-pr", wid, "--repo", "owner/other-repo",
            "--from-branch", "topic", "--title", "   ",
        ])
        rc = m.cmd_create_pr(args)

        assert rc == 2
        assert called["foreign"] is False



_PUSHED = {"success": True, "branch": "b", "remote": "fork", "provider": "github",
           "base_sha": "a" * 40, "head_sha": "b" * 40, "draft": False,
           "pr_open_skipped": "--no-open"}


@pytest.mark.parametrize("result, state, headline", [
    (_PUSHED, "pushed", "no PR opened: the branch was pushed (--no-open)"),
    ({**_PUSHED, "pr_open_skipped": None, "dry_run": True}, "dry-run",
     "dry run: nothing was pushed or opened"),
    ({**_PUSHED, "pr_open_skipped": None, "pr_opened": False, "pr_open_error": "HTTP 422"}, "pushed",
     "no PR opened: the provider refused: HTTP 422"),
    ({**_PUSHED, "pr_open_skipped": None, "pr_opened": True, "number": 7,
      "url": "https://h/o/r/pull/7"}, "created", "PR created"),
])
def test_create_pr_json_reminder_says_created_only_when_a_pr_was_opened(
        pr_repo, monkeypatch, result, state, headline):
    """A pushed branch with no PR (here: --no-open) must not read "PR created" with a
    merge as the next step; an opened PR still does."""
    import json

    config, wid, _wt_path, _ = pr_repo
    monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
    monkeypatch.setattr(m.pr_ops, "create_pr", lambda *_a, **_k: dict(result))
    from agent_worktrees import output

    with output.capture_json_output() as buf:  # the envelope goes to sys.__stdout__
        assert m.cmd_create_pr(_args(["create-pr", wid, "--title", "x", "--json"])) == 0
    reminder = json.loads(buf.getvalue())["reminder"]
    assert (reminder["state"], reminder["headline"]) == (state, headline)


def test_a_dry_run_never_says_it_pushed(pr_repo, monkeypatch, capsys):
    """Human output for a preview: 'would push', never 'pushed', and no PR claimed."""
    config, wid, _wt_path, _ = pr_repo
    monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
    monkeypatch.setattr(m.pr_ops, "create_pr", lambda *_a, **_k: {**_PUSHED, "pr_open_skipped": None,
                                                                  "dry_run": True})
    assert m.cmd_create_pr(_args(["create-pr", wid, "--title", "x", "--dry-run"])) == 0
    out = capsys.readouterr()
    text = out.out + out.err
    assert "would push" in text and "pushed to" not in text

