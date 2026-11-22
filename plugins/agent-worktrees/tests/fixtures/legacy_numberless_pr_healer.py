"""Pre-reconciliation healer, intentionally unaware of local retirement receipts.

The implementation is retained here to exercise a real older reader against
the new writer, without depending on Git history or network access in tests.
"""

from agent_worktrees import providers, tracking
from agent_worktrees.config import Config


def heal_numberless_active_pr(record: tracking.WorktreeRecord | None, config: Config) -> bool:
    if record is None:
        return False
    active = record.active_pr()
    if active is None or active.number is not None or not active.branch:
        return False
    prcfg = config.default_repo.pr
    provider_name = active.provider or prcfg.provider
    target_repo = active.repo or (record.repo or "")
    try:
        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(target_repo, prcfg)
        found = provider.find_pull_by_head(
            target_repo, active.branch, api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
    except Exception:
        return False
    if found is None or found.number is None:
        return False
    active.number = found.number
    active.url = found.url or active.url
    active.provider = active.provider or provider_name
    active.repo = target_repo
    if not active.opened_at:
        active.opened_at = tracking._now_iso()
    active.state = "merged" if found.merged else (found.state or active.state)
    if tracking._pr_is_terminal(active) and not active.closed_at:
        active.closed_at = tracking._now_iso()
    tracking.save_record(record)
    return True
