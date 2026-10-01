"""``pending_seed`` claim/restore primitives -- mechanical extraction from
``handoff_cli.py`` purely to control its module size. Moved verbatim, no
behavior change.
"""

from __future__ import annotations

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
                tracking.save_record(record, path)
            return seed or None
    except TimeoutError:
        return None


def restore_pending_seed(path: Path, seed: str) -> None:
    """Roll back an unconfirmed claim so a later attach can retry."""
    try:
        with tracking._RecordLock(path, require_sidecar=True):
            try:
                record = tracking.load_record(path)
            except Exception:
                return
            if not record.pending_seed:
                record.pending_seed = seed
                tracking.save_record(record, path)
    except TimeoutError:
        pass
