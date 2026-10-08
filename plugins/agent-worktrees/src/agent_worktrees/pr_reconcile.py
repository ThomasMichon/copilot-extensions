"""Out-of-band PR-state reconcile for a single worktree record
(agent-worktrees-fleet-flows Phase 2, #2740).

Extracted out of ``pr_ops.py`` (which is at its grandfathered module-size
ceiling) rather than inlined there -- mirrors the ``session_host_liveness.py``
/ ``worktree_probe.py`` / ``list_views_cli.py`` precedent of landing new
capability in a fresh same-package module instead of chasing a shrinking
ceiling line-by-line.

``pr_status``/``pr_ready``/``create_pr`` already reconcile the active PR
against the provider (:func:`pr_ops._reconcile_active_pr`) -- but only when
one of those *render-time* paths actually runs, which requires a live session
driving the worktree. A ``finalized`` worktree (or any worktree nobody is
currently working in) never gets one of those calls, so its stale ``open`` PR
tag never heals on its own (#2102). Separately, a tracked PR record that is
missing its ``number`` (``create_pr`` pushed the branch and the provider
opened the PR, but the process was interrupted before the number was
persisted) is invisible to ``_reconcile_active_pr`` entirely, since it needs a
number to look the PR up (#2146).

:func:`reconcile_pr_state` is the single entry point the ``reconcile`` verb
(``reconcile_cli.py``) calls per tracked record, regardless of the worktree's
``status`` -- so a fleet-wide/out-of-band sweep can heal every stale or
number-less PR record without depending on anyone actively working in that
worktree.
"""

from __future__ import annotations

from . import tracking
from .config import Config
from .pr_ops import _reconcile_active_pr


def tracked_pr_slug(pr, fallback: str = "", api_base: str = "") -> str | None:
    """The hosting ``owner/name`` a tracked PR lives in: ``pr.repo``, else *fallback*
    (the worktree record's own ``repo``).

    Older records keep the project name (``my-project``), not the hosting owner/name
    the provider needs: asking for it fails, so a PR merged long ago reads as open
    forever and finalize refuses the worktree. The tracked PR URL names the real
    repository. The URL and the number are stored separately, so they must name
    the same PR, or a stale record could certify a different change: then this is
    ``None`` (unresolvable; never guess). ``""`` when nothing is recorded. The one
    resolution every provider read of a tracked PR shares."""
    import re

    from .pr_ops import _repo_slug_from_pr_url

    slug = (getattr(pr, "repo", "") or fallback or "").strip()
    if "/" in slug:
        return slug
    url = (getattr(pr, "url", "") or "").strip()
    if not url:
        return None if slug else ""
    url_number = re.search(r"/pulls?/(\d+)/?$", url)
    number = getattr(pr, "number", None)
    if url_number is None or (number and int(url_number.group(1)) != int(number)):
        return None
    return _repo_slug_from_pr_url(url, api_base) or slug or None


def heal_numberless_active_pr(
    record: tracking.WorktreeRecord | None, config: Config,
) -> bool:
    """Resolve a number-less active PR by its head branch (#2146).

    A tracked PR record can be missing its ``number`` when ``create_pr`` pushed
    the feature branch and the provider opened the PR, but the process was
    interrupted (context loss, network blip) before the returned number was
    persisted. Such a record is invisible to :func:`pr_ops._reconcile_active_pr`
    (which needs a number to look the PR up) and to ``pr-status``/the Picker --
    it looks like there is no PR at all.

    Resolves it via :meth:`PRProvider.find_pull_by_head` keyed on the record's
    tracked feature branch (``active.branch``), across every PR state (so a
    since-merged PR heals too, not just a still-open one). No-op (returns
    False) when there is no active PR, it already has a number, it has no
    tracked branch to search by, or the provider can't resolve it
    (unconfigured/unreachable/unsupported, or genuinely no matching PR).
    Persists synchronously on a match.
    """
    if record is None:
        return False
    active = record.active_pr()
    if active is None or active.number is not None or not active.branch:
        return False
    prcfg = config.default_repo.pr
    provider_name = active.provider or prcfg.provider
    target_repo = active.repo or (record.repo or "")
    try:
        from . import providers

        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(target_repo, prcfg)
        found = provider.find_pull_by_head(
            target_repo, active.branch,
            api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
    except Exception:
        # Provider unconfigured/unreachable/unsupported -- leave the number-
        # less record as-is rather than guessing.
        return False
    if found is None or found.number is None:
        return False
    active.number = found.number
    active.url = found.url or active.url
    active.provider = active.provider or provider_name
    active.repo = target_repo
    if not active.opened_at:
        active.opened_at = tracking._now_iso()
    # Unlike _reconcile_active_pr (which only ever *advances* a known state to
    # terminal), a number-less record's prior state was a placeholder (e.g.
    # "creating") -- always adopt the provider's real state, terminal or not.
    active.state = "merged" if found.merged else (found.state or active.state)
    if tracking._pr_is_terminal(active) and not active.closed_at:
        active.closed_at = tracking._now_iso()
    tracking.save_record(record)
    return True


def reconcile_pr_state(
    record: tracking.WorktreeRecord | None, config: Config,
) -> bool:
    """Full out-of-band PR-state reconcile for one worktree record.

    Heals a number-less active PR first (#2146), then reconciles a numbered
    active PR against the provider (the same check ``pr-status``/
    ``pr-ready``/``create_pr`` already run at render time) -- but callable
    directly for a worktree with **no live session** driving those paths,
    e.g. a ``finalized`` worktree (#2102's render-time-reconcile gap). Returns
    True when the record's active-PR number or state changed as a result, so
    a caller (the ``reconcile`` verb) can report what it actually fixed.
    """
    if record is None:
        return False
    before_pr = record.active_pr()
    before = (before_pr.number, before_pr.state) if before_pr else None
    healed = heal_numberless_active_pr(record, config)
    _reconcile_active_pr(record, config)
    after_pr = record.active_pr()
    after = (after_pr.number, after_pr.state) if after_pr else None
    return healed or before != after
