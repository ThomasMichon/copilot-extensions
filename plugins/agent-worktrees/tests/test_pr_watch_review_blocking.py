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
        assert m._pr_watch_review_blocking(config, repo.pr, _args()) is True
        called.assert_not_called()

    def test_non_self_merge_repo_stays_non_blocking_no_network(self, monkeypatch):
        """review_blocking=False but not a pr-self-merge profile (no
        self_approve/merge_actor): nothing to gate by actor authority."""
        repo = _repo_config(review_blocking=False)
        config = SimpleNamespace(default_repo=repo)
        called = MagicMock()
        monkeypatch.setattr("agent_worktrees.providers.get_provider", called)
        assert m._pr_watch_review_blocking(config, repo.pr, _args()) is False
        called.assert_not_called()

    def test_self_merge_maintainer_stays_non_blocking(self, monkeypatch):
        """Live authority True (write/maintain/admin): the maintainer waits
        for the bare comment, matching the repo's own non-blocking policy."""
        repo = _repo_config(review_blocking=False, self_approve=True, provider="github")
        config = SimpleNamespace(default_repo=repo)
        monkeypatch.setattr(
            "agent_worktrees.providers.actor_viewer_permission",
            lambda *a, **k: "write",
        )
        assert m._pr_watch_review_blocking(config, repo.pr, _args()) is False

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
        assert m._pr_watch_review_blocking(config, repo.pr, _args()) is True

    def test_self_merge_unknown_authority_fails_open_to_non_blocking(self, monkeypatch):
        """An unknown/failed live read (None) must never deny a legitimate
        maintainer -- fails open exactly like ``_pr_merge_now``."""
        repo = _repo_config(review_blocking=False, self_approve=True, provider="github")
        config = SimpleNamespace(default_repo=repo)
        monkeypatch.setattr(
            "agent_worktrees.providers.actor_viewer_permission",
            lambda *a, **k: "",
        )
        assert m._pr_watch_review_blocking(config, repo.pr, _args()) is False

    def test_self_merge_provider_read_failure_fails_open(self, monkeypatch):
        repo = _repo_config(review_blocking=False, self_approve=True, provider="github")
        config = SimpleNamespace(default_repo=repo)

        def _boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("agent_worktrees.providers.get_provider", _boom)
        assert m._pr_watch_review_blocking(config, repo.pr, _args()) is False


class TestPrWatchUntilDefaultIntegration:
    """``cmd_pr_watch_dispatch``'s ``--until`` resolves through
    :func:`pr_contract.default_until` using the review-blocking posture."""

    @pytest.mark.parametrize(
        "review_blocking,expected",
        [(True, pc.DEFAULT_UNTIL), (False, pc.NONBLOCKING_DEFAULT_UNTIL)],
    )
    def test_default_until_matches_posture(self, review_blocking, expected):
        assert pc.default_until(review_blocking) == expected
