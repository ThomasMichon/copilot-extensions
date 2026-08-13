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

import asyncio
import datetime
import os
import re
import types

import pytest

pytest.importorskip("textual", reason="textual not installed (optional TUI dep)")

from agent_worktrees.picker_tui import capture as pcap  # noqa: E402
from agent_worktrees.picker_tui import derive  # noqa: E402
from agent_worktrees.picker_tui import obscure as pobs  # noqa: E402

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
_TOPBAR_RE = re.compile(r"^\s*Worktree Manager.*$")


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


def test_native_list_grid_parity(monkeypatch, tmp_path):
    """NF5-5 (#88): the swappable native ``OptionList`` data body renders the
    *same* character grid as the text-line body -- the whole point of a drop-in
    swap. Capture the home screen with ``AGENT_WORKTREES_PICKER_NATIVE_LIST`` OFF
    (text-line body) and ON (native OptionList) and assert the normalized grids
    are identical (styles differ -- the native cursor is amber vs the text
    body's reverse -- but the character grid is byte-for-byte the same). Also
    pins the native grid to the same golden."""
    _isolate_pivots(monkeypatch, tmp_path)
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_NATIVE_LIST", raising=False)
    off = _normalize(pcap.capture(_fixture_source(), live=False)["text"])
    monkeypatch.setenv("AGENT_WORKTREES_PICKER_NATIVE_LIST", "1")
    on = _normalize(pcap.capture(_fixture_source(), live=False)["text"])
    assert on == off
    assert on == _golden("worktrees_list.txt", on)


def test_native_list_multiselect_grid_parity(monkeypatch, tmp_path):
    """NF5-5 (#88): the native list renders the multi-select gutter identically to
    the text-line body. Mark both worktrees (so multi-select is active and the
    checkbox gutter renders) and assert native-OFF and native-ON grids match --
    the gutter is built from the same ``_build_data_vrows`` source, so the swap
    stays byte-identical even in multi-select mode."""
    _isolate_pivots(monkeypatch, tmp_path)

    async def _mark(scr, pilot):
        scr.wt_sel.replace({"aaaa", "bbbb"})
        scr.refresh()
        await pilot.pause()

    monkeypatch.delenv("AGENT_WORKTREES_PICKER_NATIVE_LIST", raising=False)
    off = _normalize(pcap.capture(_fixture_source(), live=False, prepare=_mark)["text"])
    monkeypatch.setenv("AGENT_WORKTREES_PICKER_NATIVE_LIST", "1")
    on = _normalize(pcap.capture(_fixture_source(), live=False, prepare=_mark)["text"])
    assert on == off


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


