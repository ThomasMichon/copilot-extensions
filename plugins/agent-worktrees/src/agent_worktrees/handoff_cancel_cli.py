"""``agent-worktrees cancel-handoff`` -- targeted pending-handoff retirement.

Split out of ``session_binding_cli`` (which owns the paired ``note-handoff``)
purely to stay under this repo's module-size cap; the two commands are each
other's counterpart and should be read together.
"""

from __future__ import annotations

import argparse
import os

from . import tracking
from . import tracking_lifecycle
from . import config as cfg
from . import status_updater_cli


def _core():
    """Lazily resolve ``agent_worktrees.__main__`` -- see ``pr_cli._core``."""
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _json_output(*args, **kwargs):
    return _core()._json_output(*args, **kwargs)


def _activate_project_for_path(*args, **kwargs):
    return _core_helper("_activate_project_for_path", status_updater_cli._activate_project_for_path)(*args, **kwargs)


def _activate_project_for_worktree_id(*args, **kwargs):
    return _core_helper(
        "_activate_project_for_worktree_id", status_updater_cli._activate_project_for_worktree_id
    )(*args, **kwargs)


def _resolve_worktree_id(*args, **kwargs):
    return _core()._resolve_worktree_id(*args, **kwargs)


def add_parsers(sub) -> None:
    """Register the ``cancel-handoff`` subcommand."""
    sp = sub.add_parser(
        "cancel-handoff",
        help="Cancel ONE pending handoff ledger entry by its exact token -- "
        "the targeted counterpart to note-handoff, for an explicit external "
        "retirement (e.g. context-handoff's abort) before consumption",
    )
    sp.add_argument(
        "--token",
        required=True,
        help="The exact handoff token to cancel -- same value note-handoff's --task recorded",
    )
    sp.add_argument(
        "--worktree-dir",
        dest="worktree_dir",
        default=None,
        help="The worktree checkout dir (default: cwd)",
    )
    sp.add_argument(
        "--worktree-id", default=None, help="Worktree ID (alternative to --worktree-dir)"
    )


def cmd_cancel_handoff(args: argparse.Namespace) -> int:
    """Cancel one pending handoff ledger entry by its exact token. Idempotent:
    a token that's already cancelled/linked, or was never opened, reports
    `cancelled: false` rather than raising -- a caller retrying an abort
    should see a clean result, not an error."""
    wt_id = getattr(args, "worktree_id", None)
    wdir = getattr(args, "worktree_dir", None) or os.getcwd()
    if wt_id:
        # Always (re)activate the OWNING project for an explicit ID -- never
        # only when no project is active. If a DIFFERENT project already
        # happens to be active (e.g. abort running from inside a different
        # adopted checkout), skipping relocation would read cfg.tracking_dir()
        # for the wrong project and either miss the exact ledger entry or
        # target a coincidentally matching id in the wrong project.
        if not _activate_project_for_worktree_id(wt_id):
            _json_output({
                "cancelled": False,
                "reason": f"could not find the adopted project that owns worktree '{wt_id}'",
            })
            return 1
        try:
            wt_id = _resolve_worktree_id(wt_id)
        except Exception as exc:
            _json_output({"cancelled": False, "reason": f"could not resolve worktree '{wt_id}': {exc}"})
            return 1
    else:
        _activate_project_for_path(wdir)
        try:
            wt_id = tracking.find_worktree_id_by_cwd(wdir)
        except Exception:
            wt_id = None
    if not wt_id:
        _json_output({"cancelled": False, "reason": "not a tracked worktree"})
        return 0

    token = (getattr(args, "token", None) or "").strip()
    if not token:
        _json_output({"cancelled": False, "reason": "a token is required"})
        return 1

    try:
        yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        with tracking._RecordLock(yaml_path):
            record = tracking.load_record(yaml_path)
            handoff = next((h for h in record.handoffs if h.token == token), None)
            cancelled = tracking_lifecycle.cancel_handoff(record, token)
            if cancelled:
                tracking.save_record(record, yaml_path)
    except Exception as exc:
        _json_output({"cancelled": False, "worktree_id": wt_id, "reason": str(exc)})
        return 1

    _json_output({
        "cancelled": cancelled,
        "worktree_id": wt_id,
        "token": token,
        # Surfaced regardless of `cancelled` -- a caller that must fence a
        # SEPARATE destructive action (e.g. context-handoff's task-backed
        # abort) on "no successor is mid-pickup" needs to distinguish "no
        # matching pending entry at all" from "a candidate is already
        # associated, mid-cutover" -- `cancelled: false` alone conflates
        # both (PR #4570 review round 17).
        "candidate": handoff.candidate if handoff else None,
    })
    return 0
