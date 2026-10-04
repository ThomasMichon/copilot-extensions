"""Shared claims-prominence ranking (picker-venue-pivots effort).

Given a worktree's claim ledger (``tracking_claims.ResourceClaim`` entries,
or equivalent plain dicts), selects and orders the "1-2 prominent" claims a
Picker row's claims-list column should show -- one ranking every
claims-showing pivot (Worktrees, Tasks, Codespaces, Containers) consumes
identically, rather than each pivot inventing its own selection. See
``visions/venue-pivots-ux``'s "Claims pecking order" concept in the
``copilot-extensions`` repo for the full design rationale.

Organizing principle: **human-mappable, quick-find first** -- an entity a
human can recognize and act on by its own external identity (a PR number, a
bug number) outranks one that is only ever meaningful inside this fabric (a
dispatch task id has no independently-referenceable identity outside it).

Starting order (operator-supplied 2026-09-21; explicitly tunable). Two ways
to tune it: edit ``DEFAULT_PECKING_ORDER`` below directly (repo-local), or
-- since 2026-09-21 -- let any installed plugin **contribute** its own
priority via a ``claim-kinds/*.json`` drop-in, discovered by the companion
``claim_kinds_registry`` module and passed into ``rank_claims``'s
``pecking_order=`` parameter. Either way every consumer (Worktrees, Tasks,
Codespaces, Containers) picks up the same order identically -- this module
itself never reads a plugin drop-in (stays pure/I/O-free; see below).

    PR > bug/issue > effort > bridge > CodeSpace/container > child worktree
    > machine SSH > dispatch task

**Kind-vocabulary gap (grounded against the real code, 2026-09-21):**
``claims_cli._claims_add``'s ``valid_kinds`` today is only
``{worktree, codespace, container, ssh, workdir, pr, task}`` -- "bug"/
"issue", "effort", "bridge", and "session" (added 2026-09-29) are **not yet
claimable kinds**. This module ranks whatever kind is actually present in a
ledger; a kind this repo cannot yet produce a claim for simply never
appears here (no fabrication). Adding a "bug"/"issue" claim kind is a
prerequisite of the vision's own workspace-PR auto-claim work, not
something this module does.

**Deliberately pure, no I/O.** This module never scans a filesystem, reads
a plugin's installed files, or imports anything beyond the standard library
-- it is pecking-order *arithmetic* only. A plugin contributing a new
claimable kind (and the priority it should rank at) is a *discovery*
concern, owned by the sibling ``claim_kinds_registry`` module (a ``.d/``
drop-in scanner mirroring the Worktree Picker's own ``pivots/<name>.json``
contribution pattern) -- that module does the I/O and hands this one a
plain ``{kind: priority}`` mapping via ``pecking_order=``.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

# Lower rank == more prominent. A kind absent from this table (including one
# not yet in claims_cli's own `valid_kinds`) falls back to the lowest rank in
# whichever table is actually in effect (see `_default_rank`) -- ranked below
# every named tier, but never raises -- so an unrecognized or future claim
# kind degrades gracefully instead of breaking every consuming pivot.
DEFAULT_PECKING_ORDER: dict[str, int] = {
    "pr": 0,
    "bug": 1,
    "issue": 1,        # same tier as "bug" -- both are ADO/GitHub work items
    "effort": 2,
    "bridge": 3,
    "codespace": 4,
    "container": 4,    # CodeSpace and container share a tier (venue parity)
    "worktree": 5,     # a claimed CHILD worktree
    "ssh": 6,          # a claimed machine SSH session
    "task": 7,         # a dispatch task -- no independently referenceable
                       # id outside this fabric
    "session": 8,      # lowest -- not yet a claimable kind (see the
                       # module docstring's kind-vocabulary-gap note), but
                       # ranked here for when it becomes one: a worktree's
                       # own session is the LEAST differentiating claim,
                       # since every worktree effectively has one
                       # (operator feedback, 2026-09-29).
}

# The same "still held" vocabulary `tracking_claims.ResourceClaim.is_live`
# uses (active/at-rest are live; released/abandoned are not). Duplicated
# here (not imported) so this module stays usable against a plain dict
# ledger read from a JSON surface (e.g. `worktree-status`'s own `claims`
# fact) without needing the dataclass import.
_LIVE_STATES: tuple[str, ...] = ("", "active", "at-rest")


def _field(claim: Any, name: str) -> Any:
    if isinstance(claim, Mapping):
        return claim.get(name)
    return getattr(claim, name, None)


def _is_live(claim: Any) -> bool:
    """True unless the claim is explicitly released/abandoned/etc.

    Prefers an ``is_live`` property when the caller already has a real
    ``ResourceClaim`` (or anything duck-typed the same way); falls back to
    checking ``state`` directly against the same live-state vocabulary for a
    plain dict read off a JSON surface.
    """
    if not isinstance(claim, Mapping):
        is_live_attr = getattr(claim, "is_live", None)
        if is_live_attr is not None:
            return bool(is_live_attr)
    state = _field(claim, "state") or ""
    return state in _LIVE_STATES


def _default_rank(pecking_order: Mapping[str, int]) -> int:
    """One past the lowest (least-prominent) rank actually declared in
    ``pecking_order`` -- so an unrecognized kind always sorts after every
    declared tier in *whichever* table is in effect (the built-in default,
    or a caller-supplied/plugin-augmented one), never a stale constant sized
    to a different table."""
    return (max(pecking_order.values()) + 1) if pecking_order else 0


def _rank_of(kind: str, pecking_order: Mapping[str, int]) -> int:
    return pecking_order.get(kind, _default_rank(pecking_order))


def rank_claims(
    claims: Iterable[Any],
    *,
    limit: int | None = 2,
    live_only: bool = True,
    pecking_order: Mapping[str, int] | None = None,
) -> list[tuple[str, str]]:
    """The ``limit`` most prominent ``(kind, ref)`` pairs from ``claims``,
    ordered by ``pecking_order`` (default: :data:`DEFAULT_PECKING_ORDER`).
    Ties (same kind) keep their original ledger order (stable sort).
    ``limit=None`` returns every live claim, fully ordered -- the "full
    graph on drill-in" case.

    ``claims`` is any iterable of ``ResourceClaim``-like objects or plain
    dicts carrying at least ``kind``/``ref`` (and, when ``live_only``, a
    ``state``/``is_live`` signal). A claim missing ``kind`` or ``ref`` is
    silently skipped -- never raises on a malformed ledger entry.

    Pass ``pecking_order=claim_kinds_registry.effective_pecking_order()`` to
    include any installed plugin's own claim-kind contributions; omit it to
    use the built-in table alone (this module never scans for contributions
    itself -- see the module docstring).
    """
    table = pecking_order if pecking_order is not None else DEFAULT_PECKING_ORDER
    items: list[tuple[str, str]] = []
    for claim in claims:
        kind = _field(claim, "kind")
        ref = _field(claim, "ref")
        if not kind or not ref:
            continue
        if live_only and not _is_live(claim):
            continue
        items.append((str(kind), str(ref)))
    ranked = sorted(
        enumerate(items), key=lambda pair: (_rank_of(pair[1][0], table), pair[0])
    )
    ordered = [item for _, item in ranked]
    return ordered if limit is None else ordered[:limit]


#: Short-form kind prefix for every non-PR-like claimable kind (operator
#: feedback, 2026-09-29): a claims-list cell must stay scannable across many
#: different kinds sharing one narrow column, so each kind gets a terse,
#: fixed-width label rather than its full name. ``pr``/``bug``/``issue`` are
#: deliberately ABSENT here -- their own ref (``"#N"``/``"repo#N"``) is
#: self-explanatory on its own (see :func:`format_claim`'s special case),
#: and prefixing it with a redundant "PR"/"bug" word wastes the column's
#: scarce width. ``session`` is included even though it is not yet a
#: claimable kind (see the module docstring), so the label is ready the
#: moment a producer starts emitting one.
DEFAULT_LABEL_PREFIX: dict[str, str] = {
    "worktree": "WT",
    "session": "SESS",
    "codespace": "CS",
    "container": "CT",
    "task": "T",
    "bridge": "BR",
    "ssh": "SSH",
    "effort": "EFF",
}

#: Kinds whose ``ref`` is a PR/issue reference -- either the
#: ``"owner/repo#N"`` shape ``claims_cli``/the create-pr auto-claim writes, or
#: a full PR/issue URL from GitHub OR any other forge (e.g. a hand-added
#: claim, or a ref recovered from an external system). :func:`_parse_pr_like_ref`
#: accepts all three.
_PR_LIKE_KINDS = frozenset({"pr", "bug", "issue"})

#: Phase 6 canonical-ref shape (``worktree-claims-transitive-finalization``
#: effort, 2026-10-04): ``"<kind>:<system>:<key>"``. Duplicated from
#: ``tracking_claims.decanonicalize_ref`` as a tiny local unwrap rather than
#: an import -- this module stays import-free of siblings (see the module
#: docstring) so it works against a plain dict ledger too. Every parser
#: below unwraps at its own entry point, exactly mirroring
#: ``tracking_claims``'s own call sites, so a canonical-form ref is accepted
#: identically whether it reaches this module directly or via a sibling.
_CANONICAL_REF_RE = re.compile(r"^([a-z][a-z0-9_-]*):([^:]*):(.+)$")
_CLAIM_KINDS_FOR_CANON = frozenset({
    "worktree", "session", "codespace", "container", "task", "bridge",
    "ssh", "effort", "workdir", "pr", "bug", "issue",
})


def _decanonicalize_ref(
    ref: str, *, expected_kinds: frozenset[str] | None = None,
) -> str:
    """Unwrap a Phase 6 canonical ``"<kind>:<system>:<key>"`` ref back to
    its legacy shape, or return ``ref`` unchanged for anything else
    (including a URL's own ``https:`` scheme, which never matches a known
    kind). For ``"worktree"``/``"session"``, ``system`` (the machine) is
    re-prefixed onto ``key`` to reconstruct the full
    ``machine/project/worktree_id[#session]`` legacy grammar; every other
    kind's ``key`` already IS its legacy ref.

    ``expected_kinds``, when given, restricts unwrapping to a ref whose
    embedded kind this call site actually understands -- mirrors
    ``tracking_claims.decanonicalize_ref``'s own ``expected_kinds``
    parameter (see its docstring for why: unconditional unwrapping would
    let an unrelated kind's ref masquerade as this call site's own shape,
    e.g. a ``task``-kind ref unwrapping into something that reads like a
    PR shorthand)."""
    if not ref:
        return ref
    m = _CANONICAL_REF_RE.match(ref)
    if not m:
        return ref
    found_kind, system, key = m.groups()
    if found_kind not in _CLAIM_KINDS_FOR_CANON:
        return ref
    if expected_kinds is not None and found_kind not in expected_kinds:
        return ref
    if found_kind in ("worktree", "session"):
        return f"{system}/{key}" if system else key
    return key

_GITHUB_PR_URL_RE = re.compile(
    r"^https?://github\.com/([^/\s]+/[^/\s]+)/(?:pull|issues)/(\d+)(?:[/?#].*)?$"
)
#: Generic PR/issue URL shape for any OTHER forge -- Gitea/Forgejo use
#: ``pulls`` (plural); Azure DevOps uses ``pullrequest`` (see
#: ``providers/azure_devops.py``'s ``_pr_web_url``) and some hosts use the
#: generic ``pull-requests``; GitLab uses ``merge_requests``, optionally
#: behind a literal ``/-/`` separator, with an arbitrary-depth
#: ``group/subgroup/.../project`` namespace (not just a flat ``owner/repo``);
#: all of the above (and GitHub) use ``issues`` for issue refs. The
#: ``pull(?:s|-?requests?)?`` alternation mirrors
#: :func:`claims_find_cli._pr_number`'s own established PR-URL-shape
#: vocabulary, so both call sites agree on what counts as a supported PR URL.
#: Host-agnostic: it only extracts the owner/repo-ish path and the number for
#: a short ``#N`` label (and the cross-repo check) -- it never reconstructs a
#: URL for a foreign host (see :func:`claim_url`, which hyperlinks the
#: ORIGINAL url as-is instead).
_GENERIC_PR_URL_RE = re.compile(
    r"^https?://[^/\s]+/([^/\s]+(?:/[^/\s]+)*?)/(?:-/)?"
    r"(?:pull(?:s|-?requests?)?|merge_requests|issues)/(\d+)(?:[/?#].*)?$"
)


def _repo_short_name(repo: str | None) -> str | None:
    """The trailing ``repo`` segment of an ``"owner/repo"`` (or bare
    ``"repo"``) string, or ``None`` for empty input -- ``WorktreeRecord.repo``
    and :func:`tracking_claims.format_claim_ref`'s ``project`` are bare repo
    names (no owner), while a PR's own ``repo`` field is full ``"owner/repo"``
    (per ``PRRecord.repo``'s own doc comment) -- this normalizes both to the
    same bare form so the two can be compared for the cross-repo check."""
    if not repo:
        return None
    return repo.rsplit("/", 1)[-1] or None


def _is_cross_repo(own_repo: str | None, candidate_repo: str | None) -> bool:
    """True only when BOTH repos are known and their short names differ --
    an unknown side (``own_repo`` not passed by an older caller, or a claim
    whose ref carries no repo segment) never asserts cross-repo, since that
    would be a guess, not a fact."""
    own = _repo_short_name(own_repo)
    cand = _repo_short_name(candidate_repo)
    return bool(own and cand and own != cand)


def _parse_pr_like_ref(ref: str) -> tuple[str | None, str | None]:
    """``(owner_repo, number)`` from a PR/bug/issue ``ref`` -- either the
    ``"owner/repo#N"`` shape (repo optional: a bare ``"#N"`` is valid too), a
    full GitHub PR/issue URL, or a full PR/issue URL from any other forge
    (Gitea/Forgejo's ``pulls``, GitLab's ``merge_requests``, or ``issues`` on
    any of them). ``(None, None)`` when no shape matches (never raises on a
    malformed/foreign ref)."""
    if not ref:
        return None, None
    stripped = _decanonicalize_ref(ref.strip(), expected_kinds=_PR_LIKE_KINDS)
    m = _GITHUB_PR_URL_RE.match(stripped) or _GENERIC_PR_URL_RE.match(stripped)
    if m:
        return m.group(1), m.group(2)
    if "#" in stripped:
        owner_repo, _, number = stripped.rpartition("#")
        number = number.strip()
        if number.isdigit():
            return (owner_repo.strip() or None), number
    return None, None


def claim_url(kind: str, ref: str) -> str | None:
    """The fully-qualified URL a claim's short label can hyperlink to, or
    ``None`` when this claim kind/ref carries no independently-resolvable
    URL (a CodeSpace name, a dispatch task id, an unqualified ``"#N"`` PR ref
    with no repo to build a URL from). A ref that is ALREADY a full URL (any
    forge/host) is hyperlinked as-is -- never rewritten to a reconstructed
    ``github.com`` URL, which would silently point a non-GitHub ref at the
    wrong site. Only the bare ``"owner/repo#N"`` shorthand (which is
    GitHub-only by the convention ``claims_cli``/create-pr writes it in) gets
    a synthesized ``github.com`` URL."""
    if kind in _PR_LIKE_KINDS:
        stripped = (
            _decanonicalize_ref(ref.strip(), expected_kinds=frozenset({kind}))
            if ref else ""
        )
        if stripped.startswith("http://") or stripped.startswith("https://"):
            return stripped
        owner_repo, number = _parse_pr_like_ref(stripped)
        if owner_repo and number:
            path = "pull" if kind == "pr" else "issues"
            return f"https://github.com/{owner_repo}/{path}/{number}"
        return None
    if ref and (ref.startswith("http://") or ref.startswith("https://")):
        return ref
    return None


def _parse_worktree_ref(ref: str) -> tuple[str | None, str | None]:
    """``(project, worktree_id)`` from a ``"worktree"``-kind claim ref.

    Mirrors ``tracking_claims.format_claim_ref``'s ``"machine/project/
    worktree_id[#session]"`` convention structurally (module docstring: this
    file stays import-free of siblings so it works against a plain dict
    ledger too) -- duplicated here as a tiny local parse rather than an
    import. ``(None, None)`` for a ref with fewer than 3 ``/``-segments (an
    older or hand-added ref with no embedded project)."""
    if not ref:
        return None, None
    body = _decanonicalize_ref(
        ref, expected_kinds=frozenset({"worktree"}),
    ).partition("#")[0]
    parts = body.split("/")
    if len(parts) >= 3:
        return parts[1] or None, "/".join(parts[2:]) or None
    return None, None


def format_claim(
    kind: str,
    ref: str,
    *,
    own_repo: str | None = None,
    label_overrides: Mapping[str, str] | None = None,
) -> str:
    """A short, human-readable label for one ``(kind, ref)`` claim.

    Cross-repo-aware (#3307 worktrees-pivot-ux-overhaul follow-up, operator
    feedback): ``own_repo`` (the CLAIMING worktree's own repo) is compared
    against the claim's own repo, when the claim's ref carries one, via
    :func:`_is_cross_repo` -- a repo that can't be determined on either side
    never asserts cross-repo (never a guess).

    * ``pr``/``bug``/``issue`` -- **no DEFAULT kind prefix** (operator
      feedback, 2026-09-29): the ref alone is self-explanatory for the
      ordinary GitHub-sourced case. ``"#2481"`` same-repo (or repo
      unknown); ``"sample-repo#2481"`` cross-repo (short repo name, no
      owner -- the number alone is the point, the repo name is the only
      NEW information cross-repo actually adds). Parses a full PR/issue URL
      ref -- from GitHub or any other forge (Gitea/Forgejo, GitLab) -- the
      same as the ``"owner/repo#N"`` shape (:func:`_parse_pr_like_ref`). A
      plugin-contributed
      ``label_overrides[kind]`` (e.g. an ADO-sourced "bug" wanting to read
      distinctly from a native GitHub issue) still applies and IS
      prefixed -- only the unlabeled default is bare.
    * ``worktree`` -- ``"WT <last4>"`` same-repo, ``"WT <repo>:<last4>"``
      cross-repo (operator's own example: ``"copilot-extensions:4b8a"``,
      now prefixed), reading the sibling's project from the
      ``tracking_claims.format_claim_ref`` ref convention (see
      :func:`_parse_worktree_ref`). Falls through to the generic
      ``#N``/bare-ref rule below when the ref doesn't parse that way (an
      older or hand-added ref with no embedded project).
    * every other kind (session/bridge/codespace/container/ssh/task) --
      unchanged: prefers a trailing ``#<number>`` already in ``ref``, else
      the bare ``ref``, prefixed by
      ``label_overrides``/:data:`DEFAULT_LABEL_PREFIX`.
    """
    prefix = (
        label_overrides[kind]
        if label_overrides and kind in label_overrides
        else DEFAULT_LABEL_PREFIX.get(kind, kind)
    )
    # Phase 6 canonical-ref support (worktree-claims-transitive-finalization
    # effort, 2026-10-04): unwrap once up front so the generic
    # ``#N``/bare-ref fallback below (the only branch with no kind-specific
    # unwrap of its own) also accepts a canonical-form ref identically to
    # its legacy shape, not just the worktree/PR-like branches.
    ref = (
        _decanonicalize_ref(ref, expected_kinds=frozenset({kind})) if ref else ref
    )
    if kind == "worktree":
        project, worktree_id = _parse_worktree_ref(ref)
        if worktree_id:
            last4 = worktree_id[-4:]
            if project and _is_cross_repo(own_repo, project):
                return f"{prefix} {project}:{last4}"
            return f"{prefix} {last4}"
    if kind in _PR_LIKE_KINDS:
        owner_repo, number = _parse_pr_like_ref(ref)
        if number:
            # Only a plugin-contributed override is prefixed -- pr/bug/issue
            # carry no DEFAULT prefix (removed from DEFAULT_LABEL_PREFIX),
            # so an unlabeled kind falls through to bare here.
            override = label_overrides.get(kind) if label_overrides else None
            if owner_repo and _is_cross_repo(own_repo, owner_repo):
                short_ref = f"{_repo_short_name(owner_repo)}#{number}"
                return f"{override} {short_ref}" if override else short_ref
            return f"{override} #{number}" if override else f"#{number}"
    if "#" in ref:
        _, _, number = ref.rpartition("#")
        number = number.strip()
        if number:
            return f"{prefix} #{number}"
    return f"{prefix} {ref}".strip()


def summarize_claims(
    claims: Iterable[Any],
    *,
    limit: int = 2,
    sep: str = " \u00b7 ",
    live_only: bool = True,
    pecking_order: Mapping[str, int] | None = None,
    own_repo: str | None = None,
    label_overrides: Mapping[str, str] | None = None,
) -> str:
    """The row-ready ``claims_summary`` string a Codespaces/Containers/Tasks
    pivot's own backend command computes and hands to the Picker (e.g.
    ``"PR #2481 \u00b7 bug #2410"``) -- ``rank_claims`` + ``format_claim``,
    joined. ``""`` for an empty/all-non-live ledger (graceful-absence, never
    a placeholder). See :func:`rank_claims` for ``pecking_order`` and
    :func:`format_claim` for ``own_repo``/``label_overrides``."""
    top = rank_claims(
        claims, limit=limit, live_only=live_only, pecking_order=pecking_order
    )
    return sep.join(
        format_claim(kind, ref, own_repo=own_repo, label_overrides=label_overrides)
        for kind, ref in top
    )


def claim_entries_for_worktree(
    resources: Iterable[Any],
    active_pr: Mapping[str, Any] | None = None,
    *,
    limit: int = 2,
    pecking_order: Mapping[str, int] | None = None,
    own_repo: str | None = None,
    label_overrides: Mapping[str, str] | None = None,
) -> list[dict[str, str | None]]:
    """Like :func:`claims_summary_for_worktree`, but returns the ranked
    ``[{"label": ..., "url": ...}]`` list instead of one joined string --
    for a consumer (the Worktrees pivot's CLAIMS column) that renders each
    claim as its own hyperlinked segment rather than a single flat string.
    ``url`` is ``None`` when the claim kind/ref has no resolvable URL (see
    :func:`claim_url`). Shares the PR-backfill logic with
    :func:`claims_summary_for_worktree` via :func:`_claims_with_pr_backfill`
    so the two never drift apart on which claims they consider."""
    claims = _claims_with_pr_backfill(resources, active_pr)
    top = rank_claims(claims, limit=limit, pecking_order=pecking_order)
    return [
        {
            "label": format_claim(kind, ref, own_repo=own_repo,
                                   label_overrides=label_overrides),
            "url": claim_url(kind, ref),
        }
        for kind, ref in top
    ]


def _claims_with_pr_backfill(
    resources: Iterable[Any], active_pr: Mapping[str, Any] | None,
) -> list[Any]:
    """The shared backfill step :func:`claims_summary_for_worktree` and
    :func:`claim_entries_for_worktree` both need -- factored out so the two
    can never drift on which claims they rank. See
    :func:`claims_summary_for_worktree`'s own docstring for the backfill
    rationale."""
    claims = list(resources)
    if (active_pr and active_pr.get("number")
            and active_pr.get("state") not in ("merged", "closed")
            and not any(_field(c, "kind") == "pr" and _is_live(c) for c in claims)):
        repo = active_pr.get("repo")
        number = active_pr["number"]
        ref = f"{repo}#{number}" if repo else f"#{number}"
        claims.append({"kind": "pr", "ref": ref, "state": "active"})
    return claims


def claims_summary_for_worktree(
    resources: Iterable[Any],
    active_pr: Mapping[str, Any] | None = None,
    *,
    limit: int = 2,
    pecking_order: Mapping[str, int] | None = None,
    own_repo: str | None = None,
    label_overrides: Mapping[str, str] | None = None,
) -> str:
    """The Worktrees pivot's own ranked ``claims_summary`` (#3307
    worktrees-pivot-ux-overhaul Phase 4) -- :func:`summarize_claims` over
    ``resources`` (a worktree's claim ledger), with one addition:
    ``active_pr`` (an optional ``{repo, number, state}`` mapping -- the
    back-compat single active PR ``WorktreeRecord.active_pr()`` already
    resolves) is backfilled as a synthetic live ``pr`` claim when
    ``resources`` carries no live ``pr`` claim of its own yet. This covers a
    worktree whose PR predates the ``create-pr``-time auto-claim
    (``worktree_ops_cli._claim_from_run_output``) without needing a one-time
    migration. Never backfills a merged/closed PR -- :func:`summarize_claims`
    would filter it as non-live anyway (default ``live_only=True``).

    Kept in this module (not the caller) so the backfill logic stays next to
    the ranking it feeds, and so a caller need only pass already-extracted
    plain data -- this module remains pure/I-O-free per its own docstring;
    ``resources``/``active_pr`` may be real ``ResourceClaim``/``PRRecord``
    objects or plain dicts, both duck-typed identically to every other
    entry point here."""
    claims = _claims_with_pr_backfill(resources, active_pr)
    return summarize_claims(
        claims, limit=limit, pecking_order=pecking_order, own_repo=own_repo,
        label_overrides=label_overrides,
    )
