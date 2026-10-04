"""Race-safe claim/restore primitives for a worktree record's
``pending_seed`` -- the first-turn prompt persisted at creation time
(``agent-worktrees create``/``resolve --new --seed``) and delivered by
whichever path first attaches a live Copilot session to that worktree.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import tracking


def claim_pending_seed(path: Path) -> str | None:
    """Atomically claim+clear a ``pending_seed`` (race-safe: see caller).

    Requires the cross-process sidecar lock -- ``_RecordLock``'s default
    mode silently degrades to an in-process-only lock on sidecar
    contention, which would let two processes both read+deliver the same
    seed; failing closed (nothing claimed) is safer than that."""
    try:
        with tracking._RecordLock(path, require_sidecar=True):
            try:
                record = tracking.load_record(path)
            except Exception:
                return None
            seed = getattr(record, "pending_seed", None)
            if seed:
                record.pending_seed = None
                record.pending_seed_revision = getattr(record, "pending_seed_revision", 0) + 1
                tracking.save_record(record, path)
            return seed or None
    except TimeoutError:
        return None


#: ``mux_seed_pane`` outcomes that prove no keystroke reached the pane: it
#: never typed (no confirmed-ready Copilot, or the pane was gone before it
#: did). Anything else -- even ``send-failed``, which can follow a partial
#: send -- may have left a draft, so retrying would append another copy.
_NOTHING_TYPED = frozenset({"not-ready-timeout", "pane-target-unresolved", "pane-target-lost"})


def nothing_typed(result: dict) -> bool:
    """True when a seed delivery provably typed nothing (or was never
    attempted), so its claimed pending seed may be restored for a retry."""
    return not result or (not result.get("sent") and result.get("reason") in _NOTHING_TYPED)


def settle_claim(path: Path, seed: str | None, result: dict) -> dict:
    """After delivering a claimed pending ``seed``: restore it for a later
    attach only when the delivery provably typed nothing, and say so
    (``seed_deferred``) -- the session is idle until then. A typed but
    unconfirmed seed may sit in the input as a draft -- another delivery would
    append a second copy -- so it is reported for recovery instead
    (``seed_unconfirmed``). Returns those report fields (``{}`` when there was
    no claim or it was submitted)."""
    if not seed or result.get("ok"):
        return {}
    report = {"seed_reason": result.get("reason") or "not-attempted"}
    if not nothing_typed(result):
        return {**report, "seed_unconfirmed": True}
    try:
        restore_pending_seed(path, seed)
    except Exception:
        pass
    return {**report, "seed_deferred": True}


def restore_pending_seed(path: Path, seed: str) -> None:
    """Roll back an unconfirmed claim so a later attach can retry.

    Unlike ``claim_pending_seed`` (where giving up on contention loses
    nothing -- the claim simply never happened), giving up here would
    permanently lose a prompt that was ALREADY claimed for a delivery that
    then failed. Retries the sidecar lock a few times before accepting
    that loss, rather than abandoning it after one 2s timeout."""
    for attempt in range(3):
        try:
            with tracking._RecordLock(path, require_sidecar=True):
                try:
                    record = tracking.load_record(path)
                except Exception:
                    return
                if not record.pending_seed:
                    record.pending_seed = seed
                    record.pending_seed_revision = getattr(record, "pending_seed_revision", 0) + 1
                    tracking.save_record(record, path)
                return
        except TimeoutError:
            if attempt < 2:
                time.sleep(1.0)
