"""Production Picker's own `paused` glyph/field coverage -- mirrors
`agent_worktrees.picker_support.derive`'s `TestPausedGlyph` (that in-plugin
compatibility normalizer's tests), applied to the real deployed Picker
renderer this module vendors separately (`derive.py` is deliberately
excluded from the byte-identical transplant comparison, see
`test_production_picker_transplant.py`, so its behavior needs its own
coverage rather than relying on the other copy's tests alone).

`paused` is purely informational -- a title glyph + a field, never fed into
bucket()/the prune verdict (unlike `follow_up`).
"""

from __future__ import annotations

from worktree_manager.production_picker.picker_tui import derive


def _raw(**values):
    row = {
        "id": "child",
        "repo": "example",
        "status": "finalized",
        "started_at": "2026-01-01T00:00:00",
        "state": "completed",
    }
    row.update(values)
    return row


def test_paused_gets_glyph_and_field() -> None:
    row = derive.norm(_raw(paused=True, title="Nudge subsystem"), "host", "windows")
    assert row["title"].startswith("\u23f8 ")  # ⏸ prefix
    assert row["paused"] is True


def test_unpaused_has_no_glyph() -> None:
    row = derive.norm(_raw(title="Done"), "host", "windows")
    assert not row["title"].startswith("\u23f8")
    assert row["paused"] is False


def test_paused_state_stays_pure_for_bucketing() -> None:
    # The glyph never leaks into `state` (bucket()/prune key off it) --
    # proven by an unaffected state regardless of paused.
    unpaused = derive.norm(_raw(), "host", "windows")
    paused = derive.norm(_raw(paused=True), "host", "windows")
    assert paused["state"] == unpaused["state"]


def test_paused_and_follow_up_glyphs_compose() -> None:
    row = derive.norm(
        _raw(follow_up=True, paused=True, summary="x"), "host", "windows",
    )
    assert row["title"].startswith("\u23f8 \u271a ")  # ⏸ outside ✚
    assert row["follow_up"] is True
    assert row["paused"] is True
