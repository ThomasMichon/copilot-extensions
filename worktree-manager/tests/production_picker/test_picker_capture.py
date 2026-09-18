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

from worktree_manager.production_picker.picker_tui import capture as pcap  # noqa: E402
from worktree_manager.production_picker.picker_tui import derive  # noqa: E402
from worktree_manager.production_picker.picker_tui import obscure as pobs  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "goldens", "picker")


def _fixture_source():
    """A hermetic two-worktree fleet (frozen clock; no git/SSH/subprocess)."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("anomalous-potato", "Win")
    raws = [
        {"id": "anomalous-potato-win-20260627-aaaa", "title": "Fix the thing",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 4, "state": "wip", "ahead": 2, "behind": 1},
        {"id": "anomalous-potato-win-20260620-bbbb", "title": "Old idle wt",
         "status": "active", "started_at": "2026-06-20T10:00:00",
         "turn_count": 0, "state": "unused"},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "anomalous-potato · win"
    src.machines = lambda: [("anomalous-potato Win", "anomalous-potato", "Win", True)]
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
    local = ("anomalous-potato", "Win")
    raws = [
        {"id": "anomalous-potato-win-20260627-cccc", "title": "Needs a decision",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 4, "state": "active",
         "live_intent": "picking a rendering option",
         "live_intent_at": "2026-06-27T17:59:00", "live_rest": "awaiting-operator",
         "live_rest_at": "2026-06-27T17:59:00"},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "anomalous-potato · win"
    src.machines = lambda: [("anomalous-potato Win", "anomalous-potato", "Win", True)]
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


def test_pulse_level_never_drops_an_existing_intent():
    """context-handoff bug #2 (ephemeral "current task" line): an unparseable
    or missing ``live_intent_at``, with no graded ``live_rest``, used to make
    ``_pulse_level`` return ``None`` -- silently dropping the live-intent TEXT
    from the tile even though it was present, contradicting the documented
    #228 "never expires, only greys" contract. Grading now degrades to
    ``'stale'`` (unknown freshness reads as aged/grey), never to absent."""
    # No live_intent_at at all.
    assert derive._pulse_level({"live_intent": "still working"}) == "stale"
    # An unparseable timestamp.
    assert derive._pulse_level(
        {"live_intent": "still working", "live_intent_at": "not-a-date"}
    ) == "stale"
    # No intent text at all -- the ONLY case the line is legitimately absent.
    assert derive._pulse_level({"live_intent": ""}) is None
    assert derive._pulse_level({}) is None


def _untimed_intent_source():
    """A worktree with a live intent but no parseable timestamp/rest -- the
    #2 repro: the intent text exists but its freshness can't be graded."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("anomalous-potato", "Win")
    raws = [
        {"id": "anomalous-potato-win-20260627-eeee", "title": "In progress",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 1, "state": "active",
         "live_intent": "reconciling the untimed pulse"},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "anomalous-potato · win"
    src.machines = lambda: [("anomalous-potato Win", "anomalous-potato", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def test_untimed_intent_still_renders_the_pulse_line(monkeypatch, tmp_path):
    """End-to-end capture proof for the fix above: the tile's second line
    still shows the intent text even when its timestamp/rest can't grade
    freshness (previously the whole pulse sub-line vanished)."""
    _isolate_pivots(monkeypatch, tmp_path)
    text = pcap.capture(_untimed_intent_source(), live=False)["text"]
    assert "reconciling the untimed pulse" in text


def _assets_source():
    """A fleet with one worktree carrying held claims of several kinds (#6443/
    upstream #1979 Phase 6) -- exercises the tile's bounded asset-hint line
    across a wide and a narrow capture width."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("anomalous-potato", "Win")
    raws = [
        {"id": "anomalous-potato-win-20260627-dddd", "title": "Ships things",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 2, "state": "wip",
         "resources": [
             {"kind": "pr", "ref": "https://example/pulls/42",
              "state": "active"},
             {"kind": "worktree", "ref": "host/repo/wt-child",
              "state": "at-rest"},
         ]},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "anomalous-potato · win"
    src.machines = lambda: [("anomalous-potato Win", "anomalous-potato", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def test_asset_hints_render_at_wide_and_narrow_widths(monkeypatch, tmp_path):
    """#6443/upstream #1979 Phase 6: the tile's bounded per-kind asset-hint
    line is legible in the deterministic character grid at both a wide and a
    narrow capture width -- neither width crashes the renderer nor drops the
    hint tokens (a narrow width may clip the live-pulse intent text, but the
    hints themselves stay visible since they are bounded, not free text)."""
    _isolate_pivots(monkeypatch, tmp_path)
    wide = pcap.capture(_assets_source(), live=False, size=(118, 24))["text"]
    narrow = pcap.capture(_assets_source(), live=False, size=(60, 24))["text"]
    for text in (wide, narrow):
        assert "PR" in text
        assert "WT" in text


def _bare_markers_source():
    """A fleet with a ``status_markers`` closure descriptor and NO asset hints
    or live pulse -- the case the operator flagged as an "indecipherable bare
    marker" second line (bug-fix phase, picker-list-interaction-layer effort):
    a raw ``C1 U* OC*`` token string with nothing else to give it context."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("anomalous-potato", "Win")
    raws = [
        {"id": "anomalous-potato-win-20260627-eeee", "title": "Bare marker row",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 3, "state": "completed",
         "closure": {
             "version": 2, "label": "MERGED", "style": "merged-blocked",
             "compact": "MERGED C1 U* OC*",
             "claims": {"held": 1}, "follow_ups": {"open": 0},
             "closure": {"final": False}, "action": {"disposition": "blocked"},
         }},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "anomalous-potato · win"
    src.machines = lambda: [("anomalous-potato Win", "anomalous-potato", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def test_bare_status_markers_render_as_readable_text(monkeypatch, tmp_path):
    """The raw closure-descriptor tokens (``C1``/``U*``/``OC*``) are a wire
    shorthand, not operator-facing copy -- when they're the ONLY thing on the
    tile's second line, they must expand into a short human phrase rather than
    render as a bare, undocumented token string."""
    _isolate_pivots(monkeypatch, tmp_path)
    text = pcap.capture(_bare_markers_source(), live=False)["text"]
    assert "1 held claim" in text
    assert "merge unconfirmed" in text
    assert "claims unconfirmed" in text
    # The raw wire tokens themselves never leak into the rendered grid.
    assert "C1" not in text
    assert "OC*" not in text
    assert "U*" not in text


def _markers_and_assets_source():
    """A fleet with BOTH a ``status_markers`` closure descriptor AND asset
    hints on the same row -- the mixed case a PR #2897 review flagged: the
    human-readable marker expansion is longer than the compact wire tokens it
    replaces, and at a narrow capture width the combined line could overflow
    the row and crowd out (or wrap past) the asset hints that follow it."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("anomalous-potato", "Win")
    raws = [
        {"id": "anomalous-potato-win-20260627-ffff", "title": "Mixed row",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 5, "state": "completed",
         "closure": {
             "version": 2, "label": "MERGED", "style": "merged-blocked",
             "compact": "MERGED C1 U* OC*",
             "claims": {"held": 1}, "follow_ups": {"open": 0},
             "closure": {"final": False}, "action": {"disposition": "blocked"},
         },
         "resources": [
             {"kind": "pr", "ref": "https://example/pulls/43",
              "state": "active"},
             {"kind": "worktree", "ref": "host/repo/wt-child2",
              "state": "at-rest"},
         ]},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "anomalous-potato · win"
    src.machines = lambda: [("anomalous-potato Win", "anomalous-potato", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def test_marker_and_asset_line_never_overflows_narrow_width(monkeypatch, tmp_path):
    """The combined status_markers + asset_hints detail line must never
    exceed the capture width, even at a narrow 60-column width where the
    readable marker expansion alone could otherwise overrun the row (PR #2897
    review). Bounded by construction -- every grid row is exactly `width`
    cells, so this just asserts the capture doesn't crash and stays a clean
    rectangular grid at the narrow width."""
    _isolate_pivots(monkeypatch, tmp_path)
    grid = pcap.capture(_markers_and_assets_source(), live=False, size=(60, 24))["text"]
    lines = grid.splitlines()
    widths = {len(line) for line in lines}
    assert len(widths) == 1, f"ragged grid at narrow width: {sorted(widths)}"
    # At least the asset hint survives -- markers were truncated to make room
    # for it rather than crowding it out entirely.
    assert "PR" in grid


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
