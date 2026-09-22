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

Starting order (operator-supplied 2026-09-21; explicitly tunable -- change
``_PECKING_ORDER`` below, every consumer picks up the new order identically):

    PR > bug/issue > effort > bridge > CodeSpace/container > child worktree
    > machine SSH > dispatch task

**Kind-vocabulary gap (grounded against the real code, 2026-09-21):**
``claims_cli._claims_add``'s ``valid_kinds`` today is only
``{worktree, codespace, container, ssh, workdir, pr, task}`` -- "bug"/
"issue", "effort", and "bridge" are **not yet claimable kinds**. This module
ranks whatever kind is actually present in a ledger; a kind this repo cannot
yet produce a claim for simply never appears here (no fabrication). Adding a
"bug"/"issue" claim kind is a prerequisite of the vision's own odsp-web PR
auto-claim work, not something this module does.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Lower rank == more prominent. A kind absent from this table (including one
# not yet in claims_cli's own `valid_kinds`) falls back to `_DEFAULT_RANK` --
# ranked below every named tier, but never raises -- so an unrecognized or
# future claim kind degrades gracefully instead of breaking every consuming
# pivot.
_PECKING_ORDER: dict[str, int] = {
    "pr": 0,
    "bug": 1,
    "issue": 1,        # same tier as "bug" -- both are ADO/GitHub work items
    "effort": 2,
    "bridge": 3,
    "codespace": 4,
    "container": 4,    # CodeSpace and container share a tier (venue parity)
    "worktree": 5,     # a claimed CHILD worktree
    "ssh": 6,          # a claimed machine SSH session
    "task": 7,         # a dispatch task -- lowest: no independently
                       # referenceable id outside this fabric
}
_DEFAULT_RANK = len(_PECKING_ORDER)

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


def _rank_of(kind: str) -> int:
    return _PECKING_ORDER.get(kind, _DEFAULT_RANK)


def rank_claims(
    claims: Iterable[Any],
    *,
    limit: int | None = 2,
    live_only: bool = True,
) -> list[tuple[str, str]]:
    """The ``limit`` most prominent ``(kind, ref)`` pairs from ``claims``,
    ordered by the shared pecking order. Ties (same kind) keep their
    original ledger order (stable sort). ``limit=None`` returns every live
    claim, fully ordered -- the "full graph on drill-in" case.

    ``claims`` is any iterable of ``ResourceClaim``-like objects or plain
    dicts carrying at least ``kind``/``ref`` (and, when ``live_only``, a
    ``state``/``is_live`` signal). A claim missing ``kind`` or ``ref`` is
    silently skipped -- never raises on a malformed ledger entry.
    """
    items: list[tuple[str, str]] = []
    for claim in claims:
        kind = _field(claim, "kind")
        ref = _field(claim, "ref")
        if not kind or not ref:
            continue
        if live_only and not _is_live(claim):
            continue
        items.append((str(kind), str(ref)))
    ranked = sorted(enumerate(items), key=lambda pair: (_rank_of(pair[1][0]), pair[0]))
    ordered = [item for _, item in ranked]
    return ordered if limit is None else ordered[:limit]


_LABEL_PREFIX: dict[str, str] = {
    "pr": "PR",
    "bug": "bug",
    "issue": "bug",
}


def format_claim(kind: str, ref: str) -> str:
    """A short, human-readable label for one ``(kind, ref)`` claim, e.g.
    ``"PR #2481"`` from ``kind="pr", ref="acme-org/sample-repo#2481"``.

    Prefers a trailing ``#<number>`` already present in ``ref`` (the common
    PR/issue reference shape); falls back to the bare ``ref`` for a claim
    kind with no such convention (a CodeSpace name, a worktree id).
    """
    prefix = _LABEL_PREFIX.get(kind, kind)
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
) -> str:
    """The row-ready ``claims_summary`` string a Codespaces/Containers/Tasks
    pivot's own backend command computes and hands to the Picker (e.g.
    ``"PR #2481 \u00b7 bug #2410"``) -- ``rank_claims`` + ``format_claim``,
    joined. ``""`` for an empty/all-non-live ledger (graceful-absence, never
    a placeholder)."""
    top = rank_claims(claims, limit=limit, live_only=live_only)
    return sep.join(format_claim(kind, ref) for kind, ref in top)
