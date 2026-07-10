"""Headless render test for the ported Worktree Picker TUI (slice 1).

Hermetic: drives the engine over a fixture source (no real tracking/git/SSH),
asserting it boots and renders real-shaped records with the canonical state
vocabulary.
"""
from __future__ import annotations

import asyncio
import datetime
import threading
import time
import types

from agent_worktrees.picker_tui import derive, new_picker_enabled
from agent_worktrees.picker_tui.engine import PickerApp, PickerScreen
from agent_worktrees.picker_tui.selection import ListSelection


def _fixture_source():
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")
    raws = [
        {"id": "lambda-core-win-20260627-aaaa", "title": "Fix the thing",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 4, "state": "wip", "ahead": 2, "behind": 1,
         "mux_session": True, "mux_attached": True, "mux_clients": 1,
         "pr": {"number": 42, "state": "open"}},
        {"id": "lambda-core-win-20260620-bbbb", "title": "Old idle wt",
         "status": "active", "started_at": "2026-06-20T10:00:00",
         "turn_count": 0, "state": "unused"},
        {"id": "lambda-core-win-20260626-cccc", "title": "Done work",
         "status": "finalized", "completed_at": "2026-06-26T10:00:00",
         "started_at": "2026-06-25T10:00:00", "turn_count": 9,
         "state": "completed", "pr": {"number": 40, "state": "merged"}},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def test_maintenance_eliminated_from_nav():
    """#1427: Maintenance is a hidden anchor -- off the left rail and not under
    Configuration; the Worktrees pivot carries bulk Clean/Sync buttons instead."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            maint = next(i for i, p in enumerate(scr.pivots)
                         if p["kind"] == "maintenance")
            assert scr.pivots[maint]["placement"] == "hidden"
            assert maint not in scr._left_pivots()
            assert maint not in scr._config_pivots()
            # Worktrees pivot now exposes the bulk Clean/Sync buttons.
            assert scr._kind() == "worktrees"
            bset = scr.button_set()
            assert "K" in bset and "SY" in bset

    asyncio.run(run())


def test_worktrees_clean_button_opens_dialog():
    """Activating the Clean button on the Worktrees row opens the cleanup
    dialog (the state-quick-select mini-picker), not the old pivot (#1427)."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            scr.sel = ("BTN", 0)
            scr.btn_idx = scr.button_set().index("K")
            scr._activate()
            assert scr.cleanup is not None            # cleanup mini-picker open
            # Its options are the state buckets (select all merged, unused, …).
            labels = [o["label"] for o in scr.cleanup["opts"]]
            assert any("Merged" in x for x in labels)

    asyncio.run(run())


def test_clean_focus_preview_dims_non_cleanable():
    """Focusing Clean dims worktree rows it would not touch (#1427)."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            scr.sel = ("BTN", 0)
            scr.btn_idx = scr.button_set().index("K")
            await pilot.pause()
            rows = {getattr(v, "stop", None): v for v in scr.build_body(118)
                    if getattr(v, "stop", None) and v.stop[0] == "L"}
            dimmed = {}
            for stop, vr in rows.items():
                rec = vr.data
                is_dim = any("grey35" in str(sp.style) for sp in vr.text.spans)
                dimmed[rec["id4"]] = (is_dim, scr._cleanable(rec))
            # Every non-cleanable, non-selected row is dimmed; cleanable rows are not.
            for _id, (is_dim, cleanable) in dimmed.items():
                if not cleanable:
                    assert is_dim, f"{_id} should be dimmed"

    asyncio.run(run())


def test_submenu_cleanup_opens_scoped_dialog():
    """The per-worktree submenu Cleanup now runs the real op (scoped to that
    worktree), not a mock (#1427)."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            # Find a cleanable row and open its submenu.
            recs = scr.list_records()
            ci = next(i for i, r in enumerate(recs) if scr._cleanable(r))
            scr.sel = ("L", ci)
            scr._open_submenu()
            assert "Cleanup" in scr.submenu["actions"]
            scr.submenu_idx = scr.submenu["actions"].index("Cleanup")
            scr._key_submenu("enter")
            assert scr.submenu is None
            assert scr.cleanup is not None            # real scoped dialog opened

    asyncio.run(run())


def test_clean_dialog_live_filter_preview():
    """While the Clean dialog is open, the worktree list dims rows outside the
    selected bucket union and keeps selected rows bright; toggling a bucket
    updates the preview live (#2179)."""
    src = _maint_source()

    def dim_map(scr):
        rows = {}
        for v in scr.build_body(118):
            stop = getattr(v, "stop", None)
            if stop and stop[0] == "L":
                dim = any("grey35" in str(sp.style) for sp in v.text.spans)
                rows[v.data["id4"]] = dim
        return rows

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            scr.sel = ("BTN", 0)
            scr.btn_idx = scr.button_set().index("K")
            scr._activate()                       # open the Clean dialog
            assert scr.cleanup is not None
            sel_ids = scr._cleanup_union()
            assert sel_ids                        # default "Merged" bucket on
            dm = dim_map(scr)
            for id4, dim in dm.items():
                assert dim == (id4 not in sel_ids)   # in-set bright, rest dimmed
            # Toggling the "Unused" bucket widens the previewed set live.
            before = set(scr._cleanup_union())
            ui = next(i for i, o in enumerate(scr.cleanup["opts"])
                      if o["label"] == "Unused")
            scr.cleanup["idx"] = ui
            scr._key_scopedlg("space")
            after = set(scr._cleanup_union())
            assert after > before
            dm2 = dim_map(scr)
            for id4, dim in dm2.items():
                assert dim == (id4 not in after)

    asyncio.run(run())


def _dim_map(scr):
    """id4 -> is the row dimmed (grey35) in the current build_body render."""
    rows = {}
    for v in scr.build_body(118):
        stop = getattr(v, "stop", None)
        if stop and stop[0] == "L":
            rows[v.data["id4"]] = any("grey35" in str(sp.style) for sp in v.text.spans)
    return rows


def _open_clean_live(scr):
    """Open the Clean live filter on the local machine tab; return the screen."""
    scr.machine_idx = scr.local_index()
    scr.sel = ("BTN", 0)
    scr.btn_idx = scr.button_set().index("K")
    scr._activate()
    assert scr.cleanup is not None
    return scr


def _focus_worktree_row(scr, id4):
    """Point self.sel at the ("L", i) stop whose record is id4."""
    recs = scr.list_records()
    li = next(i for i, r in enumerate(recs) if r["id4"] == id4)
    scr.sel = ("L", li)
    return li


def test_clean_per_row_unselect_drops_from_net_set():
    """Space on a focused worktree row inside the Clean live filter drops just
    that worktree from the net set (bucket stays on); the row then previews as
    dimmed, and toggling again puts it back (#2179 second increment)."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            _open_clean_live(scr)
            union = set(scr._cleanup_union())
            assert len(union) >= 1
            victim = next(iter(union))

            scr._key_scopedlg("tab")                 # buckets -> rows
            assert scr.cleanup["section"] == 2
            assert scr.sel[0] == "L"                 # focus landed on a row

            _focus_worktree_row(scr, victim)
            scr._key_scopedlg("space")               # drop the focused worktree
            net = set(scr._cleanup_union())
            assert victim not in net
            assert net == union - {victim}

            # It previews dimmed once focus moves off it.
            recs = scr.list_records()
            other_li = next((i for i, r in enumerate(recs)
                             if r["id4"] != victim), None)
            if other_li is not None:
                scr.sel = ("L", other_li)
                assert _dim_map(scr).get(victim) is True

            # Toggle back in -> restored.
            _focus_worktree_row(scr, victim)
            scr._key_scopedlg("space")
            assert victim in set(scr._cleanup_union())

    asyncio.run(run())


def test_clean_per_row_unselect_ignores_rows_outside_union():
    """Dropping a worktree that isn't in the enabled-bucket union is a no-op --
    you can't exclude what isn't selected."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            _open_clean_live(scr)
            union = set(scr._cleanup_union())
            outside = next((r["id4"] for r in scr.list_records()
                            if r["id4"] not in union), None)
            if outside is None:
                return  # every row is in the union here; nothing to assert
            _focus_worktree_row(scr, outside)
            scr._key_scopedlg("tab")                 # into the rows section
            _focus_worktree_row(scr, outside)
            scr._key_scopedlg("space")               # no-op
            assert not scr.cleanup["excluded"]
            assert set(scr._cleanup_union()) == union

    asyncio.run(run())


def test_clean_dialog_tab_cycles_buckets_rows_confirm():
    """Tab in the Clean live filter cycles buckets(0) -> rows(2) -> confirm(1)."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            _open_clean_live(scr)
            assert scr.cleanup["section"] == 0
            scr._key_scopedlg("tab")
            assert scr.cleanup["section"] == 2       # rows
            scr._key_scopedlg("tab")
            assert scr.cleanup["section"] == 1       # confirm
            scr._key_scopedlg("tab")
            assert scr.cleanup["section"] == 0       # back to buckets

    asyncio.run(run())


def test_clean_confirm_acts_on_net_after_unselect():
    """The set Confirm acts on (`_cleanup_union`) reflects a per-row drop."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            _open_clean_live(scr)
            union = set(scr._cleanup_union())
            if not union:
                return
            victim = next(iter(union))
            scr._key_scopedlg("tab")
            _focus_worktree_row(scr, victim)
            scr._key_scopedlg("space")
            # Enter -> confirm row; the net set the executor will use excludes it.
            scr._key_scopedlg("enter")
            assert scr.cleanup["section"] == 1
            assert victim not in set(scr._cleanup_union())

    asyncio.run(run())


# ── #2228 Phase 2b: unified Worktrees Space-select / Enter-action-menu ────────

def test_worktrees_space_toggles_selection():
    """Space on a Worktrees row toggles it in the list multi-select (not the
    old open-submenu behavior)."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            recs = scr.list_records()
            assert recs
            wid = recs[0]["id4"]
            scr.sel = ("L", 0)
            scr.handle_key("space")
            assert wid in scr.wt_sel
            assert scr.submenu is None            # Space no longer opens submenu
            scr.handle_key("space")
            assert wid not in scr.wt_sel

    asyncio.run(run())


def test_worktrees_enter_without_selection_opens_submenu():
    """Enter on a row with no multi-selection opens that row's sub-menu (which
    carries Open/Resume) -- the primary flow is preserved."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            assert scr.list_records()
            scr.sel = ("L", 0)
            assert not scr.wt_sel
            scr.handle_key("enter")
            assert scr.submenu is not None
            assert scr.submenu["actions"][0] in ("Open", "Resume")

    asyncio.run(run())


def test_worktrees_enter_with_selection_opens_bulk_menu():
    """Enter with a multi-selection opens the bulk action menu (Sync/Cleanup)
    for the selected set, not a per-row submenu."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            recs = scr.list_records()
            cleanable = [i for i, r in enumerate(recs) if scr._cleanable(r)]
            if not cleanable:
                return
            scr.sel = ("L", cleanable[0])
            scr.handle_key("space")               # select a cleanable row
            assert scr.wt_sel
            scr.handle_key("enter")               # -> bulk action menu
            assert scr.submenu is None
            assert scr.maint_menu is not None
            assert "Cleanup" in scr.maint_menu["actions"]

    asyncio.run(run())


def test_worktrees_bulk_menu_routes_to_scoped_cleanup():
    """Choosing Cleanup in the bulk menu opens the Clean dialog scoped to the
    selected set."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            recs = scr.list_records()
            cleanable = [i for i, r in enumerate(recs) if scr._cleanable(r)]
            if not cleanable:
                return
            scr.sel = ("L", cleanable[0])
            scr.handle_key("space")
            scr._open_wt_action_menu()
            scr.maint_menu_idx = scr.maint_menu["actions"].index("Cleanup")
            scr._key_maint_menu("enter")          # route to scoped cleanup
            assert scr.maint_menu is None
            assert scr.cleanup is not None
            assert "selected" in scr.cleanup["scope"]

    asyncio.run(run())


def test_worktrees_checkbox_only_in_multiselect_mode():
    """The selection checkbox gutter is hidden during ordinary single-select
    navigation (visual pollution) and only appears once the operator is in a
    multi-select state: a diverged single selection, or multiple selected
    (#2258 follow-up)."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            recs = scr.list_records()
            if len(recs) < 2:
                return

            def firstchars():
                out = {}
                for v in scr.build_body(118):
                    stop = getattr(v, "stop", None)
                    if stop and stop[0] == "L":
                        out[v.data["id4"]] = v.text.plain[0]
                return out

            def boxes():
                return {k: c for k, c in firstchars().items() if c in "☐☑"}

            # Nothing selected -> no checkbox gutter (leading char is not a box).
            scr.sel = ("L", 0)
            scr.wt_sel.clear()
            assert not boxes()

            # Single selection tracking focus -> still no gutter.
            scr.wt_sel.replace({recs[0]["id4"]})
            scr.sel = ("L", 0)
            assert not boxes()

            # Focus diverges from the single selection (e.g. after Ctrl+Arrow)
            # -> the gutter appears; the selected row shows ☑, others ☐.
            target = recs[0]["id4"]
            scr.sel = ("L", 1)
            b = boxes()
            assert b.get(target) == "☑"
            assert all(b[k] == "☐" for k in b if k != target)

            # Multiple selected -> gutter shown even with focus on a selected row.
            scr.wt_sel.replace({recs[0]["id4"], recs[1]["id4"]})
            scr.sel = ("L", 0)
            b = boxes()
            assert b.get(recs[0]["id4"]) == "☑"
            assert b.get(recs[1]["id4"]) == "☑"

    asyncio.run(run())


# ---- Phase 3: keyboard-accessible multi-selection (#2258) ----

def _wt_scr(pilot_body):
    """Boilerplate: build a fixture-backed Worktrees screen on the local tab and
    hand it to ``pilot_body(scr)`` with focus seeded on the first list row."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            assert scr.list_records()
            await pilot_body(scr)

    asyncio.run(run())


def test_arrow_moves_focus_and_selection_follows():
    """P3-1: plain Up/Down move focus AND collapse selection to just the focused
    row (single-select tracks focus)."""
    async def body(scr):
        ids = [r["id4"] for r in scr.list_records()]
        scr.sel = ("L", 0)
        scr._wt_track_focus()             # seed: focus follows to row 0
        assert scr.wt_sel == {ids[0]}
        scr.handle_key("down")
        assert scr.sel == ("L", 1)
        assert scr.wt_sel == {ids[1]}     # collapsed to the new focus
        scr.handle_key("down")
        assert scr.wt_sel == {ids[2]}
        scr.handle_key("up")
        assert scr.wt_sel == {ids[1]}

    _wt_scr(body)


def test_arrow_out_of_list_clears_selection():
    """P3-1: when a plain arrow moves focus off the list, the selection follows
    it to nothing."""
    async def body(scr):
        scr.sel = ("L", 0)
        scr._wt_track_focus()
        assert scr.wt_sel
        scr.handle_key("up")              # ("L",0) -> ("BTN",0), leaves the list
        assert scr.sel[0] != "L"
        assert not scr.wt_sel

    _wt_scr(body)


def test_shift_arrow_extends_range_from_anchor():
    """P3-2: Shift+Down/Up extend a contiguous range from the anchor row set when
    the gesture began."""
    async def body(scr):
        ids = [r["id4"] for r in scr.list_records()]
        assert len(ids) >= 4
        scr.sel = ("L", 1)                # anchor seeds here on first shift move
        scr.handle_key("shift+down")
        assert scr.sel == ("L", 2)
        assert scr.wt_sel == {ids[1], ids[2]}
        scr.handle_key("shift+down")
        assert scr.wt_sel == {ids[1], ids[2], ids[3]}
        scr.handle_key("shift+up")        # shrink back toward the anchor
        assert scr.sel == ("L", 2)
        assert scr.wt_sel == {ids[1], ids[2]}

    _wt_scr(body)


def test_shift_arrow_clamps_inside_list():
    """P3-2: a range gesture never steps focus out of the list."""
    async def body(scr):
        n = len(scr.list_records())
        scr.sel = ("L", n - 1)
        scr.handle_key("shift+down")      # already at the bottom
        assert scr.sel == ("L", n - 1)

    _wt_scr(body)


def test_space_is_additive_and_reseats_anchor():
    """P3-3: Space toggles the focused row independently (does not collapse the
    rest) and re-seats the range anchor there, so a following Shift+arrow
    extends the contiguous range from *that* row (range-replace, dropping the
    earlier non-contiguous add -- the native list model)."""
    async def body(scr):
        ids = [r["id4"] for r in scr.list_records()]
        scr.sel = ("L", 0)
        scr.handle_key("space")           # additive select row 0
        scr.sel = ("L", 2)
        scr.handle_key("space")           # additive select row 2 (row 0 kept)
        assert scr.wt_sel == {ids[0], ids[2]}
        assert scr.wt_anchor == 2         # Space re-seated the anchor
        scr.handle_key("shift+down")      # extend the range from row 2
        assert scr.sel == ("L", 3)
        assert scr.wt_sel == {ids[2], ids[3]}

    _wt_scr(body)


def test_ctrl_arrow_moves_focus_only():
    """P3-4: Ctrl+Up/Down move focus without disturbing the selection or the
    range anchor."""
    async def body(scr):
        ids = [r["id4"] for r in scr.list_records()]
        scr.sel = ("L", 0)
        scr.handle_key("space")           # build a selection at row 0
        scr.handle_key("ctrl+down")       # move focus only
        assert scr.sel == ("L", 1)
        assert scr.wt_sel == {ids[0]}     # selection untouched
        scr.handle_key("ctrl+down")
        assert scr.sel == ("L", 2)
        assert scr.wt_sel == {ids[0]}

    _wt_scr(body)


def test_escape_collapses_selection_before_quit():
    """P3-5: Esc with >1 selected collapses to the focused row and does NOT open
    the quit-confirm; a second Esc (nothing to collapse) reaches it (#1429)."""
    async def body(scr):
        ids = [r["id4"] for r in scr.list_records()]
        scr.sel = ("L", 1)
        scr.handle_key("shift+down")
        scr.handle_key("shift+down")      # rows 1..3 selected
        assert len(scr.wt_sel) == 3
        scr.handle_key("escape")          # collapse, not quit
        assert scr.quit_confirm is None
        assert scr.wt_sel == {ids[scr.sel[1]]}
        scr.handle_key("escape")          # nothing left to collapse -> quit prompt
        assert scr.quit_confirm is not None

    _wt_scr(body)


def test_escape_outside_list_clears_to_nothing():
    """P3-5: with focus outside the list, Esc collapses a built-up selection to
    nothing (still not a quit)."""
    async def body(scr):
        scr.sel = ("L", 1)
        scr.handle_key("shift+down")
        scr.handle_key("shift+down")
        assert len(scr.wt_sel) == 3
        scr.sel = ("BTN", 0)              # tabbed away; selection persists
        scr.handle_key("escape")
        assert scr.quit_confirm is None
        assert not scr.wt_sel

    _wt_scr(body)


def test_tab_preserves_selection_and_remembers_focus():
    """P3-6: Tab out of and back into the list keeps the selection and restores
    the last-focused row."""
    async def body(scr):
        ids = [r["id4"] for r in scr.list_records()]
        scr.sel = ("L", 2)
        scr.handle_key("space")           # select row 2, focus row 2
        # Tab out to another region, then keep tabbing back around to the list.
        seen = set()
        scr.handle_key("tab")
        while scr.sel[0] != "L":
            key = tuple(scr.sel)
            assert key not in seen, "Tab cycle did not return to the list"
            seen.add(key)
            scr.handle_key("tab")
        assert scr.sel == ("L", 2)        # focus restored
        assert scr.wt_sel == {ids[2]}     # selection preserved

    _wt_scr(body)


def test_selection_survives_reload_and_focus_rehomes_by_index():
    """P3-7: after an operation reloads the list, surviving rows stay selected, a
    deleted row drops from the selection, and focus stays at the equivalent
    index (clamped)."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            recs = scr.list_records()
            ids = [r["id4"] for r in recs]
            assert len(ids) >= 3
            # Select the first three rows; focus the last of them.
            scr.wt_sel.replace(set(ids[:3]))
            scr.sel = ("L", 2)
            # Simulate the operation deleting rows[0] (e.g. a Clean removed it):
            # the reloaded source no longer emits that worktree.
            gone = recs[0]["raw"]["id"]
            base = src.load
            src.load = lambda: [r for r in base() if r["raw"]["id"] != gone]
            scr._refresh_after_maint({"recs": [{"machine": "lambda-core",
                                                "env": "Win"}]})
            survivors = {r["id4"] for r in scr.list_records()}
            assert ids[0] not in survivors               # deleted row is gone
            assert scr.wt_sel == {ids[1], ids[2]}         # survivors stay selected
            assert ids[0] not in scr.wt_sel               # dropped from selection
            assert scr.sel[0] == "L"                      # focus stayed in the list
            assert scr.sel[1] < len(scr.list_records())   # and at a valid index

    asyncio.run(run())


def test_reconcile_wt_sel_noop_on_empty_reload():
    """P3-7: while a live reload is momentarily empty, reconcile is a no-op so a
    transient empty frame never clobbers a built-up selection."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            ids = [r["id4"] for r in scr.list_records()]
            scr.wt_sel.replace(set(ids[:2]))
            scr.data = []                     # nothing loaded yet
            scr._reconcile_wt_sel()
            assert scr.wt_sel == set(ids[:2])  # preserved, not cleared

    asyncio.run(run())


def test_live_reconcile_deferred_until_reload_settles():
    """P3-7: in live mode the post-op reconcile waits for the touched machine to
    finish reloading -- a 'loading' state leaves the selection untouched, and it
    only drops the deleted row once the machine reports 'ready'."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            recs = scr.list_records()
            ids = [r["id4"] for r in recs]
            scr.wt_sel.replace(set(ids[:3]))
            # Fake a live loader whose reload is still in flight.
            state_holder = {"s": "loading"}
            scr.loader = types.SimpleNamespace(
                state=lambda m, e: state_holder["s"])
            scr._wt_reconcile_after = {("lambda-core", "Win")}

            # Still loading -> reconcile is deferred, selection intact.
            scr._process_pending_wt_reconcile()
            assert scr._wt_reconcile_after is not None
            assert scr.wt_sel == set(ids[:3])

            # The reload lands with recs[0] removed and the machine ready.
            gone = recs[0]["raw"]["id"]
            scr.data = [r for r in scr.data if (r.get("raw") or {}).get("id") != gone]
            state_holder["s"] = "ready"
            scr._process_pending_wt_reconcile()
            assert scr._wt_reconcile_after is None
            assert ids[0] not in scr.wt_sel               # deleted row dropped
            assert scr.wt_sel == {ids[1], ids[2]}         # survivors kept

    asyncio.run(run())


def test_machine_rotate_scopes_selection_to_visible_tab():
    """#2258 P3 (rubber-duck): rotating the machine tab drops selections for
    rows not visible on the new tab, so the checkbox column and the Enter bulk
    menu never act on rows the operator can't see."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)

    def raw(mid, code):
        return {"id": f"{mid}-{code}", "title": code, "status": "active",
                "started_at": "2026-06-27T17:00:00", "cleanup_bucket": "clean"}

    src = types.SimpleNamespace()
    src.LOCAL = ("lambda-core", "Win")
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [
        ("lambda-core Win", "lambda-core", "Win", True),
        ("borealis Win", "borealis", "Win", True),
    ]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: (
        [derive.norm(raw("lambda-core-win", "aa00"), "lambda-core", "Win")]
        + [derive.norm(raw("borealis-win", "bb00"), "borealis", "Win")]
    )

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.t0 = 0                     # all machine tabs ready
            scr.machine_idx = 0            # start on All -> both machines visible
            await pilot.pause()
            ids = {r["id4"] for r in scr.list_records()}
            assert len(ids) == 2
            scr.wt_sel.replace(ids)        # select rows from both machines
            # Rotate All -> lambda-core Win (index 1); the borealis row is no
            # longer visible, so its selection drops.
            scr._rotate_machine(1)
            visible = {r["id4"] for r in scr.list_records()}
            assert len(visible) == 1
            assert scr.wt_sel == visible
            assert scr.wt_anchor is None   # anchor reset on scope change

    asyncio.run(run())


def test_ctrl_space_toggles_selection():
    """#2258 follow-up: Ctrl+Space toggles the focused row just like Space, so
    the toggle works while the operator holds Ctrl to move focus. Textual
    delivers Ctrl+Space as 'ctrl+at'."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            recs = scr.list_records()
            wid = recs[0]["id4"]
            scr.sel = ("L", 0)
            scr.wt_sel.clear()
            scr.handle_key("ctrl+at")            # canonical Textual key
            assert wid in scr.wt_sel
            scr.handle_key("ctrl+at")
            assert wid not in scr.wt_sel
            scr.handle_key("ctrl+space")         # alias also accepted
            assert wid in scr.wt_sel

    asyncio.run(run())


def test_worktrees_row_highlight_states():
    """#2258 follow-up: the three visual states — green invert (focused AND
    selected), plain invert (focused only), grey background (selected but not
    focused)."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            recs = scr.list_records()
            ids = [r["id4"] for r in recs]

            def styles(id4_target):
                for v in scr.build_body(118):
                    stop = getattr(v, "stop", None)
                    if stop and stop[0] == "L" and v.data["id4"] == id4_target:
                        return [s.style for s in v.text.spans]
                return []

            # Focused AND selected -> green invert.
            scr.sel = ("L", 0)
            scr.wt_sel.replace({ids[0]})
            assert "reverse green3" in styles(ids[0])

            # Focused, not selected -> plain (white) invert, not green.
            scr.wt_sel.clear()
            scr.sel = ("L", 0)
            s0 = styles(ids[0])
            assert "reverse" in s0
            assert "reverse green3" not in s0

            # Selected but focus moved off it -> grey background, no invert.
            scr.wt_sel.replace({ids[0]})
            scr.sel = ("L", 1)
            s0 = styles(ids[0])
            assert "on grey30" in s0
            assert "reverse" not in s0
            # ...and the now-focused unselected row is a plain invert.
            assert "reverse" in styles(ids[1])

    asyncio.run(run())


def test_configuration_reachable_via_tab(monkeypatch):
    """The ⚙ Configuration entry is in the Tab cycle (region_heads) and Tab from
    the View pivot lands on it (operator feedback: couldn't reach it)."""
    from agent_worktrees.picker_tui import engine

    # Pin the update indicator to a non-actionable state. When a plugin update
    # is staged on the host, an optional refresh icon ("V", 1) legitimately sits
    # between the View pivot and Configuration in the Up/Down flow -- so without
    # this, Down from ("V", 0) lands on ("V", 1) and the assertion below depends
    # on the machine's update state (flaky on a box with an update staged).
    monkeypatch.setattr(engine, "indicator_state", lambda: "current")
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            assert ("CFG", 0) in scr.region_heads()
            scr.sel = ("V", 0)
            scr.handle_key("tab")
            assert scr.sel == ("CFG", 0)
            # And it is also reachable by arrowing down from the View pivot.
            scr.sel = ("V", 0)
            scr.handle_key("down")
            assert scr.sel == ("CFG", 0)

    asyncio.run(run())


def test_action_row_caption_tracks_focused_button():
    """The Worktrees action-row caption reflects the focused button, not always
    'creates on' (operator feedback on #1427)."""
    src = _maint_source()

    def caption_for(scr, code):
        bset = scr.button_set()
        idx = bset.index(code)
        row = scr.new_worktree_row(118, True, idx)
        return row.plain

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            assert "creates on" in caption_for(scr, "N")
            assert "cleans" in caption_for(scr, "K")
            assert "fast-forwards" in caption_for(scr, "SY")
            # Unfocused row falls back to the New caption.
            assert "creates on" in scr.new_worktree_row(118, False, 0).plain

    asyncio.run(run())


def _bridge_source():
    """Two machines; a bridge-owned worktree lives on the non-local one, for
    the #1424 jump-to-host flow."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    raws_local = [
        {"id": "lambda-core-win-1111", "title": "Local wt", "status": "active",
         "started_at": "2026-06-27T17:00:00", "turn_count": 3},
    ]
    raws_bor = [
        {"id": "borealis-win-bridge-2222", "title": "Bridge wt",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "kind": "bridge", "turn_count": 1},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = ("lambda-core", "Win")
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [
        ("lambda-core Win", "lambda-core", "Win", True),
        ("borealis Win", "borealis", "Win", True),
    ]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: (
        [derive.norm(w, "lambda-core", "Win") for w in raws_local]
        + [derive.norm(w, "borealis", "Win") for w in raws_bor]
    )
    return src


def test_jump_to_host_offered_only_for_managed(tmp_path):
    """The submenu offers 'Jump to host' for a bridge/system worktree, not a
    plain session worktree (#1424)."""
    src = _bridge_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.t0 = 0                     # force all machine tabs ready
            scr.show_hidden = True
            scr.machine_idx = 0            # All
            await pilot.pause()
            recs = scr.list_records()
            bi = next(i for i, r in enumerate(recs) if r.get("kind") == "bridge")
            si = next(i for i, r in enumerate(recs) if r.get("kind") == "session")
            scr.sel = ("L", bi)
            scr._open_submenu()
            assert "Jump to host" in scr.submenu["actions"]
            scr.submenu = None
            scr.sel = ("L", si)
            scr._open_submenu()
            assert "Jump to host" not in scr.submenu["actions"]

    asyncio.run(run())


def test_jump_to_host_switches_machine_and_highlights(tmp_path):
    """Invoking 'Jump to host' switches to the host machine tab, reveals hidden,
    lands selection on the row by stable id, and never exits the picker
    (#1424)."""
    src = _bridge_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.t0 = 0
            scr.show_hidden = True
            scr.machine_idx = 0            # start on All
            await pilot.pause()
            recs = scr.list_records()
            bi = next(i for i, r in enumerate(recs) if r.get("kind") == "bridge")
            scr.sel = ("L", bi)
            scr._open_submenu()
            scr.submenu_idx = scr.submenu["actions"].index("Jump to host")
            scr._key_submenu("enter")
            await pilot.pause()
        assert scr.submenu is None
        assert scr.machine_idx == scr._machine_index_for("borealis", "Win")
        assert scr.show_hidden is True
        assert scr.sel[0] == "L"
        landed = scr.list_records()[scr.sel[1]]
        assert (landed.get("raw") or {}).get("id") == "borealis-win-bridge-2222"
        assert app.result is None          # internal nav -- never exited

    asyncio.run(run())


def test_jump_to_worktree_unknown_id_is_safe(tmp_path):
    """A jump to a worktree not in the loaded set is a reported no-op, not a
    crash (guards the #1425 registered-pivot internal action)."""
    src = _bridge_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            ok, msg = scr._jump_to_worktree("does-not-exist")
            assert ok is False
            assert "not found" in msg
            # And the internal-action dispatcher reports unknown verbs.
            ok2, msg2 = scr._internal_pivot_action("no-such-verb", {})
            assert ok2 is False
            assert "unknown internal action" in msg2

    asyncio.run(run())


def test_open_worktree_cli_exits_with_resume_decision():
    """#2253: the ``open-cli`` internal action opens the entry's target worktree
    into a CLI session -- it exits the picker with a standard resume decision for
    that worktree id, so __main__ maps it onto the launch/resume path."""
    src = _bridge_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            ok, msg = scr._internal_pivot_action(
                "open-cli", {"worktree": "borealis-win-bridge-2222"})
            assert ok is True
            assert "CLI session" in msg
            # The picker recorded a resume decision for that worktree and exited.
            assert app.result is not None
            assert app.result["action"] == "resume"
            assert app.result["worktree_id"] == "borealis-win-bridge-2222"
            assert app.result["machine"] == "borealis"
            assert app.result["env"] == "Win"
            assert app.result["is_local"] is False

    asyncio.run(run())


def test_open_worktree_cli_unknown_id_is_safe():
    """``open-cli`` on a worktree not in the loaded set is a reported no-op
    (never exits the picker, never crashes)."""
    src = _bridge_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            ok, msg = scr._open_worktree_cli("does-not-exist")
            assert ok is False
            assert "not found" in msg
            assert app.result is None      # never exited

    asyncio.run(run())


def test_jump_to_caller_targets_caller_worktree():
    """A bridge worktree with a recorded caller offers 'Jump to caller', which
    navigates to the CALLER worktree (not the bridge itself) (#2178)."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    caller_raw = {"id": "lambda-core-win-caller-9999", "title": "Caller wt",
                  "status": "active", "started_at": "2026-06-27T17:00:00",
                  "turn_count": 2}
    bridge_raw = {"id": "borealis-win-bridge-8888", "title": "Bridge wt",
                  "status": "active", "started_at": "2026-06-27T17:00:00",
                  "kind": "bridge", "turn_count": 1,
                  "caller_worktree": "lambda-core-win-caller-9999"}
    src = types.SimpleNamespace()
    src.LOCAL = ("lambda-core", "Win")
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [
        ("lambda-core Win", "lambda-core", "Win", True),
        ("borealis Win", "borealis", "Win", True),
    ]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: (
        [derive.norm(caller_raw, "lambda-core", "Win")]
        + [derive.norm(bridge_raw, "borealis", "Win")]
    )

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.t0 = 0
            scr.show_hidden = True
            scr.machine_idx = 0
            await pilot.pause()
            recs = scr.list_records()
            bi = next(i for i, r in enumerate(recs) if r.get("kind") == "bridge")
            scr.sel = ("L", bi)
            scr._open_submenu()
            # A resolvable caller wins over the own-host fallback.
            assert "Jump to caller" in scr.submenu["actions"]
            assert "Jump to host" not in scr.submenu["actions"]
            scr.submenu_idx = scr.submenu["actions"].index("Jump to caller")
            scr._key_submenu("enter")
            await pilot.pause()
        # Landed on the CALLER worktree (lambda-core tab), not the bridge.
        assert scr.machine_idx == scr._machine_index_for("lambda-core", "Win")
        landed = scr.list_records()[scr.sel[1]]
        assert (landed.get("raw") or {}).get("id") == "lambda-core-win-caller-9999"
        assert app.result is None

    asyncio.run(run())


def test_profiles_hosted_under_configuration():
    """Profiles is placed under ⚙ Configuration: off the left cycle, in the
    config set, and the ('CFG', 0) stop exists (#1426)."""
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            prof = next(i for i, p in enumerate(scr.pivots)
                        if p["kind"] == "profiles")
            assert scr.pivots[prof]["placement"] == "config"
            assert prof in scr._config_pivots()
            assert prof not in scr._left_pivots()
            assert ("CFG", 0) in scr._v_stops()

    asyncio.run(run())


def test_configuration_menu_opens_profiles():
    """Enter on the Configuration entry opens its menu; choosing Profiles
    switches to that pivot and focuses its body (#1426)."""
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            assert scr._kind() == "worktrees"
            scr.sel = ("CFG", 0)
            scr._activate()                 # opens the Configuration menu
            assert scr.cfgmenu is not None
            scr._key_cfgmenu("enter")       # selects Profiles (only item)
            assert scr.cfgmenu is None
            assert scr._kind() == "profiles"
            assert scr.sel[0] in ("PR", "BTN")   # focused the profiles body

    asyncio.run(run())


def _maint_source():
    """Fixture with one worktree per cleanup bucket + one FF-eligible."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")

    def raw(code, bucket, ff=False):
        return {"id": f"lambda-core-win-{code}", "title": code,
                "status": "active", "started_at": "2026-06-27T17:00:00",
                "cleanup_bucket": bucket, "ff_eligible": ff}

    raws = [
        raw("cl00", "clean"),
        raw("el00", "clean", ff=True),
        raw("un00", "unused"),
        raw("cv00", "conversation"),
        raw("dr00", "dirty"),
        raw("op00", "open-pr"),
        raw("ac00", "active"),
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def test_cleanup_dialog_buckets_and_sync_eligibility():
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:  # noqa: F841
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()

            scr._open_cleanup()
            opts = {o["label"]: o["ids"] for o in scr.cleanup["opts"]}
            assert len(opts["Merged & finalized"]) == 2   # cl00 + el00
            assert len(opts["Unused"]) == 1               # un00
            assert len(opts["Conversation-only"]) == 1    # cv00
            assert len(opts["All eligible"]) == 4         # clean(2)+unused+convo
            # Unsafe buckets are never offered.
            for unsafe in ("dr00", "op00", "ac00"):
                assert unsafe not in opts["All eligible"]

            scr.cleanup = None
            scr._open_sync()
            sopts = {o["label"]: o["ids"] for o in scr.cleanup["opts"]}
            assert sopts["Eligible"] == {"el00"}          # only the FF-eligible

            # Disposition chips reflect the buckets.
            rows = {w["id4"]: w for w in scr.cleanup_rows()}
            assert rows["cl00"]["dispo_level"] == "SAFE"
            assert rows["ac00"]["dispo_level"] == "UNSAFE"
            assert rows["op00"]["dispo_level"] == ""      # open PR: healthy

    asyncio.run(run())


def test_maintenance_multiselect_and_actions_menu():
    """Maintenance: Space toggles selection, group/select-all quick-pick, and
    Enter opens the actions menu scoped to the selection (#1345)."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            scr.htab = 1
            scr.sel = scr.default_sel()
            await pilot.pause()

            # New maintenance stops exist: Select-all, group headers, rows.
            kinds = {z for z, _ in scr.stops()}
            assert {"SA", "GH", "C"} <= kinds

            recs = scr.maint_records()
            assert recs  # grouped, non-empty

            # Space on a focused row toggles just that row.
            crow = next(s for s in scr.stops() if s[0] == "C")
            scr.sel = crow
            scr._toggle_maint(crow[1])
            assert len(scr.maint_sel) == 1

            # Select-all selects every candidate; again clears.
            scr._toggle_maint_all()
            assert scr.maint_sel == scr._maint_ids()
            scr._toggle_maint_all()
            assert not scr.maint_sel

            # Enter with nothing selected selects the focused row, opens menu.
            # Focus a row that actually has a maintenance action (cleanable) --
            # a non-actionable selection no longer opens an (empty) menu.
            recs = scr.maint_records()
            ci = next(i for i, r in enumerate(recs) if scr._cleanable(r))
            scr.sel = ("C", ci)
            scr._activate()
            assert scr.maint_menu is not None
            assert scr.maint_menu["count"] == 1
            # Only real actions are offered (the Diagnostics mock was removed).
            assert "Diagnostics" not in scr.maint_menu["actions"]
            assert set(scr.maint_menu["actions"]) <= {"Sync", "Cleanup"}
            assert "Cleanup" in scr.maint_menu["actions"]
            # Enter must NOT have produced a launch/resume decision.
            assert app.result is None

            # The menu's Cleanup action opens a scope dialog over the selection.
            acts = scr.maint_menu["actions"]
            if "Cleanup" in acts:
                scr.maint_menu_idx = acts.index("Cleanup")
                scr._key_maint_menu("enter")
                assert scr.cleanup is not None
                assert "selected" in scr.cleanup["scope"]

    asyncio.run(run())


def test_maint_menu_no_actionable_selection_does_not_open():
    """A selection with no FF-eligible or cleanable worktree no longer opens an
    (empty) actions menu -- it reports a no-op instead (Diagnostics mock gone)."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            fake = [{"id4": "x1", "ff_eligible": False},
                    {"id4": "x2", "ff_eligible": False}]
            scr.maint_records = lambda: fake
            scr._cleanable = lambda rec: False   # nothing cleanable
            scr.maint_sel = ListSelection({"x1", "x2"})
            scr._open_maint_menu()
            assert scr.maint_menu is None
            assert "no maintenance action" in scr.debug

    asyncio.run(run())


def _profiles_source():
    """Fixture source exposing config-bound axes + profile IO hooks (no SSH)."""
    src = _fixture_source()
    src.host_cols = lambda: [
        ("Lambda-Core·Win", "Lambda-Core", "Win"),
        ("Borealis·Win", "Borealis", "Win"),
    ]
    src.target_envs = lambda: [("Lambda-Core", "Win"), ("Borealis", "Win")]
    # In-memory column store keyed by (machine, env).
    store: dict = {}
    applied_calls: list = []

    def load_col(machine, env):
        from agent_worktrees.profiles import self_diagonal
        return set(store.get((machine, env), {self_diagonal(machine, env)}))

    def apply_col(machine, env, sels, *, mirror=True):
        store[(machine, env)] = set(sels)
        applied_calls.append((machine, env, mirror))
        return True, "saved"

    src.load_profile_column = load_col
    src.apply_profile_column = apply_col
    src._store = store
    src._applied_calls = applied_calls
    # The engine resolves the local host from the source LOCAL; align it with a
    # host column so the self-diagonal lock lands on Lambda-Core Win.
    src.LOCAL = ("Lambda-Core", "Win")
    return src


def test_profiles_apply_writes_changed_columns():
    """Toggling a grid cell and Applying runs the per-host progress dialog and
    persists that host's column on close."""
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 2
            await pilot.pause()
            # Find a non-locked cell in the Borealis column (host index 1) and
            # toggle it on.
            hi = 1
            ti = next(t for t in range(len(scr.targets))
                      if not scr.cell_locked(t, hi))
            scr.sel = ("PR", ti)
            scr.pcol = hi
            scr._toggle_cell()
            assert scr.grid_dirty()
            # Apply via the button -> opens the per-host progress dialog.
            scr.btn_idx = 0
            scr.sel = ("BTN", 0)
            assert scr.active_button() == "PA"
            scr._activate()
            await pilot.pause()
            # Apply now opens a confirm dialog showing the add/remove diff.
            assert scr.prof_confirm is not None
            assert scr.prof_confirm["changed"]
            scr._key_prof_confirm("enter")     # confirm -> runs the progress
            await pilot.pause()
            assert scr.progress is not None
            assert scr.progress["op"] == "profiles"
            # Drive the executor to completion, then close (Enter) to commit.
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not scr.executor.is_done():
                time.sleep(0.02)
            scr._advance_progress()
            assert scr.progress["done"]
            scr._key_progress("enter")
            await pilot.pause()
        # The Borealis column was written and is no longer dirty.
        assert any(m == "Borealis" for m, _e, _mir in src._applied_calls)
        assert not scr.grid_dirty()

    asyncio.run(run())


def test_profiles_apply_confirm_cancel_is_noop():
    """Esc on the Apply confirm dialog cancels without writing or running."""
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 2
            await pilot.pause()
            hi = 1
            ti = next(t for t in range(len(scr.targets))
                      if not scr.cell_locked(t, hi))
            scr.sel = ("PR", ti)
            scr.pcol = hi
            scr._toggle_cell()
            scr.btn_idx = 0
            scr.sel = ("BTN", 0)
            scr._activate()
            await pilot.pause()
            assert scr.prof_confirm is not None
            # Confirm shows the concrete add/remove diff for the changed host.
            added, removed = scr.prof_confirm["diffs"][hi]
            assert added or removed
            scr._key_prof_confirm("escape")    # cancel
            await pilot.pause()
        assert scr.prof_confirm is None
        assert scr.progress is None
        assert src._applied_calls == []        # nothing written
        assert scr.grid_dirty()                # edit preserved, not applied

    asyncio.run(run())


def _profiles_source_unavailable():
    """Profiles fixture where the Borealis host column fails to load, as an
    old/unreachable remote does over SSH (#1370)."""
    from agent_worktrees.picker_tui import profiles_io
    src = _profiles_source()
    inner = src.load_profile_column

    def load_col(machine, env):
        if machine == "Borealis":
            return profiles_io.UNAVAILABLE
        return inner(machine, env)

    src.load_profile_column = load_col
    return src


def test_profiles_unavailable_column_is_readonly():
    """A host column that couldn't load is marked read-only: cells show '?',
    toggling is a no-op, and Apply excludes it (#1370)."""
    src = _profiles_source_unavailable()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 2
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not scr._prof_loaded:
                await pilot.pause()
            # Borealis (host col 1) is unavailable; the local col 0 is not.
            assert 1 in scr._prof_unavailable
            assert 0 not in scr._prof_unavailable
            # Cells in the unavailable column render the "unknown" marker.
            ti = next(t for t in range(len(scr.targets))
                      if not scr.cell_locked(t, 1))
            ch, _st = scr._cell_visual(ti, 1, scr.cell_locked(ti, 1))
            assert ch == "?"
            # Toggling that column is a read-only no-op.
            scr.sel = ("PR", ti)
            scr.pcol = 1
            scr._toggle_cell()
            assert not scr.grid_dirty()
            # Apply finds nothing to change (the column is excluded).
            scr._apply_profiles()
            assert scr.prof_confirm is None
            # The legend keys the agent/shell rows and flags the unavailable col.
            body = "\n".join(v.text.plain for v in scr.build_body(118))
            assert "plain SSH login shell" in body
            assert "remote unavailable" in body

    asyncio.run(run())


def test_profiles_apply_progress_carries_restart_summary():
    """After confirming an Apply, the progress dict carries the add/remove
    counts the done-state surfaces alongside the restart reminder (#1368)."""
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 2
            await pilot.pause()
            hi = 1
            ti = next(t for t in range(len(scr.targets))
                      if not scr.cell_locked(t, hi))
            scr.sel = ("PR", ti)
            scr.pcol = hi
            scr._toggle_cell()
            scr._apply_profiles()
            assert scr.prof_confirm is not None
            scr._key_prof_confirm("enter")
            await pilot.pause()
            assert scr.progress["op"] == "profiles"
            assert scr.progress["n_add"] + scr.progress["n_rem"] >= 1

    asyncio.run(run())


def test_profiles_active_row_label_highlighted():
    """The focused target row label carries the same subtle 'on grey23' shading
    the active host column header uses, so both cursor axes read -- not just the
    cell inversion cursor (#1287)."""
    src = _profiles_source()

    def _label_shaded(vrow):
        # The label occupies the first ~30 cells; look for an 'on grey23' span
        # anchored there (distinct from the 'reverse' cell cursor and from a
        # locked cell's later 'grey50 on grey23').
        return any("on grey23" in str(sp.style) and sp.start <= 2
                   for sp in vrow.text.spans)

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 2
            await pilot.pause()
            scr.sel = ("PR", 1)
            scr.pcol = 0
            rows = {getattr(v, "stop", None): v for v in scr.build_body(118)}
            assert _label_shaded(rows[("PR", 1)])       # focused row shaded
            assert not _label_shaded(rows[("PR", 0)])   # others are not

    asyncio.run(run())


def test_profiles_grid_cell_restored_on_tab():
    """Tabbing out of the Profiles grid and back restores the last-focused
    target row and host column, not the top-left default (#1288)."""
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 2
            await pilot.pause()
            scr.sel = ("PR", 1)
            scr.pcol = 1
            scr.handle_key("shift+tab")     # grid -> View region
            assert scr.sel[0] != "PR"
            scr.handle_key("tab")           # back into the grid
            assert scr.sel == ("PR", 1)     # target row restored
            assert scr.pcol == 1            # host column persisted

    asyncio.run(run())


def test_run_tui_picker_redirects_stdout_when_captured(monkeypatch):
    """When stdout is captured (launcher) but stderr is a TTY, Textual must
    render to stderr while the real stdout stays reserved for the plan."""
    import io
    import sys

    import agent_worktrees.picker_tui as pkg
    from agent_worktrees.picker_tui import engine as eng

    seen = {}

    class _FakeApp:
        def __init__(self, source, live=False, mock_mode=None):
            self.result = {"action": "cancel"}

        def run(self):
            seen["during"] = sys.__stdout__

    class _Pipe(io.StringIO):
        def isatty(self):
            return False

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    pipe, tty = _Pipe(), _Tty()
    monkeypatch.setattr(sys, "__stdout__", pipe)
    monkeypatch.setattr(sys, "stderr", tty)
    monkeypatch.setattr(eng, "PickerApp", _FakeApp)

    result = pkg.run_tui_picker(source=object(), live=False)
    assert result == {"action": "cancel"}
    assert seen["during"] is tty       # Textual rendered to the terminal
    assert sys.__stdout__ is pipe      # restored: fd1 free for the JSON plan


def test_run_tui_picker_no_redirect_in_real_terminal(monkeypatch):
    """In a normal terminal (stdout is a TTY) no redirect happens."""
    import io
    import sys

    import agent_worktrees.picker_tui as pkg
    from agent_worktrees.picker_tui import engine as eng

    seen = {}

    class _FakeApp:
        def __init__(self, source, live=False, mock_mode=None):
            self.result = None

        def run(self):
            seen["during"] = sys.__stdout__

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    tty_out, tty_err = _Tty(), _Tty()
    monkeypatch.setattr(sys, "__stdout__", tty_out)
    monkeypatch.setattr(sys, "stderr", tty_err)
    monkeypatch.setattr(eng, "PickerApp", _FakeApp)

    pkg.run_tui_picker(source=object(), live=False)
    assert seen["during"] is tty_out   # unchanged -- Textual uses stdout


def test_bucket_sections_key_off_state():
    """Active = in-session (ACTIVE); Completed = FINAL (any age); Recent = rest."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)

    def rec(raw_state, hours):
        ts = (derive.NOW - datetime.timedelta(hours=hours)).isoformat()
        return derive.norm(
            {"id": f"x-{raw_state}-{hours}", "status": "active",
             "state": raw_state, "started_at": ts}, "m", "Win")

    wts = [rec("active", 1), rec("completed", 1), rec("completed", 60),
           rec("wip", 2), rec("unused", 3)]
    active, recent, completed = derive.bucket(wts)
    assert [w["state"] for w in active] == ["ACTIVE"]
    # Both FINALs land in Completed regardless of age (1h and 60h).
    assert sorted(w["state"] for w in completed) == ["FINAL", "FINAL"]
    # Recent is whatever is neither in-session nor final.
    assert sorted(w["state"] for w in recent) == ["UNUSED", "WIP"]


def test_sessionless_flag_only_when_count_known_zero():
    """#1026: sessionless is flagged only when session_count is present and 0,
    with no turns / mux ownership and not a managed kind."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)

    def n(**extra):
        base = {"id": "lambda-core-win-zzzz", "status": "active",
                "state": "wip", "started_at": "2026-06-27T17:00:00"}
        base.update(extra)
        return derive.norm(base, "m", "Win")

    assert n(session_count=0)["sessionless"] is True            # the orphan
    assert n(session_count=2)["sessionless"] is False           # owned
    assert n()["sessionless"] is False                          # unknown (absent)
    assert n(session_count=0, turn_count=3)["sessionless"] is False   # had turns
    assert n(session_count=0, mux_attached=True)["sessionless"] is False
    assert n(session_count=0, kind="bridge")["sessionless"] is False  # managed


def _sessionless_source():
    """One normal (owned) worktree + one sessionless orphan (session_count 0)."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")
    raws = [
        {"id": "lambda-core-win-owned", "title": "Owned wip",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 4, "state": "wip", "session_count": 1},
        {"id": "lambda-core-win-orph", "title": "Orphan wip",
         "status": "active", "started_at": "2026-06-27T16:00:00",
         "turn_count": 0, "state": "wip", "session_count": 0,
         "pr": {"number": 99, "state": "open"}},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def test_picker_buckets_sessionless_into_unowned():
    """#1026: a worktree with no owning session lands in a distinct 'Unowned'
    section (not Recent), and its sub-menu omits Resume."""
    src = _sessionless_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            _cols, sections = scr.current_list()
            labels = [lbl for lbl, _rows in sections]
            unowned = next(rows for lbl, rows in sections if lbl.startswith("Unowned"))
            recent = next(rows for lbl, rows in sections if lbl == "Recent")
            assert any(lbl.startswith("Unowned") for lbl in labels)
            assert [w["id4"] for w in unowned] == ["orph"[-4:]]
            assert all(not w.get("sessionless") for w in recent)
            # Select the orphan and open its sub-menu -> no Resume offered.
            recs = scr.list_records()
            oi = next(i for i, w in enumerate(recs) if w.get("sessionless"))
            scr.sel = ("L", oi)
            scr._open_submenu()
            assert "Resume" not in scr.submenu["actions"]
            assert "Open" in scr.submenu["actions"]

    asyncio.run(run())


def test_reconcile_prs_counts_terminal_transitions(monkeypatch):
    """#1423: reconcile_prs reconciles each non-terminal active PR and counts
    those that moved to a terminal state, skipping no-PR / already-terminal."""
    from pathlib import Path

    from agent_worktrees.picker_tui import data_local

    class FakePR:
        def __init__(self, number, state):
            self.number, self.state = number, state

    class FakeRec:
        def __init__(self, pr):
            self._pr = pr

        def active_pr(self):
            return self._pr

    open_pr = FakePR(1, "open")
    recs = [FakeRec(open_pr), FakeRec(FakePR(2, "merged")), FakeRec(None)]
    monkeypatch.setattr(data_local.cfg, "load_config", lambda: object())
    monkeypatch.setattr(data_local.cfg, "tracking_dir", lambda: Path("."))
    monkeypatch.setattr(data_local.cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(data_local.tracking, "list_records",
                        lambda p, platform_filter=None: recs)

    def fake_reconcile(rec, config):
        if rec.active_pr() is open_pr:        # provider reports it merged
            open_pr.state = "merged"

    monkeypatch.setattr("agent_worktrees.pr_ops._reconcile_active_pr",
                        fake_reconcile)
    assert data_local.reconcile_prs() == 1


def test_picker_background_pr_reconcile_reloads_on_change():
    """#1423: when the background reconcile reports a change, the non-live path
    reloads local data so the render reflects the corrected PR state."""
    src = _fixture_source()
    calls = {"reconcile": 0, "load": 0}
    orig_load = src.load

    def load2():
        calls["load"] += 1
        return orig_load()

    def reconcile_prs():
        calls["reconcile"] += 1
        return 1

    src.load = load2
    src.reconcile_prs = reconcile_prs

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not scr._pr_reconciled:
                await pilot.pause()
            assert calls["reconcile"] == 1
            assert calls["load"] >= 2       # setup load + post-reconcile reload

    asyncio.run(run())


def test_picker_background_pr_reconcile_no_change_no_reload():
    """A reconcile that changes nothing must not trigger a reload."""
    src = _fixture_source()
    calls = {"load": 0}
    orig_load = src.load

    def load2():
        calls["load"] += 1
        return orig_load()

    src.load = load2
    src.reconcile_prs = lambda: 0

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not scr._pr_reconciled:
                await pilot.pause()
            assert calls["load"] == 1       # only the setup load

    asyncio.run(run())


def test_maybe_repoll_gating(monkeypatch):
    """#1421: _maybe_repoll fires once per interval, and is suppressed on the
    Profiles tab, during a progress dialog, and when POLL_SECS <= 0."""
    from agent_worktrees.picker_tui import engine as eng

    class FakeLoader:
        def __init__(self):
            self.polls = 0

        def repoll_silent(self, keys=None):
            self.polls += 1
            return 0

    loader = FakeLoader()

    def fresh_obj():
        obj = types.SimpleNamespace(
            loader=loader, progress=None, htab=0,
            pivots=[{"kind": "worktrees"}, {"kind": "maintenance"}, {"kind": "profiles"}],
            _last_poll=time.monotonic() - 1000,
            _poll_keys=lambda: {("m", "Win")})
        # Dispatch is keyed off pivot *kind* now, not the raw htab index.
        obj._kind = lambda idx=None: obj.pivots[obj.htab if idx is None else idx]["kind"]
        return obj

    monkeypatch.setattr(eng, "POLL_SECS", 45.0)
    obj = fresh_obj()

    eng.PickerScreen._maybe_repoll(obj)
    assert loader.polls == 1                 # due -> fires
    eng.PickerScreen._maybe_repoll(obj)
    assert loader.polls == 1                 # within interval -> no-op

    obj._last_poll = time.monotonic() - 1000
    obj.htab = 2                             # Profiles tab -> suppressed
    eng.PickerScreen._maybe_repoll(obj)
    assert loader.polls == 1

    obj.htab = 0
    obj.progress = {"done": False}           # dialog up -> suppressed
    eng.PickerScreen._maybe_repoll(obj)
    assert loader.polls == 1

    obj.progress = None
    monkeypatch.setattr(eng, "POLL_SECS", 0.0)   # disabled
    eng.PickerScreen._maybe_repoll(obj)
    assert loader.polls == 1


def test_bucket_fallback_no_classify_finalized_is_clean_not_wip():
    """An old remote (no --classify -> no state) must not show FINAL + unmerged."""
    # status finalized, no git classification -> display FINAL, bucket clean.
    w = derive.norm(
        {"id": "borealis-wsl-1234", "status": "finalized",
         "started_at": "2026-06-25T10:00:00"}, "Borealis", "WSL")
    assert w["state"] == "FINAL"
    assert w["cleanup_bucket"] == "clean"          # not 'wip'/'unmerged'
    assert derive.BUCKET_DISPO[w["cleanup_bucket"]] == "SAFE"

    # status active, no classification, no PR -> unknown (neutral, not unmerged).
    w2 = derive.norm(
        {"id": "borealis-wsl-5678", "status": "active",
         "started_at": "2026-06-25T10:00:00"}, "Borealis", "WSL")
    assert w2["cleanup_bucket"] == "unknown"
    assert derive.BUCKET_DISPO[w2["cleanup_bucket"]] == ""   # no chip


def test_new_picker_flag_gating(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_NEW_PICKER", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_LEGACY_PICKER", raising=False)
    assert new_picker_enabled() is False
    monkeypatch.setenv("AGENT_WORKTREES_NEW_PICKER", "1")
    assert new_picker_enabled() is True
    # Legacy override always wins (the rollback switch).
    monkeypatch.setenv("AGENT_WORKTREES_LEGACY_PICKER", "1")
    assert new_picker_enabled() is False


def test_tui_renders_local_worktrees():
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:  # noqa: F841
            scr = app.query_one(PickerScreen)
            out = str(scr.render())
            assert "Agent Worktrees" in out
            # Canonical state vocabulary (aperture-labs #1290).
            assert "WIP" in out
            assert "UNUSED" in out
            assert "FINAL" in out
            # Real machine identity from the source.
            assert "lambda-core" in out

    asyncio.run(run())


def test_topbar_repo_branch_are_data_backed():
    """The top bar's repo + default-branch segments come from the data source
    (config), not a hardcoded ``aperture-labs`` / ``master``."""
    src = _fixture_source()
    src.REPO = "copilot-extensions"
    src.BRANCH = "main"

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:  # noqa: F841
            scr = app.query_one(PickerScreen)
            out = str(scr.render())
            assert "copilot-extensions" in out
            assert "main" in out
            # The old hardcoded values must not leak in.
            assert "aperture-labs" not in out
            assert "master" not in out

    asyncio.run(run())


def test_topbar_drops_repo_branch_when_source_omits_them():
    """A source without REPO/BRANCH (e.g. a bare fixture) renders no repo or
    branch segment rather than a fabricated one."""
    src = _fixture_source()  # SimpleNamespace -> no REPO/BRANCH attrs

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:  # noqa: F841
            scr = app.query_one(PickerScreen)
            out = str(scr.render())
            assert "Agent Worktrees" in out
            assert "aperture-labs" not in out

    asyncio.run(run())


class _FakeLoader:
    """In-memory loader matching the engine's live contract (no SSH)."""

    def __init__(self, records_by_key, states):
        self._records = records_by_key
        self._states = states

    def start(self):
        pass

    def state(self, machine, env):
        return self._states.get((machine, env), "loading")

    def records(self):
        out = []
        for key, recs in self._records.items():
            if self._states.get(key) == "ready":
                out.extend(recs)
        return out

    def counts(self):
        vals = list(self._states.values())
        return (
            sum(1 for v in vals if v == "ready"),
            sum(1 for v in vals if v == "loading"),
            sum(1 for v in vals if v == "failed"),
        )

    def error(self, machine, env):
        return None


def _live_fixture_source():
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("Lambda-Core", "Win")
    remote = ("Borealis", "Win")
    local_raw = {"id": "lambda-core-win-20260627-aaaa", "title": "Local wip",
                 "status": "active", "started_at": "2026-06-27T17:00:00",
                 "turn_count": 4, "state": "wip"}
    remote_raw = {"id": "borealis-win-20260627-bbbb", "title": "Remote work",
                  "status": "active", "started_at": "2026-06-27T16:30:00",
                  "turn_count": 2, "state": "wip"}
    records_by_key = {
        local: [derive.norm(local_raw, *local)],
        remote: [derive.norm(remote_raw, *remote)],
    }
    states = {local: "ready", remote: "ready",
              ("Wheatley", "Linux"): "loading"}

    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [
        ("Lambda-Core Win", "Lambda-Core", "Win", True),
        ("Borealis Win", "Borealis", "Win", True),
        ("Wheatley Linux", "Wheatley", "Linux", True),
    ]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: []
    src.make_loader = lambda: _FakeLoader(records_by_key, states)
    return src


def test_tui_live_multi_machine():
    src = _live_fixture_source()

    async def run():
        app = PickerApp(src, live=True)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            # Switch to the "All" tab so every ready machine interleaves.
            scr.machine_idx = 0
            # Drive a render tick so live records stream in from the loader.
            scr._tick()
            await pilot.pause()
            out = str(scr.render())
            assert "Agent Worktrees" in out
            # Both ready machines' worktrees stream into the All view.
            assert "Local wip" in out
            assert "Remote work" in out

    asyncio.run(run())


def test_live_loader_classify_fallback(monkeypatch):
    """A remote that rejects --classify is retried without it (older remotes)."""
    from agent_worktrees.picker_tui import data_ssh

    calls = []

    class _Proc:
        def __init__(self, rc, stdout="", stderr=""):
            self.returncode = rc
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(argv, timeout):
        calls.append(list(argv))
        joined = " ".join(argv)
        if "--classify" in joined:
            return _Proc(2, stderr="error: unrecognized arguments: --classify")
        return _Proc(0, stdout='{"worktrees": []}')

    monkeypatch.setattr(data_ssh, "_run", fake_run)
    src = data_ssh.Source(
        "Borealis", "Win",
        ["ssh", "borealis", "pwsh -NoProfile -Command 'p list --json "
         "--classify --mux-details --include-other-platforms'"],
        ready=True,
    )
    recs = data_ssh._fetch(src)
    assert recs == []
    assert len(calls) == 2
    assert "--classify" in " ".join(calls[0])
    assert "--classify" not in " ".join(calls[1])
    assert src.use_classify is False


def test_resume_decision_exits_with_worktree():
    """Enter on a worktree row opens the sub-menu; Open then resumes (#1343)."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            scr.sel = ("L", 0)
            scr._activate()                 # opens the sub-menu, no exit
            await pilot.pause()
            assert scr.submenu is not None
            assert scr.submenu["actions"][0] == "Open"
            scr._key_submenu("enter")       # default-focused Open -> resume
            await pilot.pause()
        assert app.result is not None
        assert app.result["action"] == "resume"
        assert app.result["worktree_id"]  # real id carried from raw record
        assert app.result["is_local"] is True

    asyncio.run(run())


def test_open_submenu_no_mux_toggle():
    """Space toggles No-mux on Open; the resume decision carries it (#1343)."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            scr.sel = ("L", 0)
            scr._open_submenu()
            scr._key_submenu("space")       # toggle No-mux while Open focused
            assert scr.submenu["no_mux"] is True
            scr._key_submenu("enter")
            await pilot.pause()
        assert app.result["action"] == "resume"
        assert app.result["options"]["no_mux"] is True

    asyncio.run(run())


def _verb_fixture_source():
    """Local source with an ACTIVE (live-mux), a STOPPED (history, no mux), and
    a SESSIONLESS worktree -- for the state-driven submenu verb tests."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")
    raws = [
        # Live mux -> Open (+ Stop).
        {"id": "lambda-core-win-20260627-live", "title": "Live session",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 4, "state": "wip", "session_count": 1,
         "mux_session": True, "mux_attached": True, "mux_clients": 1},
        # Prior session, no live mux -> Resume (no Stop).
        {"id": "lambda-core-win-20260627-stop", "title": "Stopped session",
         "status": "active", "started_at": "2026-06-27T16:00:00",
         "turn_count": 3, "state": "wip", "session_count": 1},
        # No session ever -> Open only (cold), no Resume/Stop.
        {"id": "lambda-core-win-20260627-none", "title": "Never opened",
         "status": "active", "started_at": "2026-06-27T15:00:00",
         "turn_count": 0, "state": "unused", "session_count": 0},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def test_submenu_verbs_track_session_liveness():
    """Active (live mux) -> Open + Stop; stopped -> Resume, no Stop; sessionless
    -> Open only. The primary verb and the presence of Stop follow ``mux_live``
    (#1343)."""
    src = _verb_fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            recs = scr.list_records()
            by_id4 = {w["id4"]: i for i, w in enumerate(recs)}

            scr.sel = ("L", by_id4["live"])
            scr._open_submenu()
            acts = scr.submenu["actions"]
            assert acts[0] == "Open"
            assert "Resume" not in acts
            assert "Stop" in acts
            scr.submenu = None

            scr.sel = ("L", by_id4["stop"])
            scr._open_submenu()
            acts = scr.submenu["actions"]
            assert acts[0] == "Resume"
            assert "Open" not in acts
            assert "Stop" not in acts     # nothing live to stop
            scr.submenu = None

            scr.sel = ("L", by_id4["none"])
            scr._open_submenu()
            acts = scr.submenu["actions"]
            assert acts[0] == "Open"      # cold start
            assert "Resume" not in acts
            assert "Stop" not in acts

    asyncio.run(run())


def test_submenu_stop_starts_single_item_restart_run(monkeypatch):
    """Enter on 'Stop' launches a one-item op=restart progress run through the
    real maintenance executor path (not a mock note)."""
    monkeypatch.setenv("AGENT_WORKTREES_PICKER_REAL_OPS", "1")
    from agent_worktrees.picker_tui import maintenance as mnt

    started = {}

    class _FakeExec:
        def __init__(self, op, tasks):
            started["op"] = op
            started["n"] = len(tasks)

        def start(self):
            started["started"] = True

        def state(self, key):
            return "done"

        def is_done(self):
            return True

        def counts(self):
            return (started.get("n", 0), 0, 0)

    monkeypatch.setattr(mnt, "MaintenanceExecutor", _FakeExec)
    monkeypatch.setattr(
        mnt, "build_tasks",
        lambda op, recs, src, **kw: [(w["id4"], None) for w in recs])

    src = _verb_fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            recs = scr.list_records()
            i = next(j for j, w in enumerate(recs) if w["id4"] == "live")
            scr.sel = ("L", i)
            scr._open_submenu()
            si = scr.submenu["actions"].index("Stop")
            scr.submenu_idx = si
            scr._key_submenu("enter")
            assert scr.progress is not None
            assert scr.progress["op"] == "restart"
            assert scr.progress["verb"] == "Stop"
            assert len(scr.progress["items"]) == 1
            assert started.get("op") == "restart"
            assert started.get("started") is True
            scr._poll_executor()
            assert scr.progress["done"] is True
            await pilot.pause()

    asyncio.run(run())


def test_new_worktree_decision_exits():
    """New worktree… opens the options dialog; Create exits with a decision."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 0
            scr.btn_idx = 0
            scr.sel = ("BTN", 0)
            assert scr.active_button() == "N"
            scr._activate()                 # opens the options dialog (#1346)
            await pilot.pause()
            assert scr.optmenu is not None
            assert scr.optmenu["section"] == 1   # Create default-selected
            assert all(not o["on"] for o in scr.optmenu["opts"])
            scr._dlg_confirm(om=True)       # confirm Create, no options
            await pilot.pause()
        assert app.result is not None
        assert app.result["action"] == "new"
        assert app.result["is_local"] is True
        assert app.result["options"] == {
            "anchor": False, "bare": False,
            "no_mux": False, "local_model": False,
        }

    asyncio.run(run())


def test_new_worktree_no_mux_option():
    """The New-worktree options dialog exposes a No-Mux toggle; enabling it
    carries no_mux=True into the launch decision -- on-demand no-mux for a
    fresh worktree, not just Open/Resume (#1225)."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 0
            scr.btn_idx = 0
            scr.sel = ("BTN", 0)
            scr._activate()                     # opens the options dialog
            await pilot.pause()
            assert scr.optmenu is not None
            labels = [o["label"] for o in scr.optmenu["opts"]]
            assert "No Mux" in labels
            nm = labels.index("No Mux")
            scr._key_scopedlg("up", om=True)    # Create button -> options
            assert scr.optmenu["section"] == 0
            while scr.optmenu["idx"] < nm:
                scr._key_scopedlg("down", om=True)
            scr._key_scopedlg("space", om=True)  # toggle No Mux on
            assert scr.optmenu["opts"][nm]["on"] is True
            scr._key_scopedlg("enter", om=True)  # options -> button row
            scr._key_scopedlg("enter", om=True)  # confirm Create
            await pilot.pause()
        assert app.result["action"] == "new"
        assert app.result["options"]["no_mux"] is True

    asyncio.run(run())


def _drain(ex, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ex.is_done():
            return
        time.sleep(0.02)
    raise AssertionError("executor did not finish")


def test_maintenance_executor_cleanup_states():
    from agent_worktrees.picker_tui import maintenance as mnt

    def boom():
        raise RuntimeError("kaboom")

    tasks = [
        ("a", lambda: {"removed": True, "ok": True}),
        ("b", lambda: {"removed": False, "ok": False, "reason": "unsafe"}),
        ("c", boom),
    ]
    ex = mnt.MaintenanceExecutor("cleanup", tasks)
    ex.start()
    _drain(ex)
    assert ex.state("a") == "done"
    assert ex.state("b") == "failed"
    assert ex.state("c") == "failed"
    assert ex.counts() == (1, 2, 0)
    assert ex.result("c")["reason"] == "kaboom"


def test_maintenance_executor_sync_uptodate_is_success():
    from agent_worktrees.picker_tui import maintenance as mnt

    tasks = [
        ("a", lambda: {"updated": True, "reason": "updated", "behind": 2}),
        ("b", lambda: {"updated": False, "reason": "up-to-date"}),
        ("c", lambda: {"updated": False, "reason": "ahead"}),
    ]
    ex = mnt.MaintenanceExecutor("sync", tasks)
    ex.start()
    _drain(ex)
    assert ex.state("a") == "done"   # fast-forwarded
    assert ex.state("b") == "done"   # already current
    assert ex.state("c") == "failed"  # skipped (ahead)


def test_maintenance_executor_restart_states():
    """restart maps on the primitive's ``ok``: a graceful/hard/none stop is
    success; a failed hard-kill is a failed item."""
    from agent_worktrees.picker_tui import maintenance as mnt

    tasks = [
        ("a", lambda: {"had_session": True, "method": "graceful", "ok": True}),
        ("b", lambda: {"had_session": True, "method": "hard", "ok": True}),
        ("c", lambda: {"had_session": False, "method": "none", "ok": True}),
        ("d", lambda: {"had_session": True, "method": "failed", "ok": False}),
    ]
    ex = mnt.MaintenanceExecutor("restart", tasks)
    ex.start()
    _drain(ex)
    assert ex.state("a") == "done"   # graceful quit
    assert ex.state("b") == "done"   # hard mux kill
    assert ex.state("c") == "done"   # nothing was running
    assert ex.state("d") == "failed"  # kill failed


def test_make_task_local_restart_calls_primitive(monkeypatch):
    """A local restart task routes to sessions.restart_worktree_copilot with
    the worktree id -- not the cleanup/sync helpers."""
    from agent_worktrees.picker_tui import maintenance as mnt
    from agent_worktrees import sessions

    seen = {}

    def _fake_restart(wt_id):
        seen["id"] = wt_id
        return {"had_session": True, "method": "graceful", "ok": True}

    monkeypatch.setattr(sessions, "restart_worktree_copilot", _fake_restart)
    src = types.SimpleNamespace(LOCAL=("M", "Win"))
    tasks = mnt.build_tasks(
        "restart",
        [{"id4": "wxyz", "raw": {"id": "wt-wxyz"}, "machine": "M", "env": "Win"}],
        src)
    (_key, fn) = tasks[0]
    res = fn()
    assert seen["id"] == "wt-wxyz"
    assert res["ok"] is True


def test_cleanup_extra_confirm_gate_and_real_executor(monkeypatch):
    """Beyond-clean cleanup requires an extra confirm, then runs the executor."""
    monkeypatch.setenv("AGENT_WORKTREES_PICKER_REAL_OPS", "1")
    from agent_worktrees.picker_tui import maintenance as mnt

    started = {}

    class _FakeExec:
        def __init__(self, op, tasks):
            started["n"] = len(tasks)

        def start(self):
            started["started"] = True

        def state(self, key):
            return "done"

        def is_done(self):
            return True

        def counts(self):
            return (started.get("n", 0), 0, 0)

    monkeypatch.setattr(mnt, "MaintenanceExecutor", _FakeExec)
    monkeypatch.setattr(
        mnt, "build_tasks",
        lambda op, recs, src, **kw: [(w["id4"], None) for w in recs])

    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            assert scr.real_ops is True
            scr.machine_idx = scr.local_index()
            scr._open_cleanup()
            # Select a beyond-clean scope (Unused) -> extra confirm required.
            for o in scr.cleanup["opts"]:
                o["on"] = o["label"] in ("Merged & finalized", "Unused")
            scr._dlg_confirm(False)
            assert scr.progress is not None
            assert scr.progress["armed"] is False   # gated
            assert scr.executor is None              # not started yet
            # Confirm -> arm -> executor starts.
            scr._key_progress("enter")
            assert scr.progress["armed"] is True
            assert started.get("started") is True
            scr._poll_executor()
            assert scr.progress["done"] is True
            await pilot.pause()

    asyncio.run(run())


# ---- prefetch cancellation (picker-perf bug: orphaned SSH list workers) ----

def _sleeper_argv(seconds: int):
    import sys
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_live_loader_spawn_tracks_and_unregisters():
    """``_spawn`` runs a child, returns a CompletedProcess, and leaves no
    tracked process behind once it completes."""
    import sys

    from agent_worktrees.picker_tui import data_ssh

    loader = data_ssh.LiveLoader(sources=[])
    proc = loader._spawn([sys.executable, "-c", "print('hi')"], timeout=15)
    assert proc.returncode == 0
    assert "hi" in proc.stdout
    with loader._procs_lock:
        assert loader._procs == []   # unregistered after completion


def test_live_loader_spawn_after_cancel_raises():
    """Once cancelled, the loader refuses to spawn new prefetch children."""
    import sys

    import pytest

    from agent_worktrees.picker_tui import data_ssh

    loader = data_ssh.LiveLoader(sources=[])
    loader.cancel()
    with pytest.raises(RuntimeError):
        loader._spawn([sys.executable, "-c", "pass"], timeout=5)


def test_live_loader_cancel_kills_inflight_prefetch():
    """The core fix: cancelling the loader kills an in-flight prefetch child so
    it can't orphan into a heavy git-classify after the picker exits."""
    import threading
    import time

    from agent_worktrees.picker_tui import data_ssh

    loader = data_ssh.LiveLoader(sources=[])
    result = {}

    def run():
        try:
            result["proc"] = loader._spawn(_sleeper_argv(30), timeout=30)
        except Exception as exc:  # pragma: no cover - failure path
            result["err"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()

    # Wait until the child is registered as in-flight.
    for _ in range(100):
        with loader._procs_lock:
            if loader._procs:
                break
        time.sleep(0.05)
    with loader._procs_lock:
        assert loader._procs, "prefetch child should be tracked while running"
        child = loader._procs[0]

    loader.cancel()
    t.join(timeout=15)

    assert not t.is_alive(), "spawn thread must unblock after cancel kills child"
    assert child.poll() is not None, "the prefetch child must be dead"
    with loader._procs_lock:
        assert loader._procs == [], "tracked child must be removed after cancel"


def test_screen_on_unmount_cancels_loader():
    """The picker's teardown hook cancels the loader (no orphaned prefetch)."""
    from agent_worktrees.picker_tui import data_local
    from agent_worktrees.picker_tui.engine import PickerScreen

    screen = PickerScreen(data_local, live=True)
    cancelled = {"v": False}

    class _FakeLoader:
        def cancel(self):
            cancelled["v"] = True

    screen.loader = _FakeLoader()
    screen.on_unmount()
    assert cancelled["v"] is True


def test_update_indicator_focus_glyph_and_refresh():
    """The launcher-stage update state drives the version glyph, the focusable
    refresh stop, and the refresh decision (#1430)."""
    from agent_worktrees.picker_tui.engine import PickerScreen

    src = _fixture_source()
    s = PickerScreen(src, live=False)
    s.setup()
    s.htab = 0
    s.frame = 0

    # current: informational only -> no focus stop, checkmark glyph.
    s.update_state = "current"
    assert ("V", 1) not in s.stops()
    assert not s._update_actionable()
    assert "\u2713" in s._update_seg(False).plain          # ✓

    # available: focusable refresh stop, refresh glyph.
    s.update_state = "available"
    assert ("V", 1) in s.stops()
    assert s._update_actionable()
    assert "\u21bb" in s._update_seg(False).plain          # ↻

    # checking: a spinner segment, still not a focus target.
    s.update_state = "checking"
    assert ("V", 1) not in s.stops()
    assert s._update_seg(False) is not None

    # idle: no segment at all.
    s.update_state = "idle"
    assert s._update_seg(False) is None

    # Enter on the refresh icon records an action=refresh decision.
    captured = {}
    s._decide = lambda d: captured.update(d)
    s.update_state = "available"
    s.sel = ("V", 1)
    s._activate()
    assert captured == {"action": "refresh"}


def test_mock_mode_default_off_explicit_and_env(monkeypatch):
    """Mock mode is off by default and never turns on implicitly. It is enabled
    only explicitly: the ``mock_mode`` arg, the canonical env
    ``AGENT_WORKTREES_PICKER_MOCK``, or the deprecated ``..._REAL_OPS=0``.
    ``real_ops`` is exactly ``not mock_mode``."""
    src = _fixture_source()

    def _flags(*, arg=None, mock_env=None, realops_env=None):
        monkeypatch.delenv("AGENT_WORKTREES_PICKER_MOCK", raising=False)
        monkeypatch.delenv("AGENT_WORKTREES_PICKER_REAL_OPS", raising=False)
        if mock_env is not None:
            monkeypatch.setenv("AGENT_WORKTREES_PICKER_MOCK", mock_env)
        if realops_env is not None:
            monkeypatch.setenv("AGENT_WORKTREES_PICKER_REAL_OPS", realops_env)

        async def _run():
            app = PickerApp(src, live=False, mock_mode=arg)
            async with app.run_test(size=(118, 36)):
                scr = app.query_one(PickerScreen)
                return scr.mock_mode, scr.real_ops

        return asyncio.run(_run())

    # Default: real (mock off).
    assert _flags() == (False, True)
    # Explicit arg wins over everything.
    assert _flags(arg=True) == (True, False)
    assert _flags(arg=False, mock_env="1") == (False, True)
    # Canonical env.
    assert _flags(mock_env="1") == (True, False)
    assert _flags(mock_env="0") == (False, True)      # falsey -> off
    assert _flags(mock_env="false") == (False, True)
    # Deprecated alias: REAL_OPS=0 forces mock; =1/unset stays real.
    assert _flags(realops_env="0") == (True, False)
    assert _flags(realops_env="1") == (False, True)


def test_profiles_apply_real_mode_missing_hook_is_honest(monkeypatch):
    """In real mode a source with no apply hook does NOT fake success -- it
    reports the gap. (The no-op 'Applied (mock)' only happens in mock mode.)"""
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_MOCK", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_REAL_OPS", raising=False)
    src = _profiles_source()
    # Strip the apply hook to simulate a misconfigured real source.
    if hasattr(src, "apply_profile_column"):
        del src.apply_profile_column

    async def run():
        app = PickerApp(src, live=False)   # real mode
        async with app.run_test(size=(118, 40)):
            scr = app.query_one(PickerScreen)
            assert scr.mock_mode is False
            scr._prof_apply = None
            scr.grid = {(0, 0): True}
            scr.applied = {}
            scr._apply_profiles()
            assert "unavailable" in scr.debug.lower()
            assert "(mock)" not in scr.debug
            assert scr.applied == {}       # nothing was banked

    asyncio.run(run())


def test_profiles_apply_mock_mode_is_noop(monkeypatch):
    """In mock mode profiles Apply is a labelled no-op (no writes)."""
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_MOCK", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_REAL_OPS", raising=False)
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False, mock_mode=True)
        async with app.run_test(size=(118, 40)):
            scr = app.query_one(PickerScreen)
            assert scr.mock_mode is True
            scr.grid = {(0, 0): True}
            scr.applied = {}
            scr._apply_profiles()
            assert "(mock)" in scr.debug
            assert scr.applied == scr.grid           # banked, but no IO
            assert src._applied_calls == []          # apply hook never called

    asyncio.run(run())



def _wait_state(loader, machine, env, want, timeout=5.0):
    deadline = time.perf_counter() + timeout
    while loader.state(machine, env) != want:
        if time.perf_counter() > deadline:
            break
        time.sleep(0.01)
    return loader.state(machine, env)


def test_live_loader_local_streams_without_blocking_start(monkeypatch):
    """start() never blocks on the local source: it threads local too, so the
    picker paints and accepts input immediately while the (sometimes
    multi-second) local git-classification streams in just like the remotes.

    Regression guard for the freeze where a slow local load held the event-loop
    thread inside on_mount -> no paint, no arrow keys -- until every source
    (including the SSH fan-out) had resolved."""
    from agent_worktrees.picker_tui import data_ssh

    sentinel = [{"id4": "abcd", "machine": "lambda-core", "env": "Win"}]
    gate = threading.Event()

    def _slow_local(m=None, e=None, *, classify=True):
        gate.wait(5)
        return sentinel

    monkeypatch.setattr(data_ssh.data_local, "load", _slow_local)

    local = data_ssh.Source("lambda-core", "Win", None, local=True)
    remote = data_ssh.Source("borealis", "Win", _sleeper_argv(30),
                             local=False, alias="borealis", shell="pwsh")
    loader = data_ssh.LiveLoader(sources=[local, remote])
    t0 = time.perf_counter()
    loader.start()
    elapsed = time.perf_counter() - t0
    try:
        # start() returned immediately -- it did NOT wait on the slow local load.
        assert elapsed < 1.0
        assert loader.state("lambda-core", "Win") == "loading"
        # Release the local load; it resolves on its thread and streams in.
        gate.set()
        assert _wait_state(loader, "lambda-core", "Win", "ready") == "ready"
        assert loader.records() == sentinel
        # The remote is still loading throughout -- never blocked on.
        assert loader.state("borealis", "Win") == "loading"
    finally:
        gate.set()
        loader.cancel()


def test_live_loader_reload_local_refetches(monkeypatch):
    """reload() re-fetches the local source on a thread so a post-maintenance
    refresh streams back in without blocking the UI (#1421 live re-render)."""
    from agent_worktrees.picker_tui import data_ssh

    # The local loader is two-phase (fast classify=False, then full
    # classify=True), so a single load event calls data_local.load more than
    # once. Return the currently-staged rows regardless of call count / phase,
    # and let the test swap what "current" means between start and reload.
    state = {"rows": [{"id4": "a"}]}

    def _load(m=None, e=None, *, classify=True):
        return list(state["rows"])

    monkeypatch.setattr(data_ssh.data_local, "load", _load)
    local = data_ssh.Source("lambda-core", "Win", None, local=True)
    loader = data_ssh.LiveLoader(sources=[local])
    loader.start()
    assert _wait_state(loader, "lambda-core", "Win", "ready") == "ready"
    assert loader.records() == [{"id4": "a"}]
    state["rows"] = [{"id4": "b"}]
    assert loader.reload("lambda-core", "Win") is True
    assert _wait_state(loader, "lambda-core", "Win", "ready") == "ready"
    # Give the reload's two-phase fill a beat to swap the authoritative rows in.
    for _ in range(200):
        if loader.records() == [{"id4": "b"}]:
            break
        time.sleep(0.01)
    assert loader.records() == [{"id4": "b"}]
    assert loader.reload("nope", "X") is False


def test_live_loader_local_two_phase_fast_then_fill(monkeypatch):
    """Local tab shows fast provisional rows first (classify=False), then swaps
    in the authoritative git-classified rows (classify=True) -- the perf fix so
    a slow/stalled git classification never blocks the whole tab."""
    from agent_worktrees.picker_tui import data_ssh

    fast_gate = threading.Event()
    full_gate = threading.Event()
    calls = []

    def _load(m=None, e=None, *, classify=True):
        calls.append(classify)
        if classify:
            full_gate.wait(5)
            return [{"id4": "full", "state": "WIP"}]
        fast_gate.wait(5)
        return [{"id4": "fast", "state": "?"}]

    monkeypatch.setattr(data_ssh.data_local, "load", _load)
    local = data_ssh.Source("lambda-core", "Win", None, local=True)
    loader = data_ssh.LiveLoader(sources=[local])
    loader.start()
    try:
        # Phase 1 (fast) resolves first -> provisional rows visible, ready.
        fast_gate.set()
        assert _wait_state(loader, "lambda-core", "Win", "ready") == "ready"
        assert loader.records() == [{"id4": "fast", "state": "?"}]
        # Phase 2 (full git classification) then swaps authoritative rows in.
        full_gate.set()
        for _ in range(200):
            if loader.records() == [{"id4": "full", "state": "WIP"}]:
                break
            time.sleep(0.01)
        assert loader.records() == [{"id4": "full", "state": "WIP"}]
        # Fast pass ran without classification; full pass ran with it.
        assert calls[0] is False and True in calls
    finally:
        fast_gate.set()
        full_gate.set()
        loader.cancel()


def test_escape_on_main_view_confirms_before_quit(monkeypatch):
    """Esc/q on a main pivot view opens a quit-confirm instead of instant-quit;
    Esc/n stays, y quits, Enter acts on the focused button (default Stay) (#1429)."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)):
            scr = app.query_one(PickerScreen)
            quit_called = {"v": False}
            monkeypatch.setattr(app, "exit",
                                lambda *a, **k: quit_called.__setitem__("v", True))

            assert scr.quit_confirm is None
            scr.handle_key("escape")            # main view -> confirm, no exit
            assert scr.quit_confirm is not None
            assert quit_called["v"] is False

            scr.handle_key("escape")            # Esc in the confirm -> stay
            assert scr.quit_confirm is None
            assert quit_called["v"] is False

            scr.handle_key("q")                 # q also opens the confirm
            assert scr.quit_confirm is not None
            scr.handle_key("enter")             # default focus = Stay -> cancel
            assert scr.quit_confirm is None
            assert quit_called["v"] is False

            scr.handle_key("escape")            # open again
            scr.handle_key("y")                 # y -> quit
            assert quit_called["v"] is True

    asyncio.run(run())


def test_hidden_worktrees_filtered_and_toggle():
    """Bridge/system (kind=system) worktrees are hidden by default; Toggle-hidden
    reveals them, and the button appears only when there's something to reveal (#1422)."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")
    raws = [
        {"id": "lambda-core-win-aaaa", "title": "Real work", "status": "active",
         "started_at": "2026-06-27T17:00:00", "turn_count": 3, "state": "wip"},
        {"id": "lambda-core-win-ssss", "title": "daemon wt", "status": "active",
         "started_at": "2026-06-27T17:00:00", "turn_count": 0, "state": "wip",
         "kind": "system", "owner": "permanent-record"},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]

    recs = src.load()
    assert recs[0]["hidden"] is False
    assert recs[1]["hidden"] is True

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)):
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            # Default: the system worktree is hidden; the toggle button appears.
            ids = {r["id4"] for r in scr.list_records()}
            assert "aaaa" in ids and "ssss" not in ids
            assert scr._hidden_count() == 1
            assert "TH" in scr.button_set()
            # Activate the Toggle-hidden button (index resolved, not hardcoded --
            # Clean/Sync now share the row, #1427) -> reveal.
            scr.sel = ("BTN", 0)
            scr.btn_idx = scr.button_set().index("TH")
            scr._activate()
            assert scr.show_hidden is True
            ids = {r["id4"] for r in scr.list_records()}
            assert "ssss" in ids

    asyncio.run(run())


def test_toggle_hidden_button_absent_when_nothing_hidden():
    """With no bridge/system worktrees, the Toggle-hidden button stays off (#1422)."""
    src = _fixture_source()   # no kind=system rows

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)):
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            assert scr._hidden_count() == 0
            assert "TH" not in scr.button_set()

    asyncio.run(run())


def test_bridge_and_system_hidden_and_marked_distinctly():
    """Bridge and system worktrees are both hidden by default and marked
    distinctly in the title ([bridge] vs [system]) (#1424 tracking)."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")
    raws = [
        {"id": "lambda-core-win-aaaa", "title": "Real", "status": "active",
         "started_at": "2026-06-27T17:00:00", "turn_count": 3, "state": "wip"},
        {"id": "lambda-core-win-ssss", "title": "daemon", "status": "active",
         "started_at": "2026-06-27T17:00:00", "turn_count": 0, "state": "wip",
         "kind": "system", "owner": "permanent-record"},
        {"id": "lambda-core-win-bbbb", "title": "acp", "status": "active",
         "started_at": "2026-06-27T17:00:00", "turn_count": 0, "state": "wip",
         "kind": "bridge"},
    ]
    recs = {w["id"][-4:]: derive.norm(w, *local) for w in raws}
    assert recs["aaaa"]["hidden"] is False
    assert recs["ssss"]["hidden"] is True and recs["ssss"]["kind"] == "system"
    assert recs["bbbb"]["hidden"] is True and recs["bbbb"]["kind"] == "bridge"
    assert recs["ssss"]["title"].startswith("[system] ")
    assert recs["bbbb"]["title"].startswith("[bridge] ")

    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)):
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            ids = {r["id4"] for r in scr.list_records()}
            assert "aaaa" in ids and "ssss" not in ids and "bbbb" not in ids
            assert scr._hidden_count() == 2          # bridge + system
            scr.show_hidden = True
            ids = {r["id4"] for r in scr.list_records()}
            assert "ssss" in ids and "bbbb" in ids

    asyncio.run(run())


def test_banner_version_tracks_build_info(monkeypatch):
    """The picker banner version is derived from the real package version
    (``_build_info`` -> package metadata), never a hand-maintained literal.

    Regression for the banner that silently froze at ``1.5.3-dev69`` while the
    package shipped dev97: a stale constant must not be able to reappear.
    """
    from agent_worktrees import _build_info
    from agent_worktrees.picker_tui import engine

    monkeypatch.setitem(_build_info.BUILD_INFO, "version", "9.9.9-devTEST")
    assert engine._resolve_version() == "9.9.9-devTEST"


def test_banner_version_falls_back_when_build_info_blank(monkeypatch):
    """With no build-info version, fall back to installed metadata / ``dev`` --
    never the old frozen ``1.5.3-dev69`` literal."""
    from agent_worktrees import _build_info
    from agent_worktrees.picker_tui import engine

    monkeypatch.setattr(_build_info, "BUILD_INFO", {}, raising=False)
    v = engine._resolve_version()
    assert v and v != "1.5.3-dev69"


def test_run_detaches_console_stdin(monkeypatch):
    """``data_ssh._run`` must give its child an empty stdin, never the console.

    Regression for the picker freezing keyboard input until the SSH load
    fan-out exits: an ``ssh`` child that inherits the terminal stdin reads the
    operator's keystrokes out from under Textual's input reader (which reads
    the same console handle), so keys never reach the app until ssh dies.
    """
    import subprocess

    from agent_worktrees.picker_tui import data_ssh

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    data_ssh._run(["ssh", "host", "agent-worktrees list"], timeout=5)
    assert seen.get("stdin") is subprocess.DEVNULL


def test_live_loader_spawn_detaches_console_stdin(monkeypatch):
    """The killable prefetch runner (``LiveLoader._spawn``) must also detach
    stdin so a backgrounded ssh load can't swallow keyboard input."""
    import subprocess

    from agent_worktrees.picker_tui import data_ssh

    seen = {}

    class _FakeProc:
        returncode = 0

        def __init__(self, argv, **kwargs):
            seen.update(kwargs)

        def communicate(self, timeout=None):
            return ("{}", "")

        def poll(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", _FakeProc)
    loader = data_ssh.LiveLoader(sources=[])
    loader._spawn(["ssh", "host", "agent-worktrees list"], timeout=5)
    assert seen.get("stdin") is subprocess.DEVNULL


def test_maintenance_ssh_detaches_console_stdin(monkeypatch):
    """Remote Maintenance ops run while the TUI is up, so their ssh child must
    detach stdin too (same input-theft class as the load fan-out)."""
    import subprocess

    from agent_worktrees.picker_tui import maintenance

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    maintenance._ssh_json(["ssh", "host", "agent-worktrees sync"], timeout=5)
    assert seen.get("stdin") is subprocess.DEVNULL


def test_profiles_pivot_survives_empty_host_cols():
    """Arrowing through the Profiles pivot with **no** configured host columns
    must not crash (issue #149).

    ``_fixture_source`` exposes no ``host_cols`` hook, so the engine falls back
    to the empty ``_DEFAULT_HOST_COLS`` -- the exact state a machines.yaml with
    no native-terminal copilot host produces. Previously this raised
    ``IndexError`` in ``_visible_pcols`` (grid render), ``IndexError`` in the
    PR-zone footer hint, and ``ZeroDivisionError`` on Left/Right (``% len(
    host_cols)``)."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            assert scr.host_cols == []          # precondition: no host columns
            scr.htab = 2                          # Profiles pivot
            scr.sel = scr.default_sel()           # lands in the PR zone
            await pilot.pause()                   # render body + footer (no crash)
            for k in ("right", "left", "space", "down", "up", "right"):
                await pilot.press(k)
                await pilot.pause()
            # Reaching here without an exception is the assertion.

    asyncio.run(run())


def test_run_spawns_ssh_off_console_on_windows(monkeypatch):
    """``data_ssh._run`` must keep the ssh child off our console on Windows so a
    failing ssh can't clear the console VT-input mode and break arrows (#148)."""
    import subprocess

    from agent_worktrees.picker_tui import data_ssh

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(data_ssh.os, "name", "nt")
    monkeypatch.setattr(data_ssh, "_CREATE_NO_WINDOW", 0x08000000)
    monkeypatch.setattr(subprocess, "run", fake_run)
    data_ssh._run(["ssh", "host", "agent-worktrees list"], timeout=5)
    assert seen.get("creationflags", 0) & 0x08000000
    assert seen.get("stdin") is subprocess.DEVNULL


def test_run_no_creationflags_on_posix(monkeypatch):
    """On POSIX ``_run`` passes no Windows creationflags (would error)."""
    import subprocess

    from agent_worktrees.picker_tui import data_ssh

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(data_ssh.os, "name", "posix")
    monkeypatch.setattr(subprocess, "run", fake_run)
    data_ssh._run(["ssh", "host", "agent-worktrees list"], timeout=5)
    assert "creationflags" not in seen


def test_spawn_spawns_ssh_off_console_on_windows(monkeypatch):
    """``LiveLoader._spawn`` must add CREATE_NO_WINDOW on Windows (#148),
    alongside the existing CREATE_NEW_PROCESS_GROUP."""
    import subprocess

    from agent_worktrees.picker_tui import data_ssh

    seen = {}

    class _FakeProc:
        returncode = 0

        def __init__(self, argv, **kwargs):
            seen.update(kwargs)

        def communicate(self, timeout=None):
            return ("{}", "")

        def poll(self):
            return 0

    monkeypatch.setattr(data_ssh.os, "name", "nt")
    monkeypatch.setattr(data_ssh, "_CREATE_NO_WINDOW", 0x08000000)
    monkeypatch.setattr(subprocess, "Popen", _FakeProc)
    loader = data_ssh.LiveLoader(sources=[])
    loader._spawn(["ssh", "host", "agent-worktrees list"], timeout=5)
    assert seen.get("creationflags", 0) & 0x08000000
    assert seen.get("stdin") is subprocess.DEVNULL


def test_maintenance_ssh_off_console_on_windows(monkeypatch):
    """Remote Maintenance ssh ops must also run off our console on Windows."""
    import subprocess

    from agent_worktrees.picker_tui import maintenance

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(maintenance.os, "name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)
    maintenance._ssh_json(["ssh", "host", "agent-worktrees sync"], timeout=5)
    assert seen.get("creationflags", 0) & 0x08000000
    assert seen.get("stdin") is subprocess.DEVNULL


# --- Registered (cross-plugin) pivot: TASKS ---------------------------------

def _write_tasks_manifest(directory):
    import json
    manifest = {
        "label": "Tasks",
        "after": "Worktrees",
        "list": ["true"],
        "entry": {
            "id": "id", "title": "title", "worktree": "target_worktree",
            "subtitle": "repo_name", "badges": ["labels"],
        },
        "empty_hint": "No proposed tasks.",
        "actions": [
            {"key": "open", "label": "Open into a CLI session", "run": ["echo", "{id}"]},
            {"key": "abandon", "label": "Abandon", "run": ["echo", "{task_id}"],
             "confirm": True},
        ],
    }
    (directory / "agent-dispatch.json").write_text(json.dumps(manifest), encoding="utf-8")


class _FakeRuntime:
    def __init__(self, rows):
        self.rows = rows
        self.actions = []
        self.invalidated = False

    def ensure(self, machine):
        pass

    def get(self, machine):
        return ("ready", self.rows, "")

    def invalidate(self, machine=None):
        self.invalidated = True

    def run_action(self, action, ctx):
        self.actions.append((action.key, dict(ctx)))
        return (True, "done")


def _seed_fake_tasks(scr, rows):
    reg = scr.registered_pivots[0]
    rt = _FakeRuntime(rows)
    scr._pivot_runtimes[reg.name] = rt
    return rt


def test_registered_pivot_inserted_between_worktrees_and_maintenance(tmp_path, monkeypatch):
    from agent_worktrees.picker_tui import pivots as pivots_mod

    d = tmp_path / "pivots"
    d.mkdir()
    _write_tasks_manifest(d)
    monkeypatch.setenv(pivots_mod.PIVOTS_DIR_ENV, str(d))

    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            assert scr.htabs == ["Worktrees", "Tasks", "Maintenance", "Profiles"]
            assert scr._kind(1) == "registered"
            assert scr._kind(2) == "maintenance"     # built-ins shifted, not renumbered logic
            assert scr._kind(3) == "profiles"

    asyncio.run(run())


def test_registered_pivot_lists_and_navigates(tmp_path, monkeypatch):
    from agent_worktrees.picker_tui import pivots as pivots_mod

    d = tmp_path / "pivots"
    d.mkdir()
    _write_tasks_manifest(d)
    monkeypatch.setenv(pivots_mod.PIVOTS_DIR_ENV, str(d))

    rows = [
        {"id": "t1", "title": "First task", "target_worktree": "wt-a",
         "repo_name": "repoA", "labels": ["handoff"]},
        {"id": "t2", "title": "Second task", "target_worktree": None,
         "repo_name": "repoB", "labels": []},
    ]
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            _seed_fake_tasks(scr, rows)
            scr.htab = scr.htabs.index("Tasks")
            scr.sel = scr.default_sel()
            await pilot.pause()

            assert scr._task_rows() == rows
            # One ('T', i) stop per task; machine sub-nav ('M') present.
            zones = [z for z, _ in scr.stops()]
            assert zones.count("T") == 2
            assert ("M", 0) in scr.stops()
            # Grouped by worktree.
            groups = dict((g, [i for i, _ in items]) for g, items in scr._task_groups())
            assert "wt-a" in groups
            # Body renders the task titles.
            plain = scr.render().plain
            assert "First task" in plain
            assert "Second task" in plain

    asyncio.run(run())


def test_registered_pivot_action_menu_runs_and_invalidates(tmp_path, monkeypatch):
    from agent_worktrees.picker_tui import pivots as pivots_mod

    d = tmp_path / "pivots"
    d.mkdir()
    _write_tasks_manifest(d)
    monkeypatch.setenv(pivots_mod.PIVOTS_DIR_ENV, str(d))

    rows = [{"id": "t1", "title": "First task", "target_worktree": "wt-a",
             "repo_name": "repoA", "labels": ["handoff"]}]
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            rt = _seed_fake_tasks(scr, rows)
            scr.htab = scr.htabs.index("Tasks")
            # Focus the first task row.
            scr.sel = ("T", 0)
            await pilot.pause()

            # Enter opens the action sub-menu with the manifest's actions.
            scr._open_task_menu()
            assert scr.task_menu is not None
            assert [a.label for a in scr.task_menu["actions"]] == [
                "Open into a CLI session", "Abandon"]

            # Select "Abandon" and run it.
            scr.task_menu_idx = 1
            scr._key_task_menu("enter")
            assert scr.task_menu is None
            assert rt.actions and rt.actions[0][0] == "abandon"
            # Placeholders resolved: {task_id} -> the entry id.
            _key, ctx = rt.actions[0]
            assert ctx["task_id"] == "t1"
            assert ctx["machine"] == "lambda-core"
            assert rt.invalidated is True

    asyncio.run(run())


def test_registered_pivot_switch_pivot_cycles_left_rail(tmp_path, monkeypatch):
    from agent_worktrees.picker_tui import pivots as pivots_mod

    d = tmp_path / "pivots"
    d.mkdir()
    _write_tasks_manifest(d)
    monkeypatch.setenv(pivots_mod.PIVOTS_DIR_ENV, str(d))

    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            # Profiles is under ⚙ Configuration and Maintenance is eliminated
            # (a hidden anchor, #1427), so only Worktrees + the registered pivot
            # ride the left rail.
            left = scr._left_pivots()
            assert len(left) == 2
            kinds = []
            for _ in range(len(left)):
                kinds.append(scr._kind())
                scr._switch_pivot(1)
            assert kinds == ["worktrees", "registered"]
            assert scr.htab == 0                    # wrapped back to Worktrees
            # The left cycle never lands on the config-hosted or hidden pivots.
            seen = set()
            for _ in range(6):
                seen.add(scr._kind())
                scr._switch_pivot(1)
            assert "profiles" not in seen
            assert "maintenance" not in seen

    asyncio.run(run())
