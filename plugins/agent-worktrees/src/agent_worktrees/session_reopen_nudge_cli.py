"""``userPromptSubmit`` hook: reactivate a non-active session claim.

Phase 8 (worktree-finality-and-obligations, session-claim lifecycle): a
worktree's own live Copilot session is a held ``kind="session"``
``ResourceClaim``. It is settled to ``at-rest`` on a successful ``finalize``
or handoff cutover and released on ``sessionEnd`` -- but the SAME process can
keep talking afterward (neither ``/clear`` nor ``/new`` fires
``sessionEnd``/``sessionStart``). This module is the reopen path: fired on
every submitted prompt, it reactivates the claim whenever it has gone
non-``active``, reusing the exact same ``add_resource_claim``/
``reopen_finalized_owner`` path ``register_session`` already relies on -- no
new reopen mechanism. Extracted from ``session_binding_cli`` (its own
"hook that nudges the operator" sibling, ``bind_nudge``) into its own module
to stay under the module-size cap.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import config as cfg
from . import obligations, tracking


def _core():
    from . import __main__ as core

    return core


def _read_hook_stdin(*args, **kwargs):
    return _core()._read_hook_stdin(*args, **kwargs)


def _activate_project_for_path(*args, **kwargs):
    return _core()._activate_project_for_path(*args, **kwargs)


def add_parsers(sub) -> None:
    sp = sub.add_parser(
        "session-reopen-nudge",
        help="Hook: reactivate this session's own claim if it has gone non-active "
        "(userPromptSubmit)",
    )
    sp.add_argument(
        "--session-id",
        default=None,
        help="Copilot session ID (default: COPILOT_AGENT_SESSION_ID, or --stdin payload)",
    )
    sp.add_argument(
        "--cwd", default=None, help="The session's working directory (default: process cwd)"
    )
    sp.add_argument(
        "--stdin",
        action="store_true",
        help="Read the Copilot userPromptSubmit JSON payload from stdin",
    )


def _session_reopen_nudge_decision(cwd: str, session_id: str | None) -> dict:
    """Reactivate this session's own Phase 8 ``session`` claim, if needed.

    Fires on every ``userPromptSubmit`` -- claim-state-based, not
    slash-command-based: it reacts to whatever the claim's CURRENT state is
    (not `active`), never to which slash command the host reports, so it is
    invariant to `/clear`-vs-`/new` semantics. Reuses the same
    ``add_resource_claim``/``reopen_finalized_owner`` path
    ``register_session`` already relies on -- no new reopen mechanism.

    A session id absent from this worktree's record at all is left alone
    (never fabricates a claim for an unknown session); an already-``active``
    claim is a silent no-op. Best-effort and always side-effect-only: the
    host discards a command-type ``userPromptSubmitted`` hook's text output,
    so the caller never inspects this function's return value for anything
    but a JSON-safe shape.
    """
    if not session_id:
        return {}
    try:
        _activate_project_for_path(cwd or os.getcwd())
    except Exception:
        pass
    try:
        wt_id = tracking.find_worktree_id_by_cwd(cwd or os.getcwd())
    except Exception:
        wt_id = None
    if not wt_id:
        return {}
    try:
        yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        if not yaml_path.exists():
            return {}
        with tracking._RecordLock(yaml_path):
            record = tracking.load_record(yaml_path)
            if record.session_entry(session_id) is None:
                return {}
            self_ref = tracking.format_claim_ref(
                record.machine, record.repo, record.worktree_id, session=session_id,
            )
            existing = next(
                (c for c in record.resources if c.ref == self_ref), None,
            )
            if existing is not None and existing.state == obligations.ACTIVE:
                return {}
            tracking.add_resource_claim(
                record,
                tracking.ResourceClaim(
                    kind="session", ref=self_ref, state=obligations.ACTIVE,
                    note="live Copilot session",
                ),
                save=False,
            )
            tracking.save_record(record, yaml_path)
    except Exception:
        pass
    return {}


def cmd_session_reopen_nudge(args: argparse.Namespace) -> int:
    """userPromptSubmit hook: reactivate a non-active session claim (JSON out)."""
    import json as _json

    def _emit(obj) -> int:
        try:
            sys.stdout.write(_json.dumps(obj))
        except Exception:
            pass
        return 0

    try:
        session_id = getattr(args, "session_id", None)
        cwd = getattr(args, "cwd", None)
        if getattr(args, "stdin", False):
            payload = _read_hook_stdin()
            if payload:
                session_id = session_id or payload.get("sessionId")
                cwd = cwd or payload.get("cwd") or payload.get("workingDirectory")
        if not session_id:
            session_id = os.environ.get("COPILOT_AGENT_SESSION_ID") or None
        return _emit(_session_reopen_nudge_decision(cwd or os.getcwd(), session_id))
    except Exception:
        return _emit({})
