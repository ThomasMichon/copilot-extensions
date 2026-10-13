"""Current-row publication adapters, called only by the owning producer."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .worktrees_projection import MAX_TEXT_BYTES

MAX_CURRENT_CLAIM_LINKS = 8


def populate_claims(row: dict, record, active_pr, *, current_only: bool) -> None:
    from . import claim_kinds_registry, claims_rank

    try:
        pecking_order = claim_kinds_registry.effective_pecking_order()
        label_overrides = claim_kinds_registry.effective_label_overrides()
    except Exception:
        if current_only:
            raise
        pecking_order = None
        label_overrides = None
    try:
        entries = claims_rank.claim_entries_for_worktree(
            record.resources, active_pr, own_repo=record.repo,
            pecking_order=pecking_order, label_overrides=label_overrides,
        )
    except Exception:
        if current_only:
            raise
        entries = []
    add_claims(row, record, entries, current_only=current_only)


def add_claims(row: dict, record, entries: list[dict], *, current_only: bool) -> None:
    if not current_only:
        summary = " \u00b7 ".join(entry["label"] for entry in entries)
        if summary:
            row["claims_summary"] = summary
            row["claims_links"] = entries
        return
    kinds: Counter[str] = Counter()
    for claim in record.resources:
        if (claim.state or "").strip() in {"", "active", "at-rest"}:
            kinds[(claim.kind or "").strip() or "resource"] += 1
    row["claims_assets"] = {
        "held_count": sum(kinds.values()),
        "by_kind": dict(kinds),
        "details_available": bool(kinds),
    }
    links = []
    size = 0
    for entry in entries[:MAX_CURRENT_CLAIM_LINKS]:
        label_size = len(entry["label"].encode("utf-8"))
        if size + label_size + (5 if links else 0) > MAX_TEXT_BYTES:
            break
        links.append(entry)
        size += label_size + (5 if len(links) > 1 else 0)
    row["claims_links"] = links
    row["claims_link_count"] = len(entries)
    row["claims_links_complete"] = len(links) == len(entries)
    if links:
        row["claims_summary"] = " \u00b7 ".join(entry["label"] for entry in links)


def materialize(
    records, *, serialize, observed_at: float, state_map: dict,
    mux_map: dict, session_ctx, bare_orphan_wts, bridge_live_wts,
) -> list[dict]:
    """Reuse engine semantics without admitting historical collections.

    File, session, git and ledger work is owned by this publication path,
    never by resident or advisory reads.
    """
    rows = []
    for record in records:
        wid = record.worktree_id
        row = serialize(
            record, current_only=True, state_info=state_map.get(wid),
            mux_info=mux_map.get(wid), session_ctx=session_ctx,
            bare_orphan_wts=bare_orphan_wts, bridge_live_wts=bridge_live_wts,
        )
        path = Path(record.worktree_path) if record.worktree_path else None
        row["checkout_present"] = bool(path and path.exists() and (path / ".git").exists())
        row["fact_provenance"] = {
            "record": {"quality": "current", "observed_at": observed_at},
            "git": {
                "quality": "current" if wid in state_map else "unavailable",
                "observed_at": observed_at if wid in state_map else None,
            },
            "mux": {
                "quality": "current" if wid in mux_map else "unavailable",
                "observed_at": observed_at if wid in mux_map else None,
            },
            "session": {
                "quality": "current" if session_ctx is not None else "unavailable",
                "observed_at": observed_at if session_ctx is not None else None,
            },
        }
        rows.append(row)
    return rows
