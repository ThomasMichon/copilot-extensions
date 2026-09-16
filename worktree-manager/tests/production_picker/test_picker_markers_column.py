"""worktree-finality-and-obligations Phase 5: engine-level coverage for the
production Picker's ``markers``/descriptor-style rendering (PR #2738 review
response).

Mirrors ``plugins/agent-worktrees/tests/test_picker_markers_column.py`` but
against ``worktree_manager.production_picker.picker_tui.engine`` -- the
Manager-owned copy actually used at runtime (excluded from the byte-identical
transplant guard, so it needs its own coverage rather than relying on the
plugin-side tests staying green).
"""
from __future__ import annotations

from worktree_manager.production_picker.picker_tui import engine


def _rec(**kw):
    base = {"id4": "abcd", "state": "MERGED", "state_markers": "C2 F1",
            "state_style": "merged-blocked", "title": "demo"}
    base.update(kw)
    return base


class TestMarkersCellRendering:
    def test_markers_cell_shows_descriptor_markers(self):
        cols = [("id4", "id", 4, "l"), ("markers", "flag", 5, "l")]
        text = engine.row_text(_rec(), cols, width=20, selected=False)
        assert "C2 F1" in text.plain

    def test_markers_cell_blank_when_no_markers(self):
        cols = [("id4", "id", 4, "l"), ("markers", "flag", 5, "l")]
        text = engine.row_text(
            _rec(state_markers="", state_style=""), cols, width=20, selected=False)
        assert "None" not in text.plain

    def test_markers_cell_uses_descriptor_style_color(self):
        cols = [("markers", "flag", 5, "l")]
        text = engine.row_text(_rec(), cols, width=10, selected=False)
        styles = {span.style for span in text.spans}
        assert engine._DESCRIPTOR_STYLE["merged-blocked"] in styles

    def test_state_cell_also_uses_descriptor_style_color(self):
        cols = [("state", "state", 6, "l")]
        text = engine.row_text(_rec(), cols, width=10, selected=False)
        styles = {span.style for span in text.spans}
        assert engine._DESCRIPTOR_STYLE["merged-blocked"] in styles

    def test_state_cell_falls_back_to_legacy_c_state_when_no_descriptor_style(self):
        cols = [("state", "state", 6, "l")]
        text = engine.row_text(
            _rec(state="FINAL", state_style=""), cols, width=10, selected=False)
        styles = {span.style for span in text.spans}
        assert engine.C_STATE["FINAL"] in styles


class TestMarkersColumnFitPriority:
    """The ``markers`` column must be among the FIRST columns ``fit()`` drops
    under width pressure in the production engine too."""

    def _priority_map(self, specs):
        return {key: prio for key, _h, _w, _a, prio in specs}

    def test_active_specs_markers_is_max_priority(self):
        prio = self._priority_map(engine.ACTIVE_SPECS)
        assert prio["markers"] == max(prio.values())

    def test_list_specs_markers_is_max_priority(self):
        prio = self._priority_map(engine.LIST_SPECS)
        assert prio["markers"] == max(prio.values())

    def test_clean_specs_markers_is_max_priority(self):
        prio = self._priority_map(engine.CLEAN_SPECS)
        assert prio["markers"] == max(prio.values())

    def test_fit_drops_markers_before_age_or_size_in_clean_specs(self):
        full_width = sum(c[2] for c in engine.CLEAN_SPECS) + len(engine.PAD) * (
            len(engine.CLEAN_SPECS) - 1)
        cols = engine.fit(engine.CLEAN_SPECS, avail=full_width - 1,
                           flex_key="title", flex_min=10)
        keys = [c[0] for c in cols]
        assert "markers" not in keys
        assert "age" in keys
        assert "mib" in keys
