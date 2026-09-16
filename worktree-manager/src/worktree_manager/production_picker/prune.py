"""Closure-descriptor interpretation shim for the Worktree Picker.

``picker_tui/derive.py`` resolves ``from .. import prune`` to this module, one
level up from ``picker_tui`` to ``production_picker`` -- mirroring
``agent_worktrees/picker_tui``'s own ``from .. import prune`` (that resolves
to ``agent_worktrees/prune.py``). Worktree Manager has no runtime dependency
on the ``agent-worktrees`` plugin package, so this is a small, deliberately
duplicated copy of just the one function the Picker needs, not an import of
the plugin's module.

Only ``interpret_descriptor_payload`` (plus the ``DESCRIPTOR_VERSION`` it
gates on) is needed by ``derive.py`` -- the rest of ``agent_worktrees/
prune.py`` (finalization/cleanup planning) has no Picker-side caller and is
deliberately not duplicated here. Keep this function byte-for-byte in sync
with ``agent_worktrees/prune.py``'s copy whenever that one changes -- a
dedicated cross-copy version-parity test (see
``tests/production_picker/test_prune_shim.py``) fails CI immediately if the
two ``DESCRIPTOR_VERSION`` values ever diverge.
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

    A version-matched payload is still validated structurally before being
    trusted: ``closure``/``action``/``claims``/``follow_ups`` must each be
    present mappings; ``label``/``style``/``action.disposition``/``compact``
    must be actual ``str`` and ``closure.final`` an actual ``bool`` (never
    truthiness-coerced from a wrong type); ``label == "FINAL"`` must agree
    with ``closure.final``; a ``final: True`` payload must carry zero held
    claims, zero open follow-ups, and a ``safe`` action; and
    ``claims.held``/``follow_ups.open`` must be genuine non-negative ints
    (never a malformed value laundered into a ``0`` that would look like
    verified evidence of no blockers). Any violation degrades the whole
    payload to unsupported. Kept in sync with ``agent_worktrees/prune.py``'s
    copy.

    Returns a normalized view:
    ``{"supported": bool, "final": bool, "label": str, "style": str,
    "compact": str, "held_claims": int, "open_follow_ups": int,
    "action_disposition": str, "reason": str | None}``.
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
    for field_name, field_value in (
        ("closure", closure), ("action", action),
        ("claims", claims), ("follow_ups", follow_ups),
    ):
        if not isinstance(field_value, dict):
            return _unsupported_descriptor(
                f"unsupported-descriptor:{field_name}-missing-or-not-a-mapping")
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
    if (label == "FINAL") != final_value:
        return _unsupported_descriptor("unsupported-descriptor:label-final-mismatch")
    held_claims = _non_negative_int(claims.get("held", 0))
    open_follow_ups = _non_negative_int(follow_ups.get("open", 0))
    if held_claims is None or open_follow_ups is None:
        return _unsupported_descriptor("unsupported-descriptor:invalid-count")
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
    payload`` rejection path. Kept in sync with ``agent_worktrees/prune.py``'s
    copy."""
    return {
        "supported": False, "final": False, "label": "UNKNOWN",
        "style": "unknown", "compact": "UNKNOWN",
        "held_claims": 0, "open_follow_ups": 0,
        "action_disposition": "blocked", "reason": reason,
    }


def _non_negative_int(value) -> int | None:
    """Validate a claim/follow-up count as a non-negative int, returning
    ``None`` for anything else (a string, a list, ``None``, a bool, or a
    negative number) rather than silently coercing it to ``0``. Kept in sync
    with ``agent_worktrees/prune.py``'s copy."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None
