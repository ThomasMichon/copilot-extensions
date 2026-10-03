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


@dataclass(frozen=True)
class ForeignRepoResolution:
    """The outcome of resolving a ``repo_slug`` to its OWN registered repo
    config, independent of the caller's active project.

    ``repo_config`` and ``repo_name`` are both empty/``None`` when
    ``repo_slug`` could not be resolved to a registered repo at all --
    callers must report that plainly (the ``foreign-target-resolves-honestly``
    behavior in the ``pull-requests`` vision) rather than silently falling
    back to their own project's binding.
    """

    repo_config: cfg.RepoConfig | None
    repo_name: str = ""
    same_as_active: bool = False

    @property
    def resolved(self) -> bool:
        return self.repo_config is not None


def resolve_repo_config_for_slug(
    config: cfg.Config, repo_slug: str
) -> ForeignRepoResolution:
    """Resolve the :class:`~agent_worktrees.config.RepoConfig` that OWNS
    ``repo_slug`` (``owner/name`` or ADO ``project/repo``), independent of the
    caller's active project -- the primitive behind ``foreign-repo-pr-operations``
    (see ``visions/plugins/agent-worktrees/pull-requests``).

    Resolution order:

    1. **Fast path** -- ``repo_slug`` matches the caller's own active
       project's registered remote. Returns ``config.default_repo`` unchanged
       (no extra config load, and identical behavior to before this
       function existed).
    2. **Foreign, registered** -- ``repo_slug`` matches a *different*
       registered repo's remote. Loads **that repo's own** layered config via
       :func:`agent_worktrees.config.load_project_config` (never inheriting
       the caller's identity/config) and returns its ``default_repo``.
    3. **Unregistered** -- no registered repo claims ``repo_slug``. Returns an
       unresolved :class:`ForeignRepoResolution` (``repo_config=None``); the
       caller must fail honestly rather than guess or fall back.

    A target repo found in the registry but with **no PR binding this
    machine can build** (anchor unresolvable, config load failure) is treated
    the same as unregistered -- partial/best-effort binding would risk acting
    under the wrong provider/policy, which is exactly what this function
    exists to prevent.
    """
    from . import repos as repos_mod

    try:
        active_remote = _resolve_repo_remote(config, config.default_repo)
    except Exception:
        active_remote = ""
    if active_remote and git_ops.slug_from_url(active_remote) == repo_slug:
        return ForeignRepoResolution(
            config.default_repo, config.repo_name, same_as_active=True
        )

    try:
        entries = repos_mod.list_repos()
    except Exception:
        entries = []

    for entry in entries:
        if entry.name == config.repo_name:
            continue  # already covered by the fast path above
        if not entry.remote:
            continue
        if git_ops.slug_from_url(entry.remote) != repo_slug:
            continue
        try:
            foreign_config = cfg.load_project_config(entry.name)
        except Exception:
            return ForeignRepoResolution(None)
        resolved = foreign_config.repos.get(entry.name)
        if resolved is None:
            return ForeignRepoResolution(None)
        return ForeignRepoResolution(resolved, entry.name)

    return ForeignRepoResolution(None)


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
