"""``create-pr``'s claimant-CWD guard and foreign-``--repo`` refusal.

Owning project is the CWD; a different, also-registered ``--repo`` can be
*recorded* on the PR but create-pr has no mechanism to push into it (no
local checkout) -- it must refuse rather than silently pushing/opening
against the CALLER's own repo while mislabeling the PR as the foreign one.
"""

from __future__ import annotations

import agent_worktrees.__main__ as m
from agent_worktrees import pr_config
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
        monkeypatch.setattr(m, "_resolve_worktree_id", lambda candidate: candidate)

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
        monkeypatch.setattr(m, "_infer_worktree_id", lambda candidate, _config: candidate)
        monkeypatch.setattr(m, "_resolve_worktree_id", lambda candidate: candidate)

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
        monkeypatch.setattr(m, "_infer_worktree_id", lambda candidate, _config: wid)
        monkeypatch.setattr(m, "_resolve_worktree_id", lambda candidate: candidate)
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
        monkeypatch.setattr(m, "_infer_worktree_id", lambda candidate, _config: wid)
        monkeypatch.setattr(m, "_resolve_worktree_id", lambda candidate: candidate)
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
