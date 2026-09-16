"""Closure-descriptor interpretation shim for the transplanted Picker.

``picker_tui/derive.py`` is a byte-identical transplant of
``agent_worktrees/picker_tui/derive.py`` (see
``tests/test_production_picker_transplant.py::test_transplanted_picker_sources_match_production_copy``),
so its ``from .. import prune`` must resolve here exactly as it does one
level up from ``agent_worktrees/picker_tui`` to ``agent_worktrees/prune``.

Only ``interpret_descriptor_payload`` (plus the ``DESCRIPTOR_VERSION`` it
gates on) is needed by the transplanted ``derive.py`` -- the rest of
``agent_worktrees/prune.py`` (finalization/cleanup planning) has no
Picker-side caller and is deliberately not duplicated here. Keep this
function byte-for-byte in sync with ``agent_worktrees/prune.py``'s copy
whenever that one changes.
"""
from __future__ import annotations

DESCRIPTOR_VERSION = 1


def interpret_descriptor_payload(payload: dict | None) -> dict:
    """Mixed-version fleet safety (Phase 5): interpret a raw closure-descriptor
    payload from a remote/cached source (e.g. a machine running an older or
    newer `agent-worktrees`) without trusting fields it may not understand.

    An absent (``None``/non-mapping), malformed, or version-mismatched
    payload is NEVER final or prune-safe -- it renders as an explicit
    ``unsupported-descriptor`` review state regardless of what the payload's
    own ``closure.final``/``action.disposition`` claim, so an out-of-version
    consumer degrades safely instead of guessing. Only an EXACT
    ``version == DESCRIPTOR_VERSION`` match is trusted (both an older and a
    newer version are rejected the same way -- neither side of a version
    skew can safely interpret the other's shape).

    Returns a normalized view:
    ``{"supported": bool, "final": bool, "label": str, "style": str,
    "compact": str, "held_claims": int, "open_follow_ups": int,
    "action_disposition": str, "reason": str | None}``.

    ``style``/``compact``/``held_claims``/``open_follow_ups`` are the fields a
    presentation surface (mux, Picker) needs to render the descriptor's exact
    marker counts and semantic color without redefining state -- an
    unsupported payload degrades them the same way as ``label``/``final``
    (a neutral ``"unknown"`` style, bare ``"UNKNOWN"`` compact text, zero
    counts), never fabricating a claim/follow-up count it can't verify.
    """
    if not isinstance(payload, dict):
        return {
            "supported": False, "final": False, "label": "UNKNOWN",
            "style": "unknown", "compact": "UNKNOWN",
            "held_claims": 0, "open_follow_ups": 0,
            "action_disposition": "blocked", "reason": "unsupported-descriptor",
        }
    version = payload.get("version")
    if version != DESCRIPTOR_VERSION:
        return {
            "supported": False, "final": False, "label": "UNKNOWN",
            "style": "unknown", "compact": "UNKNOWN",
            "held_claims": 0, "open_follow_ups": 0,
            "action_disposition": "blocked",
            "reason": f"unsupported-descriptor:version={version!r}",
        }
    closure = payload.get("closure")
    action = payload.get("action")
    claims = payload.get("claims")
    follow_ups = payload.get("follow_ups")
    closure = closure if isinstance(closure, dict) else {}
    action = action if isinstance(action, dict) else {}
    claims = claims if isinstance(claims, dict) else {}
    follow_ups = follow_ups if isinstance(follow_ups, dict) else {}
    return {
        "supported": True,
        "final": bool(closure.get("final", False)),
        "label": str(payload.get("label", "UNKNOWN")),
        "style": str(payload.get("style", "unknown")),
        "compact": str(payload.get("compact", payload.get("label", "UNKNOWN"))),
        "held_claims": _non_negative_int(claims.get("held", 0)),
        "open_follow_ups": _non_negative_int(follow_ups.get("open", 0)),
        "action_disposition": str(action.get("disposition", "blocked")),
        "reason": None,
    }


def _non_negative_int(value) -> int:
    """Coerce a claim/follow-up count from an untrusted descriptor payload to
    a non-negative int, never raising on a malformed value (e.g. a string, a
    list, ``None``, or a negative number) -- degrades to ``0`` instead of
    crashing the caller. Kept in sync with ``agent_worktrees/prune.py``'s
    copy."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0

