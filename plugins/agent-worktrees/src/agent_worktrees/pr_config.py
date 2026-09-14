"""Pure PR-flow config resolution -- profile classification + remote lookup.

Extracted from ``__main__.py`` (module-size-baseline split, copilot-extensions
#2614) into its own small module rather than ``pr_ops.py`` (already at its
grandfathered line-count ceiling) or ``pr_contract.py`` (a deliberately
config-free pure contract -- see its own module docstring). These two
functions bridge a repo's ``config.yaml`` binding onto that pure contract and
the repos registry, so every PR-family surface (``get pr-profile``,
``pr-status``, ``pr-merge``, ``pr`` dispatch) reports one consistent answer.
"""

from __future__ import annotations

from . import config as cfg
from . import git_ops


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


def _pr_flow_profile(repo: cfg.RepoConfig):
    """Derive this repo's PR-flow profile from its config (pure, no network).

    Wraps :func:`pr_contract.classify_pr_flow` with the repo's ``pr`` binding so
    every surface (``get pr-profile``, ``pr-status``, ``pr-merge``) reports the
    same profile: ``direct`` | ``pr-human-merge`` | ``pr-agent-merge`` |
    ``pr-self-merge``.
    """
    from . import pr_contract as pc

    prc = repo.pr
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
    )
