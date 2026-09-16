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

DESCRIPTOR_VERSION = 2


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
        return _unsupported_descriptor("unsupported-descriptor")
    version = payload.get("version")
    if version != DESCRIPTOR_VERSION:
        return _unsupported_descriptor(f"unsupported-descriptor:version={version!r}")
    closure = payload.get("closure")
    action = payload.get("action")
    claims = payload.get("claims")
    follow_ups = payload.get("follow_ups")
    # A missing or wrong-shaped required nested field (e.g. no ``closure`` at
    # all, or ``closure: []``) is a malformed/truncated payload -- a genuine
    # descriptor always emits all four sections, so an absent one is never a
    # legitimate "nothing to report" case. Reject the whole payload as
    # unsupported rather than silently defaulting to ``{}`` and then trusting
    # the top-level ``label``/``style`` as if the payload were sound. Kept in
    # sync with ``agent_worktrees/prune.py``'s copy.
    for field_name, field_value in (
        ("closure", closure), ("action", action),
        ("claims", claims), ("follow_ups", follow_ups),
    ):
        if not isinstance(field_value, dict):
            return _unsupported_descriptor(
                f"unsupported-descriptor:{field_name}-missing-or-not-a-mapping")
    # Required scalar fields must be the exact type a genuine descriptor
    # always emits -- a truthy-but-wrong-type value (e.g. ``closure.final:
    # "false"``, a non-empty string that ``bool()`` would treat as True) must
    # never slip through as if it were sound. Kept in sync with
    # ``agent_worktrees/prune.py``'s copy.
    label = payload.get("label")
    style = payload.get("style")
    final_value = closure.get("final")
    action_disposition = action.get("disposition")
    if (not isinstance(label, str) or not isinstance(style, str)
            or not isinstance(final_value, bool)
            or not isinstance(action_disposition, str)):
        return _unsupported_descriptor("unsupported-descriptor:scalar-field-type")
    compact = payload.get("compact", label)
    if not isinstance(compact, str):
        return _unsupported_descriptor("unsupported-descriptor:scalar-field-type")
    # ``label == "FINAL"`` and ``closure.final`` must agree -- an inconsistent
    # combination (e.g. an empty ``closure: {}`` alongside a top-level
    # ``label: "FINAL"``) is exactly the malformed shape the safety contract
    # exists to catch; never trust either half in isolation.
    if (label == "FINAL") != final_value:
        return _unsupported_descriptor("unsupported-descriptor:label-final-mismatch")
    held_claims = _non_negative_int(claims.get("held", 0))
    open_follow_ups = _non_negative_int(follow_ups.get("open", 0))
    # A genuine descriptor only ever sets ``final: True`` alongside zero held
    # claims, zero open follow-ups, and a ``safe`` action -- a payload
    # claiming FINAL with any of those inconsistent is a contradictory,
    # malformed shape; never render a green FINAL with markers still
    # attached. Kept in sync with ``agent_worktrees/prune.py``'s copy.
    if final_value and (
        held_claims != 0 or open_follow_ups != 0 or action_disposition != "safe"
    ):
        return _unsupported_descriptor(
            "unsupported-descriptor:final-with-blockers")
    return {
        "supported": True,
        "final": final_value,
        "label": label,
        "style": style,
        "compact": compact,
        "held_claims": held_claims,
        "open_follow_ups": open_follow_ups,
        "action_disposition": action_disposition,
        "reason": None,
    }


def _unsupported_descriptor(reason: str) -> dict:
    """The shared degrade-to-neutral result for any ``interpret_descriptor_
    payload`` rejection path. Kept in sync with
    ``agent_worktrees/prune.py``'s copy."""
    return {
        "supported": False, "final": False, "label": "UNKNOWN",
        "style": "unknown", "compact": "UNKNOWN",
        "held_claims": 0, "open_follow_ups": 0,
        "action_disposition": "blocked", "reason": reason,
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

