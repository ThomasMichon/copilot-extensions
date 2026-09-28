"""``pr-nudge`` -- ask a repo's bound automated reviewer to (re-)review a PR.

Split out of ``pr_ops.py`` (already at its grandfathered module-size ceiling)
rather than grown in place, matching how ``pr_complete.py``/``pr_watch.py``/
``pr_reconcile.py`` already sit alongside it as small dedicated modules for
one ``pr-*`` verb each.

``pr.reviewer`` (``PRConfig.reviewer``, e.g. ``"copilot"``) names the
abstract automated-reviewer binding; each provider maps it to a concrete
action (GitHub: re-request a review from the Copilot bot via
``requested_reviewers``). Providers with no such binding report
``supported: False`` rather than raising -- "nothing to nudge" is a normal
outcome, not a failure.
"""

from __future__ import annotations

from . import config as cfg, pr_ops, tracking
from .config import Config


def pr_nudge(worktree_id: str, *, config: Config | None = None) -> dict:
    """Ask this repo's bound automated reviewer to (re-)review the active PR.

    Never fatal: an unconfigured ``pr.reviewer`` or unsupported provider
    yields ``supported: False`` + ``detail``, not an error. ``requested:
    True`` means the provider's request API call succeeded -- not that a
    fresh verdict landed (typically async; poll pr-status/pr-watch after).
    """
    base: dict = {"worktree_id": worktree_id}
    record = pr_ops._load_record_or_none(worktree_id)
    if record is None:
        return {**base, "has_pr": False,
                "error": f"No tracking record found for '{worktree_id}'."}
    if config is None:
        config = cfg.load_config()
    prcfg = config.default_repo.pr
    # Refuse a provider/credential mismatch before touching the provider at
    # all (mirrors pr_ops.create_pr's own guard): a tracked PR recorded under
    # a different provider than this repo is now configured for must never
    # have the *configured* provider's token/api_base sent to it.
    recorded_active = record.active_pr()
    if (
        recorded_active is not None
        and not tracking._pr_is_terminal(recorded_active)
        and recorded_active.provider
        and recorded_active.provider != prcfg.provider
    ):
        return {**base, "has_pr": True, "supported": False, "requested": False,
                "error": (
                    f"Tracked PR provider {recorded_active.provider!r} differs from "
                    f"configured provider {prcfg.provider!r}; refusing provider "
                    "access with mismatched credentials."
                )}
    pr_ops._reconcile_active_pr(record, config)
    active = record.active_pr()
    # ``active_pr()`` falls back to the most recent PR overall when every
    # tracked PR is terminal -- exclude that fallback here (mirrors
    # ``pr_ops``'s own ``tracking._pr_is_terminal`` checks) so a nudge never
    # targets an already-merged/closed PR.
    if active is None or active.number is None or tracking._pr_is_terminal(active):
        return {**base, "has_pr": False, "supported": False,
                "detail": "No open PR tracked for this worktree (nothing to nudge)."}
    provider_name = active.provider or prcfg.provider
    target_repo = active.repo or (record.repo or "")
    api_base = getattr(prcfg, "api_base", "") or ""
    reviewer = getattr(prcfg, "reviewer", "") or ""
    out: dict = {**base, "has_pr": True, "number": active.number, "repo": target_repo}
    try:
        from . import providers

        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(target_repo, prcfg)
        result = provider.request_review(
            target_repo, active.number, reviewer=reviewer, api_base=api_base, token=token,
        )
    except Exception as exc:
        return {**out, "supported": False, "requested": False, "error": str(exc)}
    out["supported"] = result.supported
    out["requested"] = result.requested
    out["reviewer"] = result.reviewer
    if result.detail:
        out["detail"] = result.detail
    if result.error:
        out["error"] = result.error
    return out
