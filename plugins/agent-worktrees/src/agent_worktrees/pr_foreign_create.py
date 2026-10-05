"""Foreign-repo PR creation from an already-pushed branch -- no local
checkout required (``pull-request-capability`` effort, Phase 2d).

New module rather than extending ``pr_ops.py`` (already at its grandfathered
line-count ceiling): the motivating case is a host agent whose worktree never
checked out the target repo at all -- a container, another host, or any
other process already pushed the feature branch there, and the calling
worktree just wants to open the PR and durably own the resulting obligation.
``create_pr``'s own ~400-line local path (squash, push, title derivation from
commit history, branch-reuse semantics) is entirely inapplicable here and
deliberately NOT reused; what IS reused is the same provider dispatch
(``providers.get_provider``/``account_token_for_slug``/``PRScope``) and the
same claim-journal primitive (``pr_ops._ensure_pr_claim``) the local path
already relies on, so both paths converge on identical claim/attribution
semantics rather than growing two divergent implementations.

Per the effort's own resolved design decision (2026-10-04, operator-directed):
the CALLING worktree always auto-journals a ``pr``-kind claim on itself --
never the manual ``claims add pr`` workaround ``venue-and-claims.md``
documents as today's only path. This is deliberately NOT also appended to the
calling worktree's own ``prs`` list (unlike a PR the worktree's own checkout
opened): the calling worktree never owns this PR's lifecycle in the full
create-pr sense (it has no checkout to push updates from), only the
obligation to see it through -- a claim is the right-sized bookkeeping,
not a full tracked-PR record.
"""
from __future__ import annotations

from . import claim_history, config as cfg, pr_config, pr_ops, tracking
from .codename import is_valid_handle
from .providers import attribution as attr
from .providers import base as providers
from .pr_ops import _ensure_pr_claim


def create_foreign_pr_from_branch(
    worktree_id: str,
    config: cfg.Config,
    *,
    target_repo: str,
    from_branch: str,
    title: str,
    body: str = "",
    base: str | None = None,
    draft: bool = False,
    attribution: bool | None = None,
) -> dict:
    """Open a PR on ``target_repo`` from ``from_branch`` (already pushed
    there by some other process) and claim it onto ``worktree_id``'s own
    ledger. Returns a result dict shaped like ``create_pr``'s own
    (``url``/``number``/``state``/``pr_opened``/...) on success, or
    ``{"error": "..."}`` on a resolution/provider failure -- never raises for
    an expected failure mode.

    ``target_repo`` must already be a DIFFERENT, registered repo (the caller
    is expected to have already rejected ``same_as_active`` -- see
    ``finalize_cli.cmd_create_pr``'s own ``--repo``/``--from-branch`` gating,
    which is this function's only caller today).
    """
    resolution = pr_config.resolve_repo_config_for_slug(config, target_repo)
    if not resolution.resolved:
        return {
            "error": (
                f"'{target_repo}' is not a registered repo this machine can "
                "resolve a PR binding for"
            ),
            "repo": target_repo,
        }
    if resolution.same_as_active:
        return {
            "error": (
                f"'{target_repo}' is this worktree's own active repo -- use "
                "the normal create-pr path (push + open), not --from-branch"
            ),
            "repo": target_repo,
        }

    repo_cfg = resolution.repo_config
    prcfg = repo_cfg.pr
    base_branch = base or repo_cfg.default_branch

    missing_body = pr_ops.missing_required_body_sections(body, prcfg.required_body_sections)
    if missing_body:
        return {
            "error": (
                "PR body is missing required non-empty section(s): "
                + ", ".join(missing_body)
                + ". Pass --body or --body-file before opening the PR."
            ),
            "repo": target_repo,
        }

    rec_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    record = tracking.load_record(rec_path) if rec_path.exists() else None

    machine = getattr(config, "machine", "") or ""
    session = getattr(record, "parent_session", "") if record else ""
    codename = getattr(record, "codename", "") if record else ""

    # Same tri-state resolution `create_pr`'s own local path uses:
    # `prcfg.source_attribution` (a per-repo "codename" | True | False
    # config value, NOT a bool-only toggle) is the default, overridden only
    # by an explicit `attribution=` argument (today only ever `False`, from
    # `--no-attribution` -- `None` otherwise). Unlike the local path, this
    # lean no-checkout path never attempts a live codename BACKFILL on a
    # missing/invalid codename -- it degrades to no marker rather than
    # guessing or crashing, consistent with this module's "new sibling
    # function, not a retrofit" scope.
    effective_attribution = (
        prcfg.source_attribution if attribution is None else attribution
    )
    if effective_attribution == "codename":
        if is_valid_handle(codename):
            full_body = attr.append_marker(body or "", attr.build_codename_marker(codename))
        else:
            full_body = attr.strip_marker(body or "")
    elif effective_attribution is True:
        marker = attr.build_marker(
            worktree_id, machine=machine, session=session, head=from_branch,
        )
        full_body = attr.append_marker(body or "", marker)
    else:
        full_body = attr.strip_marker(body or "")

    labels = tuple(
        lbl.replace("{machine}", machine) for lbl in (getattr(prcfg, "labels", ()) or ())
    )
    scope = providers.PRScope(
        repo=target_repo, head=from_branch, base=base_branch, title=title,
        body=full_body, api_base=getattr(prcfg, "api_base", "") or "",
        labels=labels, draft=draft,
    )
    try:
        provider = providers.get_provider(prcfg.provider)
        token = providers.account_token_for_slug(scope.repo, prcfg)
        pull = provider.create_pull(scope, token=token)
    except (providers.ProviderError, OSError) as e:
        return {"error": str(e), "repo": target_repo}

    result: dict = {
        "repo": target_repo,
        "url": pull.url,
        "number": pull.number,
        "state": pull.state or "open",
        "draft": bool(draft),
        "head": from_branch,
        "base": base_branch,
        "pr_opened": True,
    }
    if getattr(pull, "label_error", ""):
        result["pr_label_error"] = pull.label_error

    if record is not None:
        try:
            target_pr = tracking.PRRecord(
                repo=target_repo, number=pull.number, url=pull.url,
                state=pull.state or "open",
            )
            claimed_ref = _ensure_pr_claim(record, target_pr)
            tracking.save_record(record)
            if claimed_ref:
                claim_history.record_pr_event(
                    claimed_ref, worktree_id=record.worktree_id,
                    machine=record.machine, event="claimed", project=record.repo,
                )
            result["claimed"] = bool(claimed_ref)
        except Exception as e:
            # The PR already exists on the provider by this point -- a
            # claim-persistence failure must never read as the create
            # itself failing (there is nothing left to roll back). Degrade
            # to the same claim_warning shape the "no tracking record"
            # branch below uses, so the caller always has a concrete,
            # actionable next step.
            result["claimed"] = False
            result["claim_warning"] = (
                f"PR opened ({pull.url}), but claiming it onto worktree "
                f"{worktree_id!r} failed: {e}. Run `agent-worktrees claims "
                f"add pr {pull.url} --worktree {worktree_id}` manually."
            )
    else:
        result["claimed"] = False
        result["claim_warning"] = (
            f"no tracking record found for worktree {worktree_id!r} -- PR "
            "opened but not claimed; run `claims add pr` manually"
        )
    return result
