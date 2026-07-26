"""Auditable / testable rendering of the Worktree Picker (issue #86).

Exercises ``picker_tui.capture``: the picker is a *deterministic renderer*, so a
known fixture fleet yields a known character grid. These tests
 - snapshot the rendered **character grid** against a golden (layout + labels +
   focus), regenerate-able with ``AGENT_WORKTREES_UPDATE_GOLDENS=1``;
 - assert the **semantic state colour** is actually rendered into the grid (via
   the ANSI capture);
 - assert an **SVG screenshot** is produced for human/agent audit.

Realizes visions/picker Features/auditable-testable-rendering +
Behaviors/renderable-and-assertable-headless (rides on programmatic-parity).
"""
from __future__ import annotations

import datetime
import os
import re
import types

import pytest

pytest.importorskip("textual", reason="textual not installed (optional TUI dep)")

from agent_worktrees.picker_tui import capture as pcap  # noqa: E402
from agent_worktrees.picker_tui import derive  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "goldens", "picker")


def _fixture_source():
    """A hermetic two-worktree fleet (frozen clock; no git/SSH/subprocess)."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")
    raws = [
        {"id": "lambda-core-win-20260627-aaaa", "title": "Fix the thing",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 4, "state": "wip", "ahead": 2, "behind": 1},
        {"id": "lambda-core-win-20260620-bbbb", "title": "Old idle wt",
         "status": "active", "started_at": "2026-06-20T10:00:00",
         "turn_count": 0, "state": "unused"},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def _isolate_pivots(monkeypatch, tmp_path):
    """Empty pivot + plugin dirs so no locally-installed contributed pivot (e.g.
    a Tasks pivot) leaks into the grid -- keeps the golden environment-neutral."""
    pivots = tmp_path / "pivots"
    plugins = tmp_path / "plugins"
    pivots.mkdir()
    plugins.mkdir()
    monkeypatch.setenv("AGENT_WORKTREES_PIVOTS_DIR", str(pivots))
    monkeypatch.setenv("AGENT_WORKTREES_PLUGINS_DIR", str(plugins))


# The topbar title carries a volatile version string + "update available" flag;
# normalise it so the golden survives version bumps and update-state changes.
_TOPBAR_RE = re.compile(r"^\s*Agent Worktrees.*$")


def _normalize(grid: str) -> str:
    lines = grid.splitlines()
    if lines:
        lines[0] = "<<TOPBAR>>" if _TOPBAR_RE.match(lines[0]) else lines[0].rstrip()
    return "\n".join(ln.rstrip() for ln in lines) + "\n"


def _golden(name: str, actual: str) -> str:
    path = os.path.join(GOLDEN_DIR, name)
    if os.environ.get("AGENT_WORKTREES_UPDATE_GOLDENS"):
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(actual)
        return actual
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_worktrees_list_grid_matches_golden(monkeypatch, tmp_path):
    _isolate_pivots(monkeypatch, tmp_path)
    caps = pcap.capture(_fixture_source(), live=False)
    grid = _normalize(caps["text"])
    assert grid == _golden("worktrees_list.txt", grid)


def test_grid_renders_state_vocabulary(monkeypatch, tmp_path):
    _isolate_pivots(monkeypatch, tmp_path)
    text = pcap.capture(_fixture_source(), live=False)["text"]
    # The home pivot, the local machine tab, and the fixture's states are all in
    # the rendered grid -- states are legible as text, not only colour.
    assert "WORKTREES" in text
    assert "WIP" in text
    assert "UNUSED" in text


def test_ansi_capture_encodes_semantic_state_colour(monkeypatch, tmp_path):
    _isolate_pivots(monkeypatch, tmp_path)
    ansi = pcap.capture(_fixture_source(), live=False)["ansi"]
    # WIP is amber #d7af00 == rgb(215,175,0): the semantic state colour is
    # actually painted into the grid (validates colour-as-semantics, not just
    # the label text).
    assert "215;175;0" in ansi


def test_svg_capture_is_a_screenshot(monkeypatch, tmp_path):
    _isolate_pivots(monkeypatch, tmp_path)
    svg = pcap.capture(_fixture_source(), live=False)["svg"]
    stripped = svg.lstrip()
    assert stripped.startswith("<svg")
    assert "rich-terminal" in svg  # a real Rich terminal screenshot


def test_capture_is_deterministic(monkeypatch, tmp_path):
    _isolate_pivots(monkeypatch, tmp_path)
    first = pcap.capture(_fixture_source(), live=False)["text"]
    second = pcap.capture(_fixture_source(), live=False)["text"]
    assert first == second
