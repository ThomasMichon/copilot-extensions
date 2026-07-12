"""Wiring test: RepoConfig -> _pr_flow_profile -> profile token.

Confirms the ``get pr-profile`` / ``pr-status`` / ``pr-merge`` surfaces read
the same profile off a repo's ``pr`` binding.
"""

from __future__ import annotations

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import pr_contract as pc


def _repo(**pr_kwargs) -> cfg.RepoConfig:
    return cfg.RepoConfig(
        anchor="/tmp/anchor",
        worktree_root="/tmp/wt",
        pr=cfg.PRConfig(**pr_kwargs),
    )


def test_direct_profile_from_disabled_pr():
    prof = m._pr_flow_profile(_repo(enabled=False))
    assert prof.profile == pc.PROFILE_DIRECT


def test_agent_merge_profile_from_bound_label():
    prof = m._pr_flow_profile(
        _repo(enabled=True, required=True, provider="gitea",
              automerge_label="auto-merge")
    )
    assert prof.profile == pc.PROFILE_PR_AGENT_MERGE
    assert prof.applies("pr-merge") is True


def test_human_merge_profile_when_no_label():
    prof = m._pr_flow_profile(
        _repo(enabled=True, required=True, provider="github")
    )
    assert prof.profile == pc.PROFILE_PR_HUMAN_MERGE
    assert prof.applies("pr-merge") is False
