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

:func:`content_exceeds_merged_head` is a second, orthogonal gate added here
for the same module-size reason (#4388): it catches a keep-alive worktree
that kept committing *after* its tracked PR merged -- a case
:func:`assert_no_live_pr` cannot see, since the merged PR is terminal, not
live.
"""

from __future__ import annotations

from urllib.parse import urlparse

from . import output, tracking
from .config import Config


def _authority_origin(value: str) -> tuple[str, str, int] | None:
    """Parse ``value`` (an ``authority_endpoint()`` result OR a tracked PR
    URL) into a comparable ``(scheme, host, port)`` origin triple, or
    ``None`` when it cannot be confidently parsed (no host). Port defaults
    per-scheme (``http`` -> 80, ``https`` -> 443) when not explicit, so
    ``http://host`` and ``http://host:80`` compare equal while
    ``http://host`` and ``https://host`` -- a DIFFERENT origin entirely --
    do not.
    """
    value = (value or "").strip()
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"//{value}")
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    scheme = (parsed.scheme or "https").lower()
    port = parsed.port
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return scheme, host, port


def _authority_path_parts(value: str) -> list[str]:
    """Extract the non-empty path segments of an ``authority_endpoint()``
    result or a tracked PR URL (e.g. Azure DevOps' organization segment in
    ``https://dev.azure.com/<org>``, or a path-hosted Gitea instance's root
    in ``https://forge.example/gitea``). GitHub's endpoint never carries a
    path, so this is empty for it.
    """
    value = (value or "").strip()
    if not value:
        return []
    parsed = urlparse(value if "://" in value else f"//{value}")
    return [part for part in (parsed.path or "").split("/") if part]


def _authority_matches(tracked_url: str, expected_endpoint: str) -> bool:
    """True iff ``tracked_url``'s authority is consistent with the
    configured provider's ``expected_endpoint`` -- the full origin (scheme,
    host, and effective port) must match exactly, AND when the expected
    endpoint itself carries a path (Azure DevOps' organization, or a
    path-hosted Gitea instance's root), the tracked URL's path must fall
    under that same root.

    Fails CLOSED (returns ``False``) whenever either side cannot be
    confidently parsed into a definite origin -- the caller must then
    treat the PR as indeterminate rather than silently skip the check.
    Comparing host[:port] alone is not enough either: it collapses
    ``http://`` and ``https://`` onto the same value, which would let a
    tracked PR on one scheme be confirmed against a configured endpoint on
    the OTHER scheme as though they were the same service. And two
    different Azure DevOps organizations -- or two different Gitea
    instances path-hosted on the same shared host -- would otherwise
    compare as the same authority, letting a stale tracked PR be re-queried
    against an unrelated org/instance and confirm the wrong merge.
    """
    tracked_origin = _authority_origin(tracked_url)
    expected_origin = _authority_origin(expected_endpoint)
    if tracked_origin is None or expected_origin is None:
        return False
    if tracked_origin != expected_origin:
        return False
    base_parts = _authority_path_parts(expected_endpoint)
    if not base_parts:
        return True
    tracked_parts = _authority_path_parts(tracked_url)
    return tracked_parts[: len(base_parts)] == base_parts


def dirty_worktree_error(worktree_path: str, *, wt_exists: bool) -> str | None:
    """Refuse a PR-mode finalize on uncommitted/untracked changes (#4400).

    ``_pr_finalize_precondition`` only inspects commits, never the working
    tree -- a modified or untracked file at an otherwise-safe merged head
    would silently be discarded by the destructive cleanup that follows it.
    Returns an error message when dirty, else ``None``.
    """
    if not wt_exists:
        return None
    from . import git_ops
    if git_ops.is_clean(cwd=worktree_path):
        return None
    detail = "\n".join(f"    {ln}" for ln in git_ops.get_dirty_files(cwd=worktree_path))
    return (
        f"Working tree has uncommitted changes. Commit or stash them before "
        f"finalizing:\n{detail}"
    )


def resolve_precondition_ref(
    feature: str,
    worktree_id: str,
    worktree_path: str,
    *,
    cwd: str,
) -> str | None:
    """Resolve the ref that must represent this worktree's ACTUAL current
    content for ``_pr_finalize_precondition``'s safety checks (#4400).

    ``finalize._resolve_content_ref`` prefers a local ``feature/<slug>``
    snapshot branch when one exists -- correct for publishing
    (push-changes), but dangerous here: under the ``snapshot`` head scheme
    that branch is a FROZEN snapshot of the PR head at push time, distinct
    from the live checkout (#1804 keeps HEAD on ``worktree/<id>`` under
    refspec; a legacy flow can instead leave HEAD on the feature branch
    itself, `pr_ops.py`). A later commit on whichever branch is actually
    checked out would be invisible to a check that trusts the frozen
    snapshot instead -- letting both the upstream-content fast path and the
    merged-head check wrongly certify it safe.

    When the worktree directory is a live checkout, this trusts the current
    checkout (a named branch, or detached ``HEAD``) ONLY when neither
    tracked branch name this worktree can legitimately be on (``worktree/
    <id>``, or the tracked ``feature`` branch under the legacy flow) carries
    commits beyond it. Trusting *any* current checkout unconditionally --
    an unrelated branch, or a detached HEAD sitting behind the tracked
    branch -- would itself be a hole (#4400 rounds 7-8):
    ``validate_and_finalize`` deletes ``record.branch`` on cleanup
    regardless of what's checked out, so real, still-unmerged content
    sitting on a tracked branch the checkout has drifted away from must
    never go unvalidated. When the checkout can't be trusted (or the
    worktree directory is gone), this resolves ``worktree/<id>`` BEFORE
    ``feature`` (#4400 round 10) -- never delegating to
    ``_resolve_content_ref``, whose feature-preferring order is correct for
    publishing but would silently reintroduce the frozen-snapshot hole this
    function exists to close.
    """
    from pathlib import Path

    from . import git_ops
    worktree_ref = f"worktree/{worktree_id}"
    if Path(worktree_path).exists() and cwd == worktree_path:
        current = git_ops._get_current_branch_safe(cwd) or "HEAD"
        if git_ops.ref_exists(current, cwd=cwd) and not _tracked_branch_ahead(
            feature, worktree_id, current, cwd=cwd,
        ):
            return current
    for ref in (worktree_ref, feature, "HEAD"):
        if ref and git_ops.ref_exists(ref, cwd=cwd):
            return ref
    return None


def _tracked_branch_ahead(
    feature: str, worktree_id: str, current: str, *, cwd: str,
) -> bool:
    """True iff either tracked branch name carries commits beyond ``current``."""
    from . import git_ops
    for tracked in (feature, f"worktree/{worktree_id}"):
        if not tracked or not git_ops.ref_exists(tracked, cwd=cwd):
            continue
        ahead = git_ops.git(
            "rev-list", "--count", f"{current}..{tracked}", cwd=cwd, check=False,
        )
        if ahead.returncode != 0 or ahead.stdout.strip() not in ("", "0"):
            return True
    return False


def pr_merge_status(record: tracking.WorktreeRecord, repo) -> bool | None:
    """Tri-state merge lookup for ``record.pr`` (the ACTIVE tracked PR) --
    see :func:`_pr_entry_merge_status` for the full contract. ``False`` when
    there is no ``pr`` at all (nothing that could have merged); otherwise
    delegates to the entry-level lookup, which also repairs ``pr.head_sha``
    in place on a confirmed merge.

    Also best-effort repairs every OTHER tracked PR's own missing
    ``head_sha`` (:func:`repair_other_tracked_pr_heads`) -- this is the ONE
    call site every finalize path reaches unconditionally (``finalize.py``'s
    ``_pr_is_merged`` always calls it before the per-branch boundary check
    runs), so piggybacking the repair here, rather than requiring every
    caller to remember it separately, closes the gap for all of them.
    """
    pr = getattr(record, "pr", None)
    if not pr:
        return False
    try:
        repair_other_tracked_pr_heads(record, repo)
    except Exception:
        pass
    return _pr_entry_merge_status(pr, repo)


def _pr_entry_merge_status(pr, repo) -> bool | None:
    """Tri-state merge lookup for a single tracked PR entry: ``True``
    (confirmed merged), ``False`` (confirmed NOT merged -- neither
    ``number`` nor ``repo`` recorded, meaning a PR was never even opened --
    nothing that could have merged), ``None`` (indeterminate: a PR record
    exists with identifying fields but is otherwise unqueryable -- exactly
    ONE of ``number``/``repo`` present, or a provider/network error, #4400
    rounds 13-14).

    Generalized out of :func:`pr_merge_status` (which calls this for
    ``record.pr``, the active PR) so :func:`repair_other_tracked_pr_heads`
    can apply the SAME authority-validated repair to every OTHER tracked PR
    entry too -- a worktree can carry more than one tracked
    :class:`~agent_worktrees.tracking.PRRecord` (``record.prs``), and each
    one's own merged ``head_sha`` is needed independently by
    ``content_exceeds_merged_head_any``'s per-branch boundary check.

    ``finalize._pr_is_merged`` collapses ``None`` into ``False`` for ITS OWN
    fail-closed purpose (never certify unmerged-or-unknown work safe to
    prune). That collapse is the wrong direction for a caller instead
    proving "definitely NOT yet merged, safe to skip the merged-head check"
    (:func:`upstream_match_is_trustworthy`) -- an indeterminate lookup
    (unlike "no PR tracked at all") must never be read as a confirmed
    non-merge. A record with only ONE of ``number``/``repo`` set is a
    broken/partial write, not "never opened" -- treating it as confirmed
    unmerged would let a tree-only upstream match certify and prune a
    record whose merge boundary genuinely can't be checked.
    """
    head_sha = (getattr(pr, "head_sha", "") or "").strip()
    if getattr(pr, "state", "") == "merged" and head_sha:
        return True
    number = getattr(pr, "number", None)
    slug = getattr(pr, "repo", "") or ""
    if slug and "/" not in slug:
        # Older records keep the project name ("my-project"), not the
        # hosting owner/name the provider needs: asking for it fails and
        # leaves a merged, aligned worktree unfinalizable forever. The tracked
        # PR URL names the real repository (its authority is checked below).
        # The URL and the number are stored separately, so they must name the
        # same PR: otherwise a stale record could certify a different change.
        import re

        from .pr_ops import _repo_slug_from_pr_url

        url = (getattr(pr, "url", "") or "").strip()
        url_number = re.search(r"/pulls?/(\d+)/?$", url)
        if url_number is None or (number and int(url_number.group(1)) != int(number)):
            return None
        slug = _repo_slug_from_pr_url(url, getattr(repo.pr, "api_base", "") or "") or slug
    if not number and not slug:
        return None if getattr(pr, "state", "") == "merged" else False
    if not number or not slug:
        return None
    prcfg = repo.pr
    provider_name = getattr(pr, "provider", "") or prcfg.provider
    if provider_name != prcfg.provider:
        # A legacy/stale record can retain a different provider than the
        # repo is now configured for; querying the configured provider with
        # that slug/number could confirm an unrelated merged PR and hand
        # back the wrong head. Fail closed rather than trust it (mirrors
        # pr_ops.py's active-PR mismatch check).
        return None
    try:
        from . import providers
        provider = providers.get_provider(prcfg.provider)
        tracked_url = (getattr(pr, "url", "") or "").strip()
        if tracked_url:
            expected_endpoint = provider.authority_endpoint(
                getattr(prcfg, "api_base", "") or "",
            )
            if not _authority_matches(tracked_url, expected_endpoint):
                # Same provider kind, but the configured authority has
                # since changed -- a different GitHub Enterprise host, a
                # different Azure DevOps organization on the shared
                # dev.azure.com host, or a different Gitea instance
                # path-hosted on the same shared host. Querying the NEW
                # authority with the OLD slug/number can confirm an
                # unrelated merged PR there and hand back the wrong head.
                return None
        token = providers.account_token_for_slug(slug, prcfg)
        result = provider.get_pull(
            slug, int(number),
            api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
        merged = bool(getattr(result, "merged", False)) or (
            (getattr(result, "state", "") or "").strip().lower() == "merged"
        )
        if not merged:
            return False
        # The provider's merged head is authoritative over a recorded one: a push
        # made outside this tool (a fork, a raw `git push`) leaves the recorded
        # head at an earlier commit, which would then falsely read as "carries
        # further commits".
        observed_head = (getattr(result, "head_sha", "") or "").strip()
        if not observed_head:
            # Best-effort fallback for providers whose get_pull() doesn't
            # eagerly report head_sha -- not every provider supports this
            # (e.g. Azure DevOps deliberately keeps observe_head()
            # unsupported, since it has no server-clock timestamp to
            # satisfy that method's contract; its merged head comes from
            # get_pull() above instead), so swallow any failure here.
            try:
                observed = provider.observe_head(
                    slug, int(number),
                    api_base=getattr(prcfg, "api_base", "") or "",
                    token=token,
                )
                observed_head = (getattr(observed, "head_sha", "") or "").strip()
            except Exception:
                observed_head = ""
        if observed_head:
            pr.head_sha = observed_head
        pr.state = "merged"
        return True
    except Exception:
        return None


def refresh_merged_head(pr, repo) -> bool:
    """Re-read a merged PR's head from the provider before trusting a block.

    A record can carry ``state: merged`` with a stale ``head_sha`` (written
    before the provider's head was taken as authoritative, or recorded at an
    earlier push): the fast path in :func:`_pr_entry_merge_status` then never
    asks the provider again, and finalize refuses forever with "carries
    further commits". Called only on that refusing path, so a normal finalize
    makes no extra request. Returns True iff the provider confirmed the merge
    and reported a different head (now on ``pr``); otherwise ``pr`` is left
    exactly as it was."""
    before = (getattr(pr, "head_sha", "") or "", getattr(pr, "state", "") or "")
    pr.head_sha, pr.state = "", ""
    try:
        status = _pr_entry_merge_status(pr, repo)
    except Exception:
        status = None
    fresh = (getattr(pr, "head_sha", "") or "").strip()
    if status is not True or not fresh:
        pr.head_sha, pr.state = before
        return False
    return fresh != before[0].strip()


def merged_content_exceeds(
    record: tracking.WorktreeRecord, content_ref: str | None, upstream: str, *, cwd: str, repo,
) -> bool:
    """:func:`content_exceeds_merged_head_any` for a merged PR, re-reading a
    stale recorded merged head from the provider once (see
    :func:`refresh_merged_head`) before concluding the worktree carries more."""
    if not content_exceeds_merged_head_any(record, content_ref, upstream, cwd=cwd):
        return False
    if refresh_merged_head(record.pr, repo):
        return content_exceeds_merged_head_any(record, content_ref, upstream, cwd=cwd)
    return True


def repair_other_tracked_pr_heads(record: tracking.WorktreeRecord, repo) -> None:
    """Best-effort, authority-validated repair of every OTHER tracked PR's
    missing ``head_sha`` -- not just ``record.pr`` (the active entry).

    ``upstream_match_is_trustworthy`` only ever repairs ``record.pr`` before
    delegating to :func:`content_exceeds_merged_head_any`, which also
    inspects every ``record.prs[*]`` branch finalize's cleanup will
    force-delete and fails CLOSED when any such branch's own tracked PR is
    missing ``head_sha`` (:func:`_cleanup_branch_refs`). A legacy worktree
    carrying more than one merged PR could therefore never finalize even
    when every one of them is confirmable and repairable via the provider --
    only the active entry ever got the repair attempt. Call this before the
    boundary check so each OTHER entry gets the same chance.

    Mutates each repairable entry's ``head_sha``/``state`` in place via
    :func:`_pr_entry_merge_status` (same authority validation, same
    provider/network error handling). Never raises; a per-entry failure
    just leaves that entry's local state untouched, to be caught by the
    existing fail-closed boundary check.
    """
    active = getattr(record, "pr", None)
    for entry in getattr(record, "prs", None) or []:
        if entry is active:
            continue
        if (getattr(entry, "head_sha", "") or "").strip():
            continue
        if not getattr(entry, "number", None) or not (getattr(entry, "repo", "") or ""):
            continue
        try:
            _pr_entry_merge_status(entry, repo)
        except Exception:
            pass


def upstream_match_is_trustworthy(
    record: tracking.WorktreeRecord, content_ref: str | None, upstream: str, *, cwd: str, repo,
) -> bool:
    """True iff step 1's tree-level upstream match may be trusted without a
    further merged-head check (#4400 fourth + eleventh + twelfth rounds).

    Closes the empty-commit/revert edge case: a no-op or add+revert commit
    after the tracked PR merged leaves the net tree byte-identical to
    upstream even though a real commit exists past the merged head. Safe
    (git-only) to trust when the tracked PR is not (yet) locally recorded as
    merged -- nothing to compare against, matching the pre-existing content-
    match contract. A **merged** record missing `head_sha` (a legacy/
    incomplete record) is fail-closed *untrustworthy* rather than skipped --
    the same "don't certify safety on an inconclusive check" rule as
    `content_exceeds_merged_head` itself. Delegates to the "_any" multi-ref
    check (not just ``content_ref``) so this fast path can never certify
    safety while a separate branch cleanup will force-delete still carries
    unmerged commits.

    "Merged" here uses the SAME authoritative source as
    :func:`pr_merge_status` -- not merely the local, possibly-stale
    ``pr.state`` field (#4400 round 12): a provider-confirmed merge can
    predate a stale/failed local reconcile, in which case ``state`` is still
    ``"open"`` even though the PR genuinely merged. Trusting ``state`` alone
    would let a missing-``head_sha`` record slip through this fast path
    unvalidated. Trustworthy ONLY on a status of exactly ``False``
    (confirmed NOT merged, #4400 round 13) -- ``True`` (confirmed merged)
    and ``None`` (indeterminate: a tracked PR whose live status couldn't be
    confirmed) both fail closed, since an indeterminate lookup must never
    stand in for "provably unmerged." The provider is only asked when
    ``head_sha`` is actually missing (the one case that needs it) -- the
    common, fully-populated-record path stays git-only, no network call.

    A confirmed-NOT-merged status (e.g. a terminal CLOSED/rejected PR) is
    NOT trustworthy on its own either (#4400 round 15): ``assert_no_live_pr``
    permits that terminal state (it isn't "live"), yet cleanup still
    force-deletes every OTHER tracked PR's own feature branch regardless --
    there is no merged head to compare those against, so the fallback check
    is against ``upstream`` directly, via :func:`other_pr_branches_unreachable_
    from_upstream`. The worktree's OWN tracked branch needs no such check
    here -- step 1's TREE-level match already covers it (squash-safe, unlike
    an ancestor check).
    """
    pr = getattr(record, "pr", None)
    head_sha = (getattr(pr, "head_sha", "") or "").strip()
    if not head_sha:
        merge_status = pr_merge_status(record, repo)
        head_sha = (getattr(pr, "head_sha", "") or "").strip()
        if not head_sha and merge_status is not False:
            return False
        if not head_sha:
            return not other_pr_branches_unreachable_from_upstream(
                record, upstream, cwd=cwd,
            )
    return not content_exceeds_merged_head_any(
        record, content_ref, upstream, cwd=cwd, repo=repo,
    )


def other_pr_branches_unreachable_from_upstream(
    record: tracking.WorktreeRecord, upstream: str, *, cwd: str,
) -> bool:
    """True iff any OTHER tracked PR's own feature branch (never the
    worktree's own TRACKED branch, which step 1 already tree-validated)
    carries commits not reachable from ``upstream`` at all (#4400 round 15).

    Applies specifically to the "confirmed NOT merged" case (a closed/
    rejected PR, or none tracked at all), where there is no merged head to
    compare a feature branch against -- yet cleanup still unconditionally
    force-deletes every ``record.prs[*].branch``. Reuses
    ``_extra_commit_count`` with ``upstream`` standing in for both exclusion
    refs (equivalent to ``rev-list ref ^upstream``). Fails closed on any
    unresolvable ref.
    """
    from . import git_ops
    if not git_ops.ref_exists(upstream, cwd=cwd):
        return True
    for pr in getattr(record, "prs", None) or []:
        branch = (getattr(pr, "branch", "") or "").strip()
        if not branch or not git_ops.ref_exists(branch, cwd=cwd):
            continue
        count = _extra_commit_count(branch, upstream, upstream, cwd=cwd)
        if count is None or count > 0:
            return True
    return False


def merged_pr_block_message(
    record: tracking.WorktreeRecord, content_ref: str | None, upstream: str, *, cwd: str,
) -> str:
    """Compose ``_pr_finalize_precondition``'s merged-but-blocked message,
    distinguishing a *confirmed* later commit from an *unverifiable* merge
    boundary (#4400 review findings, rounds 8 + 11 + 14): the fail-closed
    branches in ``content_exceeds_merged_head`` block on an inconclusive
    lookup too -- an unresolvable ``head_sha``/``upstream``, or ANY ref
    cleanup will force-delete (not just ``content_ref``), each checked
    against ITS OWN corresponding PR's ``head_sha`` (never a shared/active
    one) -- and unconditionally saying "carries further commits" there would
    send an operator hunting for a commit that may not exist, or at the
    wrong ref entirely. Only reports "confirmed" when a genuine positive
    count resolves on some checked ref; every other case -- including one
    that failed closed elsewhere -- reports the unverifiable message.
    """
    from . import git_ops
    if git_ops.ref_exists(upstream, cwd=cwd):
        active_head_sha = (getattr(record.pr, "head_sha", "") or "").strip()
        pairs = (
            [(content_ref, active_head_sha)] if content_ref else []
        ) + _cleanup_branch_refs(record)
        checked: set[str] = set()
        for ref, head_sha in pairs:
            if (
                not ref or ref in checked or not head_sha
                or not git_ops.ref_exists(ref, cwd=cwd)
                or not git_ops.ref_exists(head_sha, cwd=cwd)
            ):
                continue
            checked.add(ref)
            count = _extra_commit_count(ref, head_sha, upstream, cwd=cwd)
            if count is not None and count > 0:
                return f"Worktree/{record.worktree_id}: PR merged but carries further commits."
    return (
        f"Worktree/{record.worktree_id}: PR merged, but its merge boundary "
        f"can't be verified (unresolvable head_sha/upstream/checkout ref) -- "
        f"refusing rather than certifying unverified content safe."
    )


def _cleanup_branch_refs(record: tracking.WorktreeRecord) -> list[tuple[str, str]]:
    """Every ``(ref, head_sha)`` pair finalize's cleanup will delete for this
    worktree, beyond whichever single ref was chosen as the live-content
    safety ref (#4400 rounds 11 + 13 + 14): the TRACKED worktree branch --
    ``record.branch`` when set (a legacy/non-canonical name; the same
    resolution ``finalize._worktree_branch`` uses at cleanup time), else
    ``worktree/<id>`` -- checked against the ACTIVE PR's ``head_sha`` (the
    one that record tracks), plus every OTHER tracked PR's local feature
    branch (force-deleted unconditionally, serial + parallel -- see
    ``validate_and_finalize``'s cleanup block) checked against THAT PR's OWN
    ``head_sha``, never the active one's: a historical/parallel PR's branch
    can carry a commit that is an ancestor of the ACTIVE PR's head (and so
    invisible to a shared boundary) yet never actually reached upstream.

    A legacy flow can reuse the SAME local branch name across sequential
    PRs on one long-lived worktree (``tracking._merge_pr_attribution_state``
    documents this exact reuse pattern for ``pr_id``-less records) -- the
    live git ref for that name can only ever point at the MOST RECENT push,
    never an earlier one. ``record.prs`` is NOT guaranteed to be
    chronological though (``tracking.WorktreeRecord.active_pr`` defines
    recency by ``opened_at``, only falling back to list position as a
    tie-breaker -- concurrent-save reconciliation can append an older
    unmatched on-disk entry after a newer in-memory one, #4699): iterating
    raw list order for a reused name can select an older SHA and recreate
    the exact false positive this fix exists to close. Entries for a
    reused name are therefore processed in the SAME recency order
    ``active_pr`` itself uses (``opened_at``, then original list index) --
    an already-advanced branch tip paired against a stale, superseded
    ``head_sha`` from an earlier reuse read every commit made for a LATER
    reuse of that name as "extra" commits beyond that stale boundary,
    falsely blocking finalize on content that had already landed.
    Processing in recency order and overwriting on each repeat occurrence
    keeps whichever entry is chronologically LAST for any reused branch
    name.

    The LATEST tracked entry for a reused name is not necessarily the one
    that actually merged either (#4699): a REJECTED (closed, never merged)
    reuse can be the most recent, with its own unmerged commit as the
    branch's current tip -- pairing it unconditionally would read "zero
    extra commits" and certify that unmerged commit safe. A
    confirmed-terminal-non-merge entry (``tracking._pr_is_terminal`` true,
    ``state`` not ``"merged"``) therefore never overwrites an existing
    pairing that ISN'T itself such a rejection; only a later entry that
    could plausibly represent real landed work (merged, or still open/
    unresolved) replaces it.
    """
    def _confirmed_rejected(pr) -> bool:
        state = (getattr(pr, "state", "") or "").strip()
        return state not in tracking._PR_NON_TERMINAL and state != "merged"

    active_head_sha = (getattr(record.pr, "head_sha", "") or "").strip()
    tracked = (getattr(record, "branch", "") or "").strip() or f"worktree/{record.worktree_id}"
    pairs: list[tuple[str, str]] = [(tracked, active_head_sha)]
    index_by_branch = {tracked: 0}
    rejected_by_index = {0: False}
    all_prs = list(getattr(record, "prs", None) or [])
    # Same recency ordering as tracking.WorktreeRecord.active_pr: oldest
    # opened_at (missing -> "") first, original list position as the
    # tie-breaker -- never raw append order, which concurrent-save
    # reconciliation does not guarantee is chronological.
    ordered_prs = sorted(
        enumerate(all_prs),
        key=lambda item: ((getattr(item[1], "opened_at", "") or ""), item[0]),
    )
    for _original_index, pr in ordered_prs:
        branch = (getattr(pr, "branch", "") or "").strip()
        if not branch:
            continue
        head_sha = (getattr(pr, "head_sha", "") or "").strip()
        rejected = _confirmed_rejected(pr)
        if branch in index_by_branch:
            idx = index_by_branch[branch]
            if rejected and not rejected_by_index[idx]:
                continue
            pairs[idx] = (branch, head_sha)
            rejected_by_index[idx] = rejected
        else:
            index_by_branch[branch] = len(pairs)
            rejected_by_index[len(pairs)] = rejected
            pairs.append((branch, head_sha))
    return pairs


def _extra_commit_count(
    content_ref: str, head_sha: str, upstream: str, *, cwd: str,
) -> int | None:
    """Count of commits on ``content_ref`` beyond ``head_sha``/``upstream``,
    or ``None`` when the count itself couldn't be determined (unresolvable
    ``content_ref``, or a failed ``rev-list`` -- including an unresolvable
    ``upstream``). Callers must treat ``None`` as inconclusive, never zero.
    """
    from . import git_ops
    if not git_ops.ref_exists(content_ref, cwd=cwd):
        return None
    extra = git_ops.git(
        "rev-list", "--count", content_ref, f"^{head_sha}", f"^{upstream}",
        cwd=cwd, check=False,
    )
    if extra.returncode != 0:
        return None
    stripped = extra.stdout.strip()
    return int(stripped) if stripped.isdigit() else None


def content_exceeds_merged_head(
    record: tracking.WorktreeRecord, content_ref: str | None, upstream: str, *, cwd: str,
) -> bool:
    """True iff ``content_ref`` carries commits beyond the tracked PR's merged
    head that are NOT already reachable from ``upstream``.

    ``content_ref`` must be the caller's LIVE-checkout resolution
    (``finalize_open_pr_gate.resolve_precondition_ref``, #4400), never
    ``_resolve_content_ref``'s feature-branch-preferring result: under the
    ``snapshot`` head scheme that branch is a frozen snapshot of the PR head
    at push time, distinct from the live checkout (#1804 keeps HEAD on
    ``worktree/<id>`` under refspec; a legacy flow can leave HEAD on the
    feature branch itself). Comparing against a frozen snapshot would
    silently miss later commits on the real checkout and let finalize prune
    real, un-PR'd work.
    Excluding commits already reachable from ``upstream`` (not just
    ``head_sha``) avoids a false positive after a normal squash merge or
    ``pr-complete``/``sync`` realignment: ``content_ref`` can legitimately
    sit on a later, upstream-confirmed commit (the squash commit itself, or
    a forward rebase) that is not an ancestor of the pre-merge ``head_sha``
    but *is* safely on ``upstream`` -- that must never count as "exceeds."
    Fail-closed: a missing/unresolvable ``head_sha``, a ``None``/unresolvable
    ``content_ref``, or a failed ``rev-list`` all count as "exceeds" -- never
    certifies newer work safe on an inconclusive check.
    """
    head_sha = (getattr(record.pr, "head_sha", "") or "").strip()
    if not head_sha or content_ref is None:
        return True
    count = _extra_commit_count(content_ref, head_sha, upstream, cwd=cwd)
    return count is None or count > 0


def content_exceeds_merged_head_any(
    record: tracking.WorktreeRecord, content_ref: str | None, upstream: str, *,
    cwd: str, repo=None,
) -> bool:
    """True iff ``content_ref`` OR any branch finalize's cleanup will
    force-delete carries commits beyond ITS OWN corresponding PR's merged
    head not already reachable from ``upstream`` (#4400 rounds 11 + 14).

    ``content_exceeds_merged_head`` alone only validates the ONE ref chosen
    as the live-content safety ref -- but ``validate_and_finalize``'s cleanup
    unconditionally force-deletes every ``record.prs[*].branch`` (the local
    feature branch) once this precondition passes, regardless of which ref
    was checked. Validating only ``content_ref`` can certify safety while
    separate unmerged commits sitting solely on a feature branch cleanup
    will delete go unvalidated. Each OTHER PR's branch is checked against
    THAT PR's own ``head_sha`` (never the active PR's, round 14) -- sharing
    one boundary across unrelated PRs can mask real content via unrelated
    ancestry. A tracked branch with no ``head_sha`` to validate against
    fails closed -- UNLESS that missing ``head_sha`` can itself be repaired
    first: when ``repo`` is supplied, :func:`repair_other_tracked_pr_heads`
    attempts a provider-confirmed repair of every OTHER tracked PR's
    missing ``head_sha`` before the per-branch check runs, so a worktree
    carrying more than one merged PR isn't permanently blocked here just
    because a non-active entry's cached head was never populated.
    ``repo`` is optional (defaults to ``None``, skipping the repair) so
    callers that already know no repair is possible/needed can omit it.
    """
    if content_ref is None:
        return True
    if repo is not None:
        try:
            repair_other_tracked_pr_heads(record, repo)
        except Exception:
            pass
    if content_exceeds_merged_head(record, content_ref, upstream, cwd=cwd):
        return True
    from . import git_ops
    for ref, head_sha in _cleanup_branch_refs(record):
        if ref == content_ref or not git_ops.ref_exists(ref, cwd=cwd):
            continue
        if not head_sha:
            return True
        count = _extra_commit_count(ref, head_sha, upstream, cwd=cwd)
        if count is None or count > 0:
            return True
    return False


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


def pr_precondition_recheck(
    record: tracking.WorktreeRecord,
    repo,
    worktree_path: str,
    anchor: str,
    *,
    precondition_fn,
) -> str | None:
    """Re-run the dirty-tree guard + full PR-mode precondition (#4400 round
    16). Returns an error message on failure, else ``None``. Shared by
    finalize's pre-reconciliation AND last-moment re-checks -- callers own
    the rollback + early return. ``precondition_fn`` is
    ``finalize._pr_finalize_precondition`` -- passed in rather than imported
    to avoid a hard cross-module cycle at import time.
    """
    from pathlib import Path
    dirty_err = dirty_worktree_error(worktree_path, wt_exists=Path(worktree_path).exists())
    if dirty_err:
        return dirty_err
    ok, err = precondition_fn(record, repo, worktree_path, anchor)
    return None if ok else (err or "PR finalize precondition not met.")
