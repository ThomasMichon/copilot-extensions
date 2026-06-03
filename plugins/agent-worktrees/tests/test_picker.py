"""Tests for agent_worktrees.picker — pure helper functions."""

from __future__ import annotations

import pytest

from agent_worktrees.picker import (
    ItemKind,
    MenuItem,
    PickResult,
    _build_line_map,
    _display_width,
    _truncate,
    _visible_len,
)


# ---------------------------------------------------------------------------
# _visible_len — ANSI stripping
# ---------------------------------------------------------------------------

class TestVisibleLen:
    def test_plain_text(self):
        assert _visible_len("hello") == 5

    def test_with_ansi(self):
        assert _visible_len("\033[0;32mhello\033[0m") == 5

    def test_empty(self):
        assert _visible_len("") == 0

    def test_only_ansi(self):
        assert _visible_len("\033[0m") == 0


# ---------------------------------------------------------------------------
# _display_width — Unicode-aware width
# ---------------------------------------------------------------------------

class TestDisplayWidth:
    def test_ascii(self):
        assert _display_width("hello") == 5

    def test_emoji(self):
        # Most emoji are fullwidth (2 columns)
        w = _display_width("\U0001f600")  # 😀
        assert w == 2

    def test_mixed(self):
        w = _display_width("hi \U0001f600")
        assert w == 5  # 3 ASCII + 2 for emoji


# ---------------------------------------------------------------------------
# _truncate — width-aware truncation
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_no_truncation_needed(self):
        assert _truncate("short", 10) == "short"

    def test_truncates_long_text(self):
        result = _truncate("a very long string here", 10)
        assert _display_width(result) <= 10
        assert result.endswith("\u2026")  # ellipsis character

    def test_exact_width(self):
        assert _truncate("12345", 5) == "12345"

    def test_width_one(self):
        result = _truncate("hello", 1)
        assert _display_width(result) <= 1


# ---------------------------------------------------------------------------
# _build_line_map — line index mapping
# ---------------------------------------------------------------------------

class TestBuildLineMap:
    def test_simple_items(self):
        items = [
            MenuItem("A", ItemKind.NORMAL),
            MenuItem("B", ItemKind.NORMAL),
        ]
        line_map = _build_line_map(items)
        assert len(line_map) == 2
        assert line_map[0] == (0, False)
        assert line_map[1] == (1, False)

    def test_items_with_subtitles(self):
        items = [
            MenuItem("A", ItemKind.NORMAL, subtitle="sub A"),
            MenuItem("B", ItemKind.NORMAL),
        ]
        line_map = _build_line_map(items)
        assert len(line_map) == 3
        assert line_map[0] == (0, False)   # A label
        assert line_map[1] == (0, True)    # A subtitle
        assert line_map[2] == (1, False)   # B label

    def test_empty_items(self):
        assert _build_line_map([]) == []

    def test_all_with_subtitles(self):
        items = [
            MenuItem("A", subtitle="sa"),
            MenuItem("B", subtitle="sb"),
            MenuItem("C", subtitle="sc"),
        ]
        line_map = _build_line_map(items)
        assert len(line_map) == 6


# ---------------------------------------------------------------------------
# Data model basics
# ---------------------------------------------------------------------------

class TestDataModels:
    def test_menu_item_defaults(self):
        item = MenuItem("Test")
        assert item.kind == ItemKind.NORMAL
        assert item.value is None
        assert item.subtitle is None

    def test_pick_result_defaults(self):
        result = PickResult()
        assert result.selected == -1
        assert result.profile_idx == 0
        assert result.command is None

    def test_item_kinds(self):
        assert ItemKind.NORMAL == "normal"
        assert ItemKind.ACTION == "action"
        assert ItemKind.DIMMED == "dimmed"
        assert ItemKind.SEPARATOR == "separator"
