"""finalize's backup open-PR gate -- defense-in-depth against a worktree
finalizing while its own attributed PR is still open.

Extracted out of ``finalize.py`` (at its grandfathered module-size ceiling)
rather than inlined there -- mirrors the ``pr_reconcile.py`` /
``session_host_liveness.py`` / ``worktree_probe.py`` precedent of landing new
capability in a fresh same-package module instead of chasing a shrinking
ceiling line-by-line.

``_pr_finalize_precondition`` (in ``finalize.py``) treats "content already
reachable on origin/<default>" as sufficient to finalize, reasoning that
content parity implies the tracked PR merged -- but content can
coincidentally match upstream for a different reason entirely (a *later* PR
happened to land patch-equivalent work; a squash collision) while THIS
worktree's own attributed PR is still sitting open on the provider.
:func:`assert_no_live_pr` is independent of that fast path: it live-reconciles
every tracked PR (not just the "active" one -- parallel PRs get their own
live re-check too, via :func:`reconcile_every_live_pr`) against the provider,
then refuses finalize while any of them is still genuinely open. A worktree
whose local record was never populated at all (``create-pr``/``set-pr`` were
bypassed) is outside this gate's reach -- it is a backup on top of the pr-*
tools establishing claims correctly, not a replacement for them.
"""

from __future__ import annotations

from . import output, tracking
from .config import Config


def reconcile_every_live_pr(
    record: tracking.WorktreeRecord, config: Config,
) -> None:
    """Live-reconcile every non-terminal tracked PR, not just the "active" one.

    ``pr_reconcile.reconcile_pr_state`` (heal-numberless + ``_reconcile_active_pr``)
    only ever touches ``record.active_pr()`` -- exactly one entry. A worktree
    can carry more than one non-terminal :class:`~agent_worktrees.tracking.PRRecord`
    at once (parallel PRs, per ``WorktreeRecord.prs``'s own docstring); this
    reconciles each of THOSE against the provider too, so :func:`assert_no_live_pr`
    never trusts a stale local ``open`` on an entry the active-only helper
    skipped. Best-effort throughout: any provider/network failure for one entry
    leaves that entry's local state untouched and moves on -- never raises.
    """
    try:
        from . import pr_reconcile
        pr_reconcile.reconcile_pr_state(record, config)
    except Exception:
        pass
    active = record.active_pr()
    others = [
        p for p in record.prs
        if p is not active and not tracking._pr_is_terminal(p) and p.number
    ]
    if not others:
        return
    prcfg = config.default_repo.pr
    changed = False
    for entry in others:
        provider_name = entry.provider or prcfg.provider
        target_repo = entry.repo or (record.repo or "")
        try:
            from . import providers
            provider = providers.get_provider(provider_name)
            token = providers.account_token_for_slug(target_repo, prcfg)
            pull = provider.get_pull(
                target_repo, entry.number,
                api_base=getattr(prcfg, "api_base", "") or "", token=token,
            )
        except Exception:
            continue
        state = (pull.state or "").strip().lower()
        if pull.merged:
            resolved = "merged"
        elif state and state not in tracking._PR_NON_TERMINAL:
            resolved = state
        else:
            resolved = ""
        if resolved:
            entry.state = resolved
            if not entry.closed_at:
                entry.closed_at = tracking._now_iso()
            changed = True
    if changed:
        try:
            tracking.save_record(record)
        except Exception:
            pass


def assert_no_live_pr(
    record: tracking.WorktreeRecord | None,
    config: Config,
    worktree_id: str,
    *,
    force: bool = False,
) -> bool:
    """Backup gate: refuse finalize while a *tracked* PR is still genuinely open.

    Always re-reads the provider first (:func:`reconcile_every_live_pr`) --
    never trusts the possibly-stale local ``state`` field -- so a PR merged or
    closed *externally* since the local record last saw it heals before this
    gate evaluates :meth:`~agent_worktrees.tracking.WorktreeRecord.has_live_pr`.
    Fail-open on a reconcile error (the reconcile helpers themselves already
    degrade to the untouched local state on any provider exception): a
    transient network/provider outage must not block finalize for every
    worktree that ever had a PR.

    ``force`` (the finalize CLI's ``--force-open-pr``) is the sole override,
    mirroring the obligation gate's move away from a soft env-var bypass: an
    open PR is exactly as much a real, external claim on this worktree's work
    as an unsettled resource obligation, so only an explicit, per-invocation
    operator decision may finalize anyway -- never an ambient env var.
    """
    if record is None or not record.prs:
        return True
    reconcile_every_live_pr(record, config)
    if not record.has_live_pr():
        return True
    live = [p for p in record.prs if not tracking._pr_is_terminal(p)]
    if force:
        output.warn(
            f"Worktree {worktree_id} has {len(live)} still-open tracked PR(s); "
            f"finalizing anyway (--force-open-pr):"
        )
        for p in live:
            print(f"  · {p.url or (f'#{p.number}' if p.number else p.branch or '(unopened)')}")
        return True
    output.err(
        f"Worktree {worktree_id} has {len(live)} still-open tracked PR(s) -- "
        "finalize is refused. This worktree's own attributed PR record says "
        "the work has not landed (just verified live against the provider), "
        "independent of any local content match with upstream:"
    )
    for p in live:
        label = p.url or (f"#{p.number}" if p.number else p.branch or "(unopened)")
        print(f"  · {label}")
    output.err(
        "Wait for the PR to merge (then 'agent-worktrees sync' + finalize), "
        "merge it now ('agent-worktrees pr-merge <#> --now'), or -- only when "
        "it is genuinely superseded/abandoned and you have confirmed that on "
        "the provider -- pass 'finalize --force-open-pr' to override."
    )
    return False
