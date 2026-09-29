"""PR-flow config resolution -- configured profiles and live actor profiles.

Extracted from ``__main__.py`` (module-size-baseline split, copilot-extensions
#2614) into its own small module rather than ``pr_ops.py`` (already at its
grandfathered line-count ceiling) or ``pr_contract.py`` (a deliberately
config-free pure contract -- see its own module docstring). The configured
profile stays pure and network-free for offline consumers. Networked PR
commands may additionally resolve the acting identity's provider permission,
layer ``pr.roles``, and classify the resulting effective actor flow.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import config as cfg
from . import git_ops
from . import pr_contract as pc


def _resolve_repo_remote(config: cfg.Config, repo: cfg.RepoConfig) -> str:
    """Canonical remote URL for the active repo -- the device-independent key.

    Prefers the **registry** remote for this project (curated and consistent
    across machines, so a shared consumer keys every device the same way), and
    falls back to the anchor's ``git remote get-url origin`` when the project is
    not in the repos registry. Returns ``""`` when neither resolves.
    """
    from . import repos

    try:
        entry = repos.find_repo(config.repo_name)
        if entry and entry.remote:
            return entry.remote
        result = git_ops.git("remote", "get-url", "origin", cwd=repo.anchor, check=False)
        if result.returncode == 0:
            return result.stdout.strip()
    except OSError:
        # anchor may not exist yet (e.g. a freshly-configured project); the
        # remote is simply unknown rather than an error.
        pass
    return ""


def _profile_for_pr_config(prc: cfg.PRConfig):
    """Classify one already-resolved PR config (pure, no network)."""
    return pc.classify_pr_flow(
        enabled=prc.enabled,
        required=prc.required,
        provider=prc.provider,
        automerge_label=getattr(prc, "automerge_label", ""),
        reviewer=getattr(prc, "reviewer", ""),
        review_blocking=getattr(prc, "review_blocking", False),
        review_latency_hint=getattr(prc, "review_latency_hint", ""),
        self_approve=getattr(prc, "self_approve", False),
        merge_actor=getattr(prc, "merge_actor", ""),
        conflict_retriggers_review=getattr(prc, "conflict_retriggers_review", True),
        branch_update_strategy=getattr(prc, "branch_update_strategy", "rebase"),
        merge_strategy=getattr(prc, "merge_strategy", "squash"),
        prefer_auto_merge=getattr(prc, "prefer_auto_merge", True),
        notes=getattr(prc, "notes", ""),
    )


def _pr_flow_profile(repo: cfg.RepoConfig):
    """Derive the repo's configured/base PR profile (pure, no network).

    This is intentionally the answer exposed by the offline ``get pr-profile``
    query. Networked commands that make actor-specific decisions use
    :func:`resolve_actor_pr_flow` instead.
    """
    return _profile_for_pr_config(repo.pr)


@dataclass(frozen=True)
class ActorPRFlow:
    """Configured and actor-effective views of one repo's PR flow."""

    pr_config: cfg.PRConfig
    configured_flow: pc.PRFlowProfile
    flow: pc.PRFlowProfile
    viewer_permission: str = ""
    resolution: str = "configured"


def resolve_actor_pr_flow(
    repo: cfg.RepoConfig,
    repo_slug: str,
    *,
    api_base: str = "",
    token: str | None = None,
    authority_sensitive: bool = True,
) -> ActorPRFlow:
    """Resolve the live actor's effective PR config and flow.

    ``pr.roles`` is GitHub-only and layers through
    :func:`config.resolve_role_pr_config`. Operational self-merge surfaces also
    use the provider-neutral live permission to demote a configured
    ``pr-self-merge`` flow for a confidently read-only actor. Unknown/failed
    permission reads preserve the configured/base profile: that is fail-closed
    when the base is conservative, and preserves the historical fail-open
    contract for a base self-merge repo.

    ``authority_sensitive=False`` is the publication-only mode used by
    ``create-pr``: it resolves configured role overrides but does not add a
    permission read solely because the base profile is self-merge.
    """
    from . import providers

    base_pr = repo.pr
    configured_flow = _profile_for_pr_config(base_pr)
    role_aware = bool(base_pr.roles) and base_pr.provider == "github"
    needs_authority = (
        authority_sensitive
        and configured_flow.profile == pc.PROFILE_PR_SELF_MERGE
    )
    if not repo_slug or not (role_aware or needs_authority):
        return ActorPRFlow(base_pr, configured_flow, configured_flow)

    try:
        provider = providers.get_provider(base_pr.provider)
        resolved_token = (
            token
            if token is not None
            else providers.account_token_for_slug(repo_slug, base_pr)
        )
        permission = providers.actor_viewer_permission(
            provider,
            repo_slug,
            api_base=api_base or base_pr.api_base,
            token=resolved_token,
        )
    except Exception:
        permission = ""

    if not permission:
        return ActorPRFlow(
            base_pr,
            configured_flow,
            configured_flow,
            resolution="configured-fallback",
        )

    effective_pr = (
        cfg.resolve_role_pr_config(base_pr, permission)
        if role_aware
        else base_pr
    )
    effective_flow = _profile_for_pr_config(effective_pr)
    authority = pc.actor_merge_authority(permission)
    resolution = "actor-role" if effective_pr is not base_pr else "actor"

    if (
        authority_sensitive
        and authority is False
        and effective_flow.profile == pc.PROFILE_PR_SELF_MERGE
    ):
        effective_pr = replace(
            effective_pr,
            self_approve=False,
            merge_actor="",
        )
        effective_flow = _profile_for_pr_config(effective_pr)
        resolution = "actor-authority"

    return ActorPRFlow(
        effective_pr,
        configured_flow,
        effective_flow,
        viewer_permission=permission,
        resolution=resolution,
    )


def actor_review_blocking(actor_flow: ActorPRFlow) -> bool:
    """Return the effective ``pr-watch`` verdict posture for this actor."""
    if actor_flow.resolution == "actor-authority":
        return True
    return bool(getattr(actor_flow.pr_config, "review_blocking", False))
