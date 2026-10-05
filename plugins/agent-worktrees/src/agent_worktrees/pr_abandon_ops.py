"""``agent-worktrees pr-abandon`` -- mechanical extraction from ``pr_ops.py``.

This module exists only to keep ``pr_ops.py`` under its module-size cap. The
functions below were moved verbatim, with no behavior change.
"""

from __future__ import annotations

from pathlib import Path

from . import claim_history, config as cfg, obligations, tracking
from .config import Config

#: The stern, alternatives-first refusal every first (no ``--confirm``)
#: ``pr-abandon`` call returns. Deliberately verbose and repeated in full
#: every time -- this is the ONLY place ``--confirm`` is ever mentioned to a
#: caller, so an agent can never discover it except by first reading this.
_ABANDON_WARNING = (
    "pr-abandon is a rare, deliberate action -- NOT the default response to "
    "a hard rebase, a merge conflict, or a PR that merely looks messy. Before "
    "considering it further:\n"
    "\n"
    "1. A heavy rebase/conflict is NOT a reason to abandon. Rebase onto the "
    "current default branch (`agent-worktrees git sync`) and resolve "
    "conflicts by hand; once resolved, force-push the rebased branch "
    "(`git push --force-with-lease`) to update the SAME PR. Abandoning a PR "
    "just to reopen an equivalent one discards its review history and "
    "attribution for no reason.\n"
    "2. A PR whose underlying work is genuinely superseded is still rarely "
    "worth discarding WHOLESALE. Check whether any slice of the diff remains "
    "useful on its own (a doc update, a test, a fix unrelated to the "
    "superseding change) -- whittle the branch down to that remainder and "
    "keep driving THAT to merge, rather than abandoning the whole PR. "
    "Abandoning everything should be the rare case where truly nothing of "
    "value survives.\n"
    "3. Only when the PR is genuinely, fully superseded or moot -- and you "
    "have operator sign-off for THIS SPECIFIC PR -- does abandoning it apply. "
    "Do not pass --confirm yourself on your own judgment: surface this "
    "warning to the operator, name the PR and your reasoning, and only add "
    "--confirm after they explicitly authorize it.\n"
    "\n"
    "Once genuinely authorized, re-run this exact command with --confirm."
)