def _awaiting_source():
    """A fleet with one worktree parked on the operator (live_rest=awaiting).

    The state is deliberately ``active`` (blue), NOT ``wip`` (amber): the only
    amber (#d7af00) in this render must come from the awaiting-operator pulse
    accent, so the ANSI colour assertion below is a specific signal, not a
    WIP/state false positive.
    """
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")
    raws = [
        {"id": "lambda-core-win-20260627-cccc", "title": "Needs a decision",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 4, "state": "active",
         "live_intent": "picking a rendering option",
         "live_intent_at": "2026-06-27T17:59:00", "live_rest": "awaiting-operator",
         "live_rest_at": "2026-06-27T17:59:00"},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def test_awaiting_operator_renders_marker_and_pulse(monkeypatch, tmp_path):
    """#228 slice 3: an awaiting-operator worktree renders the scannable ⏳ title
    marker, the amber ⏳ live-pulse sub-line, and its intent text -- the "this
    needs me" cue is legible in the character grid, not only in colour."""
    _isolate_pivots(monkeypatch, tmp_path)
    caps = pcap.capture(_awaiting_source(), live=False)
    text = caps["text"]
    assert "\u23f3" in text                       # the ⏳ marker/glyph
    assert "picking a rendering option" in text   # the persisted intent sub-line
    # Tie the amber accent to the glyph: the awaiting pulse's ⏳ is painted with
    # bold #d7af00 (rgb 215;175;0), and (state is ``active``, not WIP) that amber
    # appears ONLY here -- so the SGR colour must sit on the same styled run as
    # the ⏳ glyph, not merely somewhere in the grid.
    ansi = caps["ansi"]
    assert "215;175;0" in ansi
    assert re.search(r"215;175;0[^\x1b]*\u23f3", ansi), (
        "the ⏳ pulse glyph is not painted with the awaiting amber accent")


def test_capture_is_deterministic(monkeypatch, tmp_path):
    _isolate_pivots(monkeypatch, tmp_path)
    first = pcap.capture(_fixture_source(), live=False)["text"]
    second = pcap.capture(_fixture_source(), live=False)["text"]
    assert first == second


def test_capture_modal_screenshots_a_native_modal(monkeypatch, tmp_path):
    """``capture_modal`` exports the COMPOSITED app (picker + an open native
    ``ModalScreen``) as an SVG. The native modals (#88 F4+) live on the app's
    screen stack, invisible to the ``PickerScreen.render()`` seams, so this
    app-level capture is what audits / A/B-compares a modal's appearance
    (#88 NF1). Opens the ⚙ Configuration menu and asserts its content is in the
    screenshot."""
    _isolate_pivots(monkeypatch, tmp_path)

    async def open_cfg(scr, pilot):
        scr.sel = ("CFG", 0)
        scr._activate()
        await pilot.pause()

    svg = pcap.capture_modal(_fixture_source(), open_cfg)
    assert svg.lstrip().startswith("<svg")
    # The modal's own content (the ⚙ Configuration menu's Profiles option) is in
    # the screenshot -- proving the composited app, not just the main screen,
    # was captured.
    assert "Profiles" in svg


# --- obscuring (shareable capture) -------------------------------------------

def _secret_dump():
    """One machine/env of raw list-json worktrees full of identifying data."""
    raws = [
        {"id": "SECRETHOST-win-20260101-dead", "machine": "SECRET-HOST",
         "platform": "windows", "status": "active",
         "started_at": "2026-06-01T00:00:00", "state": "active", "turn_count": 3,
         "session_count": 1, "title": "Top Secret Roadmap",
         "summary": "do not leak this classified summary",
         "branch": "worktree/classified-branch", "path": "/secret/checkout/path",
         "live_intent": "exfiltrating the mainframe",
         "pr": {"state": "open", "number": 8472,
                "url": "https://secret.example/exampleuser/private/pulls/8472",
                "branch": "pr/secret-branch", "head_sha": "deadbeefcafef00d"}},
        {"id": "SECRETHOST-win-20260101-beef", "machine": "SECRET-HOST",
         "platform": "windows", "status": "finalized",
         "completed_at": "2026-06-02T00:00:00", "started_at": "2026-06-01T00:00:00",
         "state": "completed", "title": "Another Confidential Thing"},
    ]
    return [("SECRET-HOST", "Win", True, raws)]


def test_obscured_source_scrubs_all_identifiers(monkeypatch, tmp_path):
    _isolate_pivots(monkeypatch, tmp_path)
    src = pobs.obscured_source(_secret_dump(), repo="my-project", branch="main")
    caps = pcap.capture(src, view="all", size=(120, 36), settle=0.0)
    words = caps["text"] + "\n" + caps["ansi"]
    blob = words + "\n" + caps["svg"]
    # alphabetic secrets must not appear anywhere (text/ansi/svg)
    for secret in ("SECRET-HOST", "Top Secret Roadmap", "Another Confidential",
                   "do not leak", "secret.example", "classified", "mainframe",
                   "deadbeef", "/secret/checkout", "exampleuser", "private"):
        assert secret not in blob, secret
    # the real PR number is replaced (check text/ansi; SVG carries coord numbers)
    assert "8472" not in words
    # the obscured labels ARE present
    assert "my-project" in caps["text"]
    assert "Nova" in caps["text"]


def test_obscured_source_aggregates_multiple_machines(monkeypatch, tmp_path):
    _isolate_pivots(monkeypatch, tmp_path)
    dumps = [
        ("host-a", "Win", True, [
            {"id": "a-win-0001", "machine": "host-a", "platform": "windows",
             "status": "active", "state": "active", "started_at": "2026-06-01T00:00:00"}]),
        ("host-b", "Linux", False, [
            {"id": "b-lin-0002", "machine": "host-b", "platform": "linux",
             "status": "active", "state": "active", "started_at": "2026-06-01T00:00:00"}]),
    ]
    src = pobs.obscured_source(dumps)
    assert len(src.load()) == 2
    codes = {m for _l, m, _e, _ok in src.machines()}
    assert codes == {"Nova", "Orbit"}


def test_capture_frames_returns_one_per_step(monkeypatch, tmp_path):
    _isolate_pivots(monkeypatch, tmp_path)
    frames = asyncio.run(pcap.capture_frames_async(
        _fixture_source(), [None, ["]"], ["["]], size=(118, 36)))
    assert len(frames) == 3
    assert all(f["svg"].lstrip().startswith("<svg") for f in frames)
