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

#: Per-provider "this PR is open" vocabulary, normalized to the literal
#: "open" `_ensure_pr_claim`/`tracking.PRRecord` require -- Azure DevOps'
#: own `create_pull()` returns its native `status` value (``"active"``)
#: verbatim, never the cross-provider "open" literal every other provider
#: uses, so an unnormalized ADO PR would silently open unclaimed.
_OPEN_STATE_ALIASES = {"active": "open"}


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
    if not prcfg.enabled:
        return {
            "error": (
                f"PR mode is not enabled for '{target_repo}'. That repo's "
                "own config must set pr.enabled: true before anything can "
                "open a PR against it."
            ),
            "repo": target_repo,
        }
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
    pre_record = tracking.load_record(rec_path) if rec_path.exists() else None

    machine = getattr(config, "machine", "") or ""
    session = getattr(pre_record, "parent_session", "") if pre_record else ""
    codename = getattr(pre_record, "codename", "") if pre_record else ""

    # Same tri-state resolution `create_pr`'s own local path uses:
    # `prcfg.source_attribution` (a per-repo "codename" | True | False
    # config value, NOT a bool-only toggle) is the default, overridden only
    # by an explicit `attribution=` argument (today only ever `False`, from
    # `--no-attribution` -- `None` otherwise).
    effective_attribution = (
        prcfg.source_attribution if attribution is None else attribution
    )
    if effective_attribution == "codename":
        # Same provenance gate the local path's codename-mode branch
        # applies (`attribution.may_publish_codename`): a codename from a
        # CUSTOM wordlist only publishes when this repo has explicitly
        # opted into source attribution (`source_attribution_configured`);
        # the bare implicit default never does, closing exactly the
        # custom-vocabulary-leak case that check exists to prevent. This
        # lean no-checkout path never attempts a live codename BACKFILL on
        # a missing/invalid codename (unlike the local path) -- it
        # degrades to no marker rather than guessing or crashing.
        if is_valid_handle(codename) and attr.may_publish_codename(
            codename_source=(
                getattr(pre_record, "codename_source", None) if pre_record else None
            ),
            source_attribution_configured=prcfg.source_attribution_configured,
        ):
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

    normalized_state = _OPEN_STATE_ALIASES.get(pull.state, pull.state) or "open"
    result: dict = {
        "repo": target_repo,
        "url": pull.url,
        "number": pull.number,
        "state": normalized_state,
        "draft": bool(draft),
        "head": from_branch,
        "base": base_branch,
        "pr_opened": True,
    }
    if getattr(pull, "label_error", ""):
        result["pr_label_error"] = pull.label_error

    try:
        # Reload under the record lock -- RIGHT BEFORE the claim
        # read-modify-write, not the pre-network-call snapshot above --
        # so a concurrent CLI's own claim/settle in the gap while this
        # function was awaiting the provider can't be silently clobbered
        # by saving a stale copy over it.
        with tracking._RecordLock(rec_path, require_sidecar=True):
            record = tracking.load_record(rec_path) if rec_path.exists() else None
            if record is None:
                raise FileNotFoundError(f"no tracking record for {worktree_id!r}")
            target_pr = tracking.PRRecord(
                repo=target_repo, number=pull.number, url=pull.url,
                state=normalized_state,
            )
            claimed_ref = _ensure_pr_claim(record, target_pr)
            tracking.save_record(record)
            if claimed_ref:
                claim_history.record_pr_event(
                    claimed_ref, worktree_id=record.worktree_id,
                    machine=record.machine, event="claimed", project=record.repo,
                )
            result["claimed"] = bool(claimed_ref)
    except FileNotFoundError:
        result["claimed"] = False
        result["claim_warning"] = (
            f"no tracking record found for worktree {worktree_id!r} -- PR "
            "opened but not claimed; run `claims add pr` manually"
        )
    except Exception as e:
        # The PR already exists on the provider by this point -- a
        # claim-persistence failure must never read as the create itself
        # failing (there is nothing left to roll back). Degrade to a
        # warning with a concrete manual recovery command instead.
        result["claimed"] = False
        result["claim_warning"] = (
            f"PR opened ({pull.url}), but claiming it onto worktree "
            f"{worktree_id!r} failed: {e}. Run `agent-worktrees claims "
            f"add pr {pull.url} --worktree {worktree_id}` manually."
        )
    return result
