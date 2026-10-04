"""Tests for ``_pr_watch_review_blocking`` -- the role/policy-aware default
that decides whether ``pr-watch wait`` waits for a real APPROVED/
CHANGES_REQUESTED verdict or for a bare COMMENT (see ``pr_contract.
default_until`` / ``effective_verdict``'s ``review_blocking`` parameter).

GitHub's Copilot code-review app cannot render a binding verdict on a
``pr-self-merge`` repo's owner-authored PR -- it only ever submits a
``COMMENT`` review. Waiting for ``approved``/``changes_requested`` there never
resolves. A maintainer (live merge authority) should wait on ``commented``
instead; a contributor without merge authority still needs a human
maintainer's real verdict, even on the same repo.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import pr_contract as pc


def _repo_config(**pr_kwargs) -> cfg.RepoConfig:
    prc = cfg.PRConfig(enabled=True, required=True, **pr_kwargs)
    return cfg.RepoConfig(anchor="/anchor", worktree_root="/wt", pr=prc)


def _args(repo="o/r", host="", token=None):
    return SimpleNamespace(repo=repo, host=host, token=token)


class TestPrWatchReviewBlocking:
    def test_blocking_repo_stays_blocking_no_network(self, monkeypatch):
        """A repo with review_blocking=True never needs a live permission
        read -- the base config already settles it."""
        repo = _repo_config(review_blocking=True)
        config = SimpleNamespace(default_repo=repo)
        called = MagicMock()
        monkeypatch.setattr("agent_worktrees.providers.get_provider", called)
        assert m._pr_watch_review_blocking(config, _args()) is True
        called.assert_not_called()

    def test_non_self_merge_repo_stays_non_blocking_no_network(self, monkeypatch):
        """An ordinary human-merge flow preserves its explicit non-blocking
        review policy without a live permission read."""
        repo = _repo_config(review_blocking=False)
        config = SimpleNamespace(default_repo=repo)
        called = MagicMock()
        monkeypatch.setattr("agent_worktrees.providers.get_provider", called)
        assert m._pr_watch_review_blocking(config, _args()) is False
        called.assert_not_called()

    def test_conservative_base_maintain_override_keeps_actor_postures(
        self, monkeypatch,
    ):
        """A conservative human-merge base blocks Write actors on approval,
        while its Maintain override gets the configured non-blocking
        submitter-direct wait."""
        repo = _repo_config(
            review_blocking=True,
            provider="github",
            roles={
                "maintain": cfg.PRRoleOverride(
                    merge_actor="submitter-direct",
                    review_blocking=False,
                ),
            },
        )
        config = SimpleNamespace(default_repo=repo)
        permission = {"value": "write"}
        monkeypatch.setattr(
            "agent_worktrees.providers.actor_viewer_permission",
            lambda *a, **k: permission["value"],
        )
        assert m._pr_watch_review_blocking(config, _args()) is True

        permission["value"] = "maintain"
        assert m._pr_watch_review_blocking(config, _args()) is False

    def test_self_merge_maintainer_stays_non_blocking(self, monkeypatch):
        """Live authority True (write/maintain/admin): the maintainer waits
        for the bare comment, matching the repo's own non-blocking policy."""
        repo = _repo_config(review_blocking=False, self_approve=True, provider="github")
        config = SimpleNamespace(default_repo=repo)
        monkeypatch.setattr(
            "agent_worktrees.providers.actor_viewer_permission",
            lambda *a, **k: "write",
        )
        assert m._pr_watch_review_blocking(config, _args()) is False

    def test_self_merge_contributor_falls_back_to_blocking(self, monkeypatch):
        """Live authority False (a confident read-only/no-access read): a
        contributor's PR must still wait for a real human verdict, even
        though the repo config marks the review non-blocking for the owner."""
        repo = _repo_config(review_blocking=False, self_approve=True, provider="github")
        config = SimpleNamespace(default_repo=repo)
        monkeypatch.setattr(
            "agent_worktrees.providers.actor_viewer_permission",
            lambda *a, **k: "read",
        )
        assert m._pr_watch_review_blocking(config, _args()) is True

    @pytest.mark.parametrize("review_blocking", [False, True])
    def test_explicit_write_role_review_policy_is_honored(
        self, monkeypatch, review_blocking,
    ):
        """An explicit role that removes self-merge also owns its review
        posture; write authority alone must not rewrite that policy."""
        repo = _repo_config(
            review_blocking=False,
            merge_actor="submitter-direct",
            provider="github",
            roles={
                "write": cfg.PRRoleOverride(
                    merge_actor="",
                    review_blocking=review_blocking,
                ),
            },
        )
        config = SimpleNamespace(default_repo=repo)
        monkeypatch.setattr(
            "agent_worktrees.providers.actor_viewer_permission",
            lambda *a, **k: "write",
        )
        assert m._pr_watch_review_blocking(config, _args()) is review_blocking

    def test_self_merge_unknown_authority_fails_open_to_non_blocking(self, monkeypatch):
        """An unknown/failed live read (None) must never deny a legitimate
        maintainer -- fails open exactly like ``_pr_merge_now``."""
        repo = _repo_config(review_blocking=False, self_approve=True, provider="github")
        config = SimpleNamespace(default_repo=repo)
        monkeypatch.setattr(
            "agent_worktrees.providers.actor_viewer_permission",
            lambda *a, **k: "",
        )
        assert m._pr_watch_review_blocking(config, _args()) is False

    def test_self_merge_provider_read_failure_fails_open(self, monkeypatch):
        repo = _repo_config(review_blocking=False, self_approve=True, provider="github")
        config = SimpleNamespace(default_repo=repo)

        def _boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("agent_worktrees.providers.get_provider", _boom)
        assert m._pr_watch_review_blocking(config, _args()) is False


