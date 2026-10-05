"""``pr-diff`` / ``pr-comment`` / ``pr-review`` -- the reviewer-side CLI
surface for the active tracked PR (Phase 3 of the ``pull-request-capability``
effort; Vision ``plugins/agent-worktrees/pull-requests`` §Features/
``reviewer-capable-provider``).

Split out alongside ``pr_nudge_ops.py``/``pr_complete.py``/``pr_watch.py``/
``pr_reconcile.py`` as one small dedicated module per ``pr-*`` verb family,
rather than grown into ``pr_ops.py`` (already at its grandfathered
module-size ceiling). Mirrors ``pr_nudge_ops.pr_nudge``'s own active-PR
resolution + provider/credential-mismatch guard exactly -- see that module's
docstring for the reasoning.
"""

from __future__ import annotations

from . import config as cfg, pr_ops, tracking
from .config import Config
from .pr_nudge_ops import _provider_mismatch_error


def _resolve_active_pr(worktree_id: str, config: Config | None):
    """Shared resolution: tracked record -> reconciled active PR -> provider
    inputs. Returns ``(base, active_or_none, provider_name, target_repo,
    api_base, config, error_dict_or_none)``. ``error_dict_or_none`` is set
    (at which point callers should return it, merged with ``base``) on any
    failure to resolve down to a live, non-terminal tracked PR.
    """
    base: dict = {"worktree_id": worktree_id}
    record = pr_ops._load_record_or_none(worktree_id)
    if record is None:
        return (base, None, "", "", "", config,
                {"has_pr": False,
                 "error": f"No tracking record found for '{worktree_id}'."})
    if config is None:
        config = cfg.load_config()
    prcfg = config.default_repo.pr
    mismatch = _provider_mismatch_error(record.active_pr(), prcfg)
    if mismatch:
        return (base, None, "", "", "", config,
                {"has_pr": True, "error": mismatch})
    pr_ops._reconcile_active_pr(record, config)
    active = record.active_pr()
    mismatch = _provider_mismatch_error(active, prcfg)
    if mismatch:
        return (base, None, "", "", "", config,
                {"has_pr": True, "error": mismatch})
    if active is None or active.number is None or tracking._pr_is_terminal(active):
        return (base, None, "", "", "", config,
                {"has_pr": False,
                 "detail": "No open PR tracked for this worktree."})
    provider_name = active.provider or prcfg.provider
    target_repo = active.repo or (record.repo or "")
    api_base = getattr(prcfg, "api_base", "") or ""
    return base, active, provider_name, target_repo, api_base, config, None


def pr_diff(worktree_id: str, *, config: Config | None = None) -> dict:
    """Read the active tracked PR's current unified diff."""
    base, active, provider_name, target_repo, api_base, config, err = (
        _resolve_active_pr(worktree_id, config)
    )
    if err is not None:
        return {**base, **err}
    out: dict = {**base, "has_pr": True, "number": active.number, "repo": target_repo}
    try:
        from . import providers

        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(target_repo, config.default_repo.pr)
        result = provider.get_diff(target_repo, active.number, api_base=api_base, token=token)
    except Exception as exc:
        return {**out, "supported": False, "error": str(exc)}
    out["supported"] = result.supported
    out["diff"] = result.diff
    if result.error:
        out["error"] = result.error
    return out


def pr_comment(worktree_id: str, body: str, *, config: Config | None = None) -> dict:
    """Post a general comment on the active tracked PR."""
    base, active, provider_name, target_repo, api_base, config, err = (
        _resolve_active_pr(worktree_id, config)
    )
    if err is not None:
        return {**base, **err}
    out: dict = {**base, "has_pr": True, "number": active.number, "repo": target_repo}
    try:
        from . import providers

        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(target_repo, config.default_repo.pr)
        error = provider.post_comment(
            target_repo, active.number, body, api_base=api_base, token=token
        )
    except Exception as exc:
        return {**out, "error": str(exc)}
    if error:
        out["error"] = error
    return out


def pr_review(
    worktree_id: str, *, event: str, body: str = "", config: Config | None = None,
) -> dict:
    """Publish a review verdict on the active tracked PR."""
    base, active, provider_name, target_repo, api_base, config, err = (
        _resolve_active_pr(worktree_id, config)
    )
    if err is not None:
        return {**base, **err}
    out: dict = {**base, "has_pr": True, "number": active.number, "repo": target_repo,
                 "event": event.upper()}
    try:
        from . import providers

        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(target_repo, config.default_repo.pr)
        error = provider.submit_review(
            target_repo, active.number, event=event, body=body,
            api_base=api_base, token=token,
        )
    except Exception as exc:
        return {**out, "error": str(exc)}
    if error:
        out["error"] = error
    return out
