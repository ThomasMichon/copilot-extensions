"""#93: picker ``derive.norm`` marks a worktree hosting a bare (un-muxed)
bound Copilot with a scannable orphan glyph + a boolean row field.

These cover only ``derive`` (no Textual import), so they run even where the
optional TUI dependency is absent.
"""

from __future__ import annotations

import datetime

from agent_worktrees.picker_tui import derive

_ORPHAN = "\u26a0"  # WARNING SIGN -- the orphan marker prefixed on the title


def _norm(**over):
    derive.NOW = datetime.datetime(2026, 7, 28, 12, 0, 0)
    raw = {
        "id": "anomalous-potato-win-20260728-abcd",
        "title": "Some work",
        "status": "active",
        "started_at": "2026-07-28T10:00:00",
        "turn_count": 1,
    }
    raw.update(over)
    return derive.norm(raw, "anomalous-potato", "Win")


def test_bare_orphan_sets_field_and_prefixes_glyph():
    r = _norm(session_bare_orphan=True)
    assert r["session_bare_orphan"] is True
    assert r["title"].startswith(_ORPHAN + " ")
    assert "Some work" in r["title"]


def test_no_orphan_no_glyph():
    r = _norm()
    assert r["session_bare_orphan"] is False
    assert not r["title"].startswith(_ORPHAN)


def test_orphan_glyph_is_outermost_with_follow_up():
    # Derive the follow-up glyph without hardcoding it, then assert the orphan
    # marker sits to its left (outermost).
    fu_glyph = _norm(follow_up=True)["title"][0]
    r = _norm(session_bare_orphan=True, follow_up=True)
    assert r["title"].startswith(_ORPHAN + " ")
    assert fu_glyph in r["title"]
    assert r["title"].index(_ORPHAN) < r["title"].index(fu_glyph)
