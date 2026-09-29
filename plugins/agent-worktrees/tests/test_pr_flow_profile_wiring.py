"""Wiring test: RepoConfig -> _pr_flow_profile -> profile token.

Confirms the ``get pr-profile`` / ``pr-status`` / ``pr-merge`` surfaces read
the same profile off a repo's ``pr`` binding.
"""

from __future__ import annotations

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import pr_config
from agent_worktrees import pr_contract as pc
from agent_worktrees import providers


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


def test_self_merge_profile_from_self_approve():
    prof = m._pr_flow_profile(
        _repo(enabled=True, required=True, provider="github",
              self_approve=True, reviewer="copilot", review_blocking=False)
    )
    assert prof.profile == pc.PROFILE_PR_SELF_MERGE
    assert prof.applies("pr-merge") is True


def test_self_merge_profile_from_merge_actor():
    prof = m._pr_flow_profile(
        _repo(enabled=True, required=True, provider="github",
              merge_actor="submitter-direct")
    )
    assert prof.profile == pc.PROFILE_PR_SELF_MERGE


def _permission(monkeypatch, value: str):
    monkeypatch.setattr(providers, "get_provider", lambda name: object())
    monkeypatch.setattr(
        providers,
        "actor_viewer_permission",
        lambda *args, **kwargs: value,
    )


def test_effective_actor_profile_applies_maintain_self_merge_override(monkeypatch):
    _permission(monkeypatch, "maintain")
    repo = _repo(
        enabled=True,
        required=True,
        provider="github",
        merge_actor="",
        roles={
            "maintain": cfg.PRRoleOverride(merge_actor="submitter-direct"),
        },
    )

    actor = pr_config.resolve_actor_pr_flow(repo, "o/r", token="tok")

    assert actor.configured_flow.profile == pc.PROFILE_PR_HUMAN_MERGE
    assert actor.flow.profile == pc.PROFILE_PR_SELF_MERGE
    assert actor.pr_config.merge_actor == "submitter-direct"
    assert actor.viewer_permission == "maintain"
    assert actor.resolution == "actor-role"


def test_effective_actor_profile_honors_explicit_write_non_self_merge(monkeypatch):
    _permission(monkeypatch, "write")
    repo = _repo(
        enabled=True,
        required=True,
        provider="github",
        merge_actor="submitter-direct",
        roles={"write": cfg.PRRoleOverride(merge_actor="")},
    )

    actor = pr_config.resolve_actor_pr_flow(repo, "o/r", token="tok")

    assert actor.configured_flow.profile == pc.PROFILE_PR_SELF_MERGE
    assert actor.flow.profile == pc.PROFILE_PR_HUMAN_MERGE
    assert actor.pr_config.merge_actor == ""
    assert actor.viewer_permission == "write"
    assert actor.resolution == "actor-role"


def test_effective_actor_profile_honors_explicit_read_non_self_merge(monkeypatch):
    _permission(monkeypatch, "read")
    repo = _repo(
        enabled=True,
        required=True,
        provider="github",
        merge_actor="submitter-direct",
        roles={"read": cfg.PRRoleOverride(merge_actor="")},
    )

    actor = pr_config.resolve_actor_pr_flow(repo, "o/r", token="tok")

    assert actor.flow.profile == pc.PROFILE_PR_HUMAN_MERGE
    assert actor.viewer_permission == "read"
    assert actor.resolution == "actor-role"


def test_unknown_permission_uses_conservative_base_profile(monkeypatch):
    _permission(monkeypatch, "")
    repo = _repo(
        enabled=True,
        required=True,
        provider="github",
        roles={
            "maintain": cfg.PRRoleOverride(merge_actor="submitter-direct"),
        },
    )

    actor = pr_config.resolve_actor_pr_flow(repo, "o/r", token="tok")

    assert actor.flow.profile == pc.PROFILE_PR_HUMAN_MERGE
    assert actor.pr_config is repo.pr
    assert actor.resolution == "configured-fallback"


def test_unknown_permission_preserves_historical_self_merge_fail_open(monkeypatch):
    _permission(monkeypatch, "")
    repo = _repo(
        enabled=True,
        required=True,
        provider="github",
        merge_actor="submitter-direct",
    )

    actor = pr_config.resolve_actor_pr_flow(repo, "o/r", token="tok")

    assert actor.flow.profile == pc.PROFILE_PR_SELF_MERGE
    assert actor.viewer_permission == ""
    assert actor.resolution == "configured-fallback"


def test_read_only_actor_demotes_unscoped_self_merge_profile(monkeypatch):
    _permission(monkeypatch, "read")
    repo = _repo(
        enabled=True,
        required=True,
        provider="github",
        merge_actor="submitter-direct",
    )

    actor = pr_config.resolve_actor_pr_flow(repo, "o/r", token="tok")

    assert actor.configured_flow.profile == pc.PROFILE_PR_SELF_MERGE
    assert actor.flow.profile == pc.PROFILE_PR_HUMAN_MERGE
    assert actor.resolution == "actor-authority"
    assert pr_config.actor_review_blocking(actor) is True


def test_no_roles_non_self_merge_stays_network_free(monkeypatch):
    called = []
    monkeypatch.setattr(
        providers,
        "get_provider",
        lambda name: called.append(name),
    )
    repo = _repo(enabled=True, required=True, provider="github")

    actor = pr_config.resolve_actor_pr_flow(repo, "o/r")

    assert actor.flow.profile == pc.PROFILE_PR_HUMAN_MERGE
    assert actor.resolution == "configured"
    assert called == []


def test_non_github_role_map_is_backward_compatible(monkeypatch):
    called = []
    monkeypatch.setattr(
        providers,
        "get_provider",
        lambda name: called.append(name),
    )
    repo = _repo(
        enabled=True,
        required=True,
        provider="gitea",
        roles={
            "maintain": cfg.PRRoleOverride(merge_actor="submitter-direct"),
        },
    )

    actor = pr_config.resolve_actor_pr_flow(repo, "o/r")

    assert actor.flow.profile == pc.PROFILE_PR_HUMAN_MERGE
    assert actor.pr_config is repo.pr
    assert called == []