def abandon_pr(
    worktree_id: str,
    config: Config,
    *,
    target_repo: str | None = None,
    pr_number: int | None = None,
    reason: str,
    confirm: bool,
) -> dict:
    """Close a tracked PR WITHOUT merging -- the ``pr-abandon`` primitive.

    The pragmatic, friction-based follow-up to ``pr-merge-obligation-gate``
    (#4375) that #4411 deferred: a real invoker-identity/authorization
    primitive remains future work, but this closes the practical gap in the
    meantime with a two-step confirmation gate instead of a bare escape
    hatch. The FIRST call (``confirm=False``) always refuses with
    :data:`_ABANDON_WARNING` and does nothing else -- no record read, no
    provider call, no claim mutation -- so an agent's own first attempt can
    never accidentally abandon anything. Only a second, explicit
    ``--confirm`` call proceeds, and even then requires ``reason`` (always,
    both calls) so the abandonment is attributable in the claim history
    regardless of which call surfaced the warning.

    ``target_repo``/``pr_number`` default to the worktree's own
    :meth:`~tracking.WorktreeRecord.active_pr` when omitted, mirroring
    :func:`pr_ops.pr_ready`'s own selector convention.

    On confirm: live-reconfirms the PR is not already merged (never trusts
    local ``pr.state`` alone -- the same contract
    :func:`pr_ops._reconcile_active_pr` enforces), closes it via the provider
    (``close_pull``, posting ``reason`` as a PR comment, best-effort),
    settles the worktree's ``pr`` claim to ``abandoned`` (never ``released``
    -- mirrors :func:`pr_ops._release_pr_claim`'s own "an unmerged close is
    abandoned work, not a clean hand-back" contract), and records a durable
    ``claim_history`` event so the action is traceable after the fact. The
    provider call happens BEFORE the record lock is taken (matches
    :func:`pr_ops._set_pr_locked`'s own "no network/git I/O inside the lock"
    convention) -- the record is re-loaded and re-matched by PR identity
    under the lock for the actual mutation.

    Does NOT reset the worktree's git HEAD or touch its branch -- that
    remains the operator's own explicit, separate decision (per the
    `pr-merge-obligation-gate` effort's defense 1 policy: never reset HEAD
    off unmerged commits except under an explicit, operator-approved
    action). ``finalize --abandon --handoff-to`` is the separate, existing
    primitive for that follow-on step once this claim is already settled.
    """
    from . import pr_ops

    base: dict = {"success": False, "worktree_id": worktree_id}
    if not (reason or "").strip():
        return {**base, "error": (
            "pr-abandon: --reason is required (both before and after "
            "--confirm) -- state concretely why this PR is being abandoned "
            "(e.g. 'superseded by #1234, no remaining unique content')."
        )}
    if not confirm:
        return {**base, "error": _ABANDON_WARNING, "needs_confirm": True}

    record = pr_ops._load_record_or_none(worktree_id)
    if record is None:
        return {**base, "error": f"No tracking record found for '{worktree_id}'."}

    pr_ops._reconcile_active_pr(record, config)
    if pr_number is not None:
        pr = next((p for p in record.prs if p.number == pr_number), None)
        if pr is None:
            return {**base, "error": f"No tracked PR #{pr_number} for '{worktree_id}'."}
    else:
        pr = record.active_pr()
        if pr is None:
            return {**base, "error": f"No tracked PR for '{worktree_id}'."}
    if pr.number is None:
        return {**base, "error": (
            f"Tracked PR for '{worktree_id}' has no PR number recorded."
        )}

    repo = target_repo or pr.repo or record.repo or ""
    if not repo:
        return {**base, "error": (
            f"Tracked PR #{pr.number} for '{worktree_id}' has no target repo."
        )}
    number = pr.number
    base = {**base, "repo": repo, "number": number}

    if pr.state == "merged":
        return {**base, "error": f"PR #{number} is already merged; nothing to abandon."}

    from . import providers

    prcfg = config.default_repo.pr
    provider_name = pr.provider or prcfg.provider
    api_base = getattr(prcfg, "api_base", "") or ""
    provider = providers.get_provider(provider_name)
    token = providers.account_token_for_slug(repo, prcfg)

    # Live re-check immediately before acting: never close a PR based on
    # possibly-stale local tracking.
    try:
        live = provider.get_pull(repo, number, api_base=api_base, token=token)
    except Exception as exc:
        return {**base, "error": f"Could not confirm live PR state before abandoning: {exc}"}
    if live.merged:
        return {**base, "error": (
            f"PR #{number} is already merged (confirmed live); nothing to abandon."
        )}
    already_closed = (live.state or "").strip().lower() == "closed"

    warning = ""
    if not already_closed:
        comment = (
            f"Abandoning this PR: {reason}\n\n"
            "_Recorded via an explicit, operator-authorized `pr-abandon --confirm`._"
        )
        close_result = provider.close_pull(
            repo, number, api_base=api_base, token=token, comment=comment,
        )
        if close_result and "comment post failed" not in close_result:
            return {**base, "error": close_result}
        warning = close_result or ""

    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    with tracking._RecordLock(yaml_path):
        result = _abandon_pr_settle_locked(
            base, yaml_path, number=number, reason=reason,
            already_closed=already_closed, warning=warning,
        )
    return result


def _abandon_pr_settle_locked(
    base: dict, yaml_path: Path, *, number: int, reason: str,
    already_closed: bool, warning: str,
) -> dict:
    """The re-load -> settle -> save tail of :func:`abandon_pr`, run under the
    record lock -- NO network I/O in this window (matches
    :func:`pr_ops._set_pr_locked`'s own convention; the provider close
    already happened in the caller, unlocked).
    """
    from . import pr_ops

    worktree_id = base["worktree_id"]
    try:
        record = tracking.load_record(yaml_path)
    except Exception:
        return {**base, "error": f"No tracking record found for '{worktree_id}'."}
    pr = next((p for p in record.prs if p.number == number), None)
    if pr is None:
        return {**base, "error": (
            f"Tracked PR #{number} for '{worktree_id}' vanished from the "
            "record between the live check and this save -- retry."
        )}

    pr.state = "closed"
    if not pr.closed_at:
        pr.closed_at = tracking._now_iso()

    ref = pr_ops._pr_claim_ref(pr)
    settled = None
    if ref:
        settled = tracking.settle_resource_claim(record, ref, obligations.ABANDONED, save=False)
        if settled is not None:
            settled.note = f"pr-abandon: {reason}"

    tracking.save_record(record)

    if ref and settled is not None:
        claim_history.record_pr_event(
            ref, worktree_id=record.worktree_id, machine=record.machine,
            event="abandoned", note=reason, project=record.repo,
        )

    result = {
        **base, "success": True, "closed": True, "already_closed": already_closed,
        "claim_released": bool(settled),
    }
    if warning:
        result["warning"] = warning
    return result
