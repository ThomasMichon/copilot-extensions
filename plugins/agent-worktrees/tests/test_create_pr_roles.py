"""Role-resolution integration tests for create-pr."""

from __future__ import annotations

from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops


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

