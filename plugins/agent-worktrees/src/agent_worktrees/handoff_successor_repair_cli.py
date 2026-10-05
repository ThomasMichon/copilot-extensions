"""``resolve-handoff-successor`` -- the sanctioned repair verb for issue #4557.

A terminal (``kind`` in ``tracking.MANAGED_KINDS``, status finalized/complete/
completed) worktree can end up with a stale ``pending`` handoff whose real
successor session ran to completion (verifiable on-disk transcript, matching
cwd) but was never ``register-session``'d against the record -- a crash or
race in that registration step. Neither ``register-session`` nor
``status --resolved`` will touch a terminal+managed record (by design: that
gate protects every live session's sessionStart-hook-critical path from an
accidental new activation on a worktree that's already done), and
``link-succession`` requires the successor to already be a tracked
``SessionEntry`` -- exactly the thing that's missing here. Hand-editing the
tracking YAML was the only prior option, which the ``repairing-worktrees``
skill explicitly calls out as a Single-Writer-Contract violation.

This module owns the two things the write-path verb
(``tracking_session_lifecycle_write.apply_resolve_handoff_successor``)
deliberately does NOT do itself: resolving the worktree across projects and
the on-disk evidence check (a real, non-detached session transcript whose
recorded cwd matches this worktree) that justifies retroactively registering
a session the record has never seen. The verb only re-validates the
tracking-record-side invariants under its own lock.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import sessions, tracking
from . import output


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "resolve-handoff-successor",
        help="Repair a terminal managed worktree's stale pending handoff by "
        "retroactively registering its real (but never-registered) successor "
        "session -- the sanctioned alternative to hand-editing tracking YAML",
    )
    p.add_argument("worktree_id", help="Worktree id (resolved across all projects)")
    p.add_argument(
        "--token", dest="token", required=True,
        help="The handoff token to resolve (must currently be 'pending')",
    )
    p.add_argument(
        "--successor", dest="successor", required=True,
        help="The successor session id that actually consumed the handoff "
        "(must NOT already be tracked on this worktree -- use link-succession "
        "for an already-tracked successor)",
    )
    p.add_argument("--json", action="store_true", help="JSON output")


def cmd_resolve_handoff_successor(args: argparse.Namespace) -> int:
    """Retroactively register ``--successor`` and link it to ``--token``.

    Fails closed on any of: worktree not found, worktree not terminal+managed,
    token not pending, successor already tracked, an unsafe/unvalidated
    ``--successor`` id, a successor session that is still live (its lifecycle
    belongs to whatever process is still running it, not this repair), a
    successor with no real turns beyond the handoff seed itself (an unconsumed
    seed is not a legitimate successor -- see the ``repairing-worktrees``
    skill's class-G check), or -- the evidence check this command alone is
    responsible for -- no real, non-detached session transcript for
    ``--successor`` recorded against this worktree's own ``worktree_path``,
    whose first turn is not itself a handoff-seed prompt. A dead/gone
    checkout directory does not exempt the cwd check: the recorded cwd
    string is compared lexically (``Path.resolve``, non-strict), not by
    requiring the directory to still exist.
    """
    from . import session_tracking_cli as stc

    raw = args.worktree_id
    yaml_path = stc._find_tracking_file(raw)
    if yaml_path is None:
        return output._json_error(f"Worktree not found: {raw}")
    record = tracking.load_record(yaml_path)
    if not record.worktree_path:
        return output._json_error(
            f"resolve-handoff-successor: {raw} has no recorded worktree_path "
            "to verify the successor's cwd against"
        )
    valid_successor = sessions.validate_session_id(args.successor)
    if valid_successor is None:
        return output._json_error(
            f"resolve-handoff-successor: {args.successor!r} is not a real, "
            "path-safe local session id with on-disk conversation data -- "
            "refusing to register a session with no on-disk evidence"
        )
    meta = sessions._session_meta(sessions._session_state_dir(), valid_successor)
    if meta is None:
        return output._json_error(
            f"resolve-handoff-successor: no real session transcript found for "
            f"{valid_successor!r} (missing, detached, or no conversation data) "
            "-- refusing to register a session with no on-disk evidence"
        )
    if meta.get("live"):
        return output._json_error(
            f"resolve-handoff-successor: session {valid_successor!r} is still "
            "live -- refusing to retroactively conclude a session that is "
            "still running; wait for it to end, or use its own conclude path"
        )
    recorded_cwd = str(meta.get("cwd") or "").strip()
    if not recorded_cwd:
        return output._json_error(
            f"resolve-handoff-successor: session {valid_successor!r} has no "
            "recorded cwd -- cannot verify it ran against this worktree"
        )
    try:
        matches = Path(recorded_cwd).expanduser().resolve(strict=False) == Path(
            record.worktree_path
        ).expanduser().resolve(strict=False)
    except OSError:
        matches = False
    if not matches:
        return output._json_error(
            f"resolve-handoff-successor: session {valid_successor!r}'s recorded "
            f"cwd ({recorded_cwd!r}) does not match {raw}'s worktree_path "
            f"({record.worktree_path!r}) -- refusing"
        )
    events = sessions.read_session_transcript(valid_successor)
    user_turns = [ev for ev in events if ev.get("type") == "user.message"]
    if not user_turns:
        return output._json_error(
            f"resolve-handoff-successor: session {valid_successor!r} has no "
            "user turns in its transcript -- cannot verify it is a real "
            "handoff successor rather than an unrelated session sharing this cwd"
        )
    first_turn_text = sessions._event_text(user_turns[0])
    has_seed_markers = (
        "/consume-handoff" in first_turn_text and "context-handoff" in first_turn_text
    )
    if not has_seed_markers:
        return output._json_error(
            f"resolve-handoff-successor: session {valid_successor!r}'s first "
            "turn is not the canonical handoff-seed prompt (missing both the "
            "'/consume-handoff' and 'context-handoff' markers) -- refusing to "
            "attribute an unrelated session to this handoff (repairing-"
            "worktrees skill's class-G check)"
        )
    if len(user_turns) < 2:
        return output._json_error(
            f"resolve-handoff-successor: session {valid_successor!r} only "
            "received the handoff seed and never progressed -- not a "
            "legitimate successor (repairing-worktrees skill's class-G check)"
        )

    from . import tracking_write

    try:
        result = stc._dispatch_session_lifecycle(
            "session_resolve_handoff_successor",
            {
                "worktree_id": raw,
                "yaml_path": str(yaml_path),
                "handoff_token": args.token,
                "successor_id": valid_successor,
                "expected_worktree_path": record.worktree_path,
            },
        )
    except tracking_write.AmbiguousWriteOutcome as exc:
        return output._json_error(
            f"resolve-handoff-successor: write to {raw} is in an unknown state: {exc}"
        )
    if result.get("error") == "lifecycle":
        return output._json_error(result["message"])
    output._json_output(result)
    return 0
