"""Local obligation gates shared by finalization entrypoints."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tracking import WorktreeRecord


def assert_producers_settled(
    record: WorktreeRecord | None,
    worktree_id: str,
    *,
    emit_error: Callable[[str], None],
) -> bool:
    """Apply non-bypassable producer and pair gates before and during cleanup."""
    from . import paired_claims
    if not paired_claims.assert_settled(record, emit_error=emit_error):
        return False
    if record is not None and any(c.has_producer and c.is_unsettled for c in record.resources):
        emit_error(
            f"Worktree {worktree_id} owns unsettled protected producer obligations; "
            "verified close or explicitly accepted handoff is required. "
            "Abandon and obligation-gate mode cannot bypass this ownership."
        )
        return False
    return True


def assert_obligations_settled(
    record: WorktreeRecord | None,
    worktree_id: str,
    *,
    abandon: bool,
    handoff_to: str | None = None,
    emit_error: Callable[[str], None],
    emit_warning: Callable[[str], None],
    format_ref: Callable[[str, str, str], str],
) -> bool:
    """Obligation gate (resource-obligation-settlement Phase 2).

    A worktree answers for the outbound resources it still owns before it may
    finalize. Reads the **local ledger** (`record.resources`) for **unsettled**
    (``active``) claims -- a cheap, local, no-traversal balance check (a settled
    child has already flipped its claim to ``at-rest``/``released``).

    The legacy ``off``/``warn``/``block`` setting no longer weakens ownership:
    any unsettled resource **refuses** finalize unless ``abandon`` carries an
    affirmative handoff target. The setting may still shape surrounding
    diagnostics, but cannot release creator responsibility.

    ``abandon`` overrides a block only with a non-empty ``handoff_to`` recipient
    or flow: it proceeds and re-homes the unsettled obligations (the downstream
    ``release_all_resources`` marks the claims released and surfaces them for
    the named cleanup/adoption flow). Returns True to proceed, False to block
    the finalize. Degrade-safe: no record / no resources -> proceed.

    Finalize never auto-reclaims a creator's children. A crashed/missed
    settlement is handled explicitly through ``claims sweep`` or an
    operator-directed handoff, preserving affirmative ownership.
    """
    if record is None:
        return True
    if not assert_producers_settled(record, worktree_id, emit_error=emit_error):
        return False
    try:
        from . import claim_handoffs
        from .record_machine_identity import execution_machine

        source_ref = format_ref(
            execution_machine(record), record.repo, record.worktree_id)
        active_handoffs = claim_handoffs.active_bundle_ids_for_source(
            source_ref)
    except Exception as exc:
        emit_error(
            f"Cannot verify claim-handoff registry for {worktree_id}; finalize "
            f"is refused fail-closed ({exc}).")
        return False
    if active_handoffs:
        emit_error(
            f"Worktree {worktree_id} owns {len(active_handoffs)} nonterminal "
            "claim-handoff bundle(s); finalize is refused until each is "
            "accepted, declined, or cancelled: "
            + ", ".join(active_handoffs)
        )
        return False
    # A "session" claim (Phase 8, worktree-finality-and-obligations) never
    # hard-blocks finalize -- it gets its own advisory-only pass
    # (`_advise_other_live_sessions`), not this refusal gate. Finalize itself
    # settles the invoking session's own claim before this check ever runs
    # (see `validate_and_finalize`).
    unsettled = [
        c for c in record.resources if c.is_unsettled and c.kind != "session"
    ]
    if not unsettled:
        return True
    pending = [c for c in unsettled if c.ref.startswith("pending-run:")]
    if pending:
        emit_error(
            f"Worktree {worktree_id} has {len(pending)} in-flight resource "
            "creation reservation(s). Pending resources cannot be abandoned or "
            "handed off before their real identity is journaled; wait for the "
            "creating command or repair its pending claim."
        )
        return False
    offered = [c for c in unsettled if c.handoff_bundle]
    if offered:
        emit_error(
            f"Worktree {worktree_id} has {len(offered)} claim(s) reserved in "
            "offered handoff bundles. Offered responsibility cannot be settled, "
            "abandoned, or released before consumer acceptance or decline/cancel."
        )
        return False
    if abandon and not (handoff_to or "").strip():
        emit_error(
            f"Worktree {worktree_id} cannot abandon {len(unsettled)} unsettled "
            "obligation(s) without an affirmative handoff target. The creating "
            "agent remains responsible: close them now, or after explicit "
            "operator direction pass --abandon --handoff-to <recipient-or-flow>."
        )
        return False

    def _describe() -> None:
        for c in unsettled:
            label = f"  · {c.kind}: {c.ref}"
            if c.note:
                label += f" ({c.note})"
            print(label)

    if not abandon:
        emit_error(
            f"Worktree {worktree_id} still owns {len(unsettled)} unsettled "
            f"resource obligation(s) -- finalize is blocked "
            "(creator ownership cannot be disabled by obligation-gate mode):"
        )
        _describe()
        emit_error(
            "Close each resource out (a cross-repo worktree: finalize it; a "
            "CodeSpace/container: merge or move its work off-box; a bridge: drive "
            "it to final), then retry -- or pass --abandon to re-home them."
        )
        return False

    emit_warning(
        f"Abandoning {len(unsettled)} unsettled resource obligation(s) owned by "
        f"{worktree_id}"
        + f" -- handed to {(handoff_to or '').strip()}:"
    )
    _describe()
    return True