class TestPrWatchUntilDefaultIntegration:
    """``cmd_pr_watch_dispatch``'s ``--until`` resolves through
    :func:`pr_contract.default_until` using the review-blocking posture."""

    @pytest.mark.parametrize(
        "review_blocking,expected",
        [(True, pc.DEFAULT_UNTIL), (False, pc.NONBLOCKING_DEFAULT_UNTIL)],
    )
    def test_default_until_matches_posture(self, review_blocking, expected):
        assert pc.default_until(review_blocking) == expected


class TestPrWatchSelfMergeNoteToken:
    """The post-wait self-merge-bypass read `cmd_pr_watch_dispatch` performs
    must fall back to the repo-scoped account token
    (`providers.account_token_for_slug`) when no explicit `--token` was
    supplied, matching every other live read on this path -- never leave it
    on the raw (commonly `None`) CLI override, which would otherwise leave
    `pull_review_gate` falling back to the ambient `gh` identity instead of
    the resolved account. It must also stay silent for a terminal
    (merged/closed), WIP, or held snapshot, mirroring `_live_pr_state`'s own
    suppression -- there is nothing left to bypass toward."""

    def _dispatch(self, monkeypatch, *, cli_token=None, merge_extra=None,
                  note_return=None, json_mode=True):
        from types import SimpleNamespace as _NS
        from unittest.mock import MagicMock as _Mock

        from agent_worktrees import config as acfg
        from agent_worktrees import pr_cli
        from agent_worktrees import pr_config
        from agent_worktrees import pr_ops
        from agent_worktrees import pr_watch as prw
        from agent_worktrees import providers as prov
        from agent_worktrees import worktree_identity

        monkeypatch.setattr(
            worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: "wt-1"
        )
        repo = _repo_config(review_blocking=False, provider="github")
        config = acfg.Config(
            srcroot="/src", machine="host", platform="linux",
            repo_name="repo", repos={"repo": repo},
        )
        monkeypatch.setattr(acfg, "load_config", lambda *a, **k: config)
        monkeypatch.setattr(
            pr_config,
            "resolve_repo_config_for_slug",
            lambda cfg_, slug: pr_config.ForeignRepoResolution(repo, "repo"),
        )
        flow = pc.classify_pr_flow(enabled=True, required=True, provider="github",
                                   automerge_label="", self_approve=True,
                                   reviewer="copilot", review_blocking=False)
        monkeypatch.setattr(
            pr_config,
            "resolve_actor_pr_flow",
            lambda repo_cfg, slug, **k: pr_config.ActorPRFlow(
                pr_config=repo_cfg.pr, configured_flow=flow, flow=flow,
            ),
        )
        monkeypatch.setattr(pr_cli, "_pr_watch_review_blocking", lambda *a, **k: False)
        monkeypatch.setattr(
            pr_cli, "_tracked_pr_head_evidence", lambda *a, **k: ("", "")
        )
        monkeypatch.setattr(prw, "build_fetch", lambda *a, **k: (lambda: None))
        payload = {"repo": "o/r", "pr": 7, "events": [], "transitions": []}
        if merge_extra is not None:
            payload["merge"] = merge_extra
        monkeypatch.setattr(prw, "run_wait", lambda **k: prw.WaitResult(True, payload))
        monkeypatch.setattr(
            prov, "account_token_for_slug", lambda slug, prcfg: "repo-scoped-tok"
        )
        monkeypatch.setattr(prov, "get_provider", lambda name: _NS(name=name))
        note_calls = _Mock(return_value=note_return)
        monkeypatch.setattr(pr_ops, "self_merge_bypass_note", note_calls)

        argv = ["wait", "o/r", "7"]
        if json_mode:
            argv += ["--json"]
        if cli_token is not None:
            argv += ["--token", cli_token]
        rc = pr_cli.cmd_pr_watch_dispatch(argv)
        return rc, note_calls

    def test_uses_the_repo_scoped_token_not_the_raw_cli_override(self, monkeypatch):
        rc, note_calls = self._dispatch(monkeypatch)
        assert rc == 0
        assert note_calls.call_count == 1
        _args_call, kwargs_call = note_calls.call_args
        assert kwargs_call["token"] == "repo-scoped-tok"

    def test_preserves_explicit_cli_token_override(self, monkeypatch):
        rc, note_calls = self._dispatch(monkeypatch, cli_token="cli-tok")
        assert rc == 0
        assert note_calls.call_count == 1
        _args_call, kwargs_call = note_calls.call_args
        assert kwargs_call["token"] == "cli-tok"

    def test_suppresses_note_for_a_merged_pr(self, monkeypatch):
        rc, note_calls = self._dispatch(
            monkeypatch, merge_extra={"merge_state": "merged", "wip": False, "held": []}
        )
        assert rc == 0
        assert note_calls.call_count == 0

    def test_suppresses_note_for_a_wip_pr(self, monkeypatch):
        rc, note_calls = self._dispatch(
            monkeypatch, merge_extra={"merge_state": "clean", "wip": True, "held": []}
        )
        assert rc == 0
        assert note_calls.call_count == 0

    def test_suppresses_note_for_a_held_pr(self, monkeypatch):
        rc, note_calls = self._dispatch(
            monkeypatch, merge_extra={"merge_state": "clean", "wip": False, "held": ["hold:x"]}
        )
        assert rc == 0
        assert note_calls.call_count == 0

    def test_json_output_includes_a_returned_note(self, monkeypatch, capsys):
        rc, note_calls = self._dispatch(monkeypatch, note_return="bypass available")
        assert rc == 0
        assert note_calls.call_count == 1
        out = capsys.readouterr().out
        payload = json.loads(out.strip().splitlines()[-1])
        assert payload["self_merge_note"] == "bypass available"

    def test_non_json_mode_prints_a_returned_note_to_stderr(self, monkeypatch, capsys):
        rc, note_calls = self._dispatch(
            monkeypatch, note_return="bypass available", json_mode=False
        )
        assert rc == 0
        assert note_calls.call_count == 1
        err = capsys.readouterr().err
        assert "bypass available" in err

