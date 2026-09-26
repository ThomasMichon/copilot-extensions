"""``claims annotate`` -- attach/update a note on an existing outbound claim.

Split out of ``claims_cli.py`` to stay under that module's grandfathered
size ceiling (``tools/check-module-size.py``); this is otherwise plain
claims-CLI logic and imports back from it for the same helpers every other
verb there uses.
"""

from __future__ import annotations

import argparse

from . import activity, tracking
from . import config as cfg


def claims_annotate(
    args: argparse.Namespace,
    ref: str,
    infer_worktree_id,
    json_error,
    json_output,
    output,
) -> int:
    """Attach/update a human note on an already-existing outbound claim.

    Unlike ``claims add --note``, which only labels a claim at creation
    time, this updates the ``note`` on a claim that already exists --
    including one an underlying tool auto-created (e.g. a CodeSpace claimed
    by a provisioning helper, not by an explicit ``claims add``) -- without
    requiring release + re-add (copilot-extensions#2631).
    """
    note = getattr(args, "note", "") or ""
    if not note:
        msg = "claims annotate: --note is required"
        if args.json:
            return json_error(msg, 2)
        output.err(msg)
        return 2
    config = cfg.load_config()
    wt_id = infer_worktree_id(getattr(args, "release_worktree", None), config)
    rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    with tracking._RecordLock(rec_path, require_sidecar=True):
        rec = tracking.load_record(rec_path)
        match = next((c for c in rec.resources if c.ref == ref), None)
        if match is None:
            if args.json:
                return json_error(f"no outbound claim with ref: {ref}")
            output.err(f"no outbound claim with ref: {ref} on {wt_id}")
            return 1
        previous_note = match.note
        match.note = note
        kind = match.kind
        tracking.save_record(rec, rec_path)
    activity.log_event("claim_annotated", worktree_id=wt_id, kind=kind, ref=ref, note=note)
    if args.json:
        json_output(
            {"worktree_id": wt_id, "ref": ref, "note": note, "previous_note": previous_note}
        )
        return 0
    print(f"annotated outbound claim {ref} on {wt_id}: {note!r}")
    return 0
