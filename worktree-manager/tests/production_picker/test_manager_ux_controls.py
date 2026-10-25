"""Source-only UX contracts; no live lifecycle or configuration mutations."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import OptionList

from test_picker_tui import _fixture_source, _profiles_source
from worktree_manager.production_picker import context
from worktree_manager.production_picker.picker_tui import pivots
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.engine_runtime import PickerScreenRuntimeMixin
from worktree_manager.production_picker.picker_tui.orphanage import OrphanageScreen
from worktree_manager.production_picker.picker_tui.seed_prompt_field import SeedPromptField
from worktree_manager.production_picker.picker_tui.task_category_filter import combine_task_categories
from worktree_manager.production_picker.picker_tui.worktree_details_screen import WtDetailsScreen


def contribution(category):
    name = "agent-dispatch" + ("-maintenance" if category == "maintenance" else "")
    path = Path(__file__).resolve().parents[3] / "plugins" / "agent-dispatch" / "pivots" / f"{name}.json"
    return {
        "label": category, "kind": "registered",
        "pivot": pivots.parse_manifest(json.loads(path.read_text()), name=name, source_path=str(path)),
    }


def test_task_categories_do_not_reinterpret_another_attributed_provider():
    descriptor = contribution("maintenance")
    descriptor["pivot"] = replace(descriptor["pivot"], source_owner="other@example")
    assert combine_task_categories([descriptor]) == [descriptor]


@pytest.mark.parametrize("categories", [("work", "maintenance"), ("maintenance", "work"), ("maintenance",)])
@pytest.mark.parametrize("scope", [0, 1, 2])
def test_tasks_one_provider_filter_for_machine_and_all_scopes(categories, scope):
    descriptors = combine_task_categories([contribution(category) for category in categories])
    assert len(descriptors) == 1 and descriptors[0]["label"] == "Tasks"
    reg = descriptors[0]["pivot"]
    assert reg.list_cmd[reg.list_cmd.index("--category") + 1] == "all"
    assert "--limit-per-category" in reg.list_cmd
    screen = PickerScreen(_fixture_source(), live=False)
    screen.setup_sync_for_tests()
    screen.pivots = descriptors
    screen.htab, screen.machine_idx = 0, scope
    if scope >= len(screen.machines):
        screen.machines.append(("Fixture Win", f"machine-{scope}", "Win", True))
        screen.source_tabs.append({"machine": f"machine-{scope}", "source_kind": "machine-ssh"})
    rows = [{"id": "work", "category": "work"}, {"id": "maintenance", "category": "maintenance"}]
    requests = []
    runtime = SimpleNamespace(
        ensure=lambda machine: requests.append(machine),
        get=lambda machine: ("ready", rows, ""),
        get_summary=lambda machine: {},
    )
    screen._pivot_machine_id = lambda: f"machine-{scope}"
    screen._machine_key_map = lambda: {}
    screen._pivot_runtime = lambda pivot: runtime
    assert "TM" in screen.button_set()
    assert screen._task_state()[0] == "ready"
    assert [row["id"] for row in screen._task_state()[1]] == ["work"]
    assert requests
    screen.btn_idx = screen.button_set().index("TM")
    screen.sel = ("BTN", 0)
    screen._activate()
    assert screen.include_maintenance
    assert [row["id"] for row in screen._task_state()[1]] == ["work", "maintenance"]
    screen._activate()
    assert not screen.include_maintenance


@pytest.mark.parametrize("manager,payload", [("available", "available"), ("available", "checking"),
                                           ("current", "available"), ("idle", "available")])
def test_one_update_affordance_preserves_update_owner(manager, payload):
    screen = PickerScreen(_fixture_source(), live=False)
    screen.setup_sync_for_tests()
    screen.manager_update_state, screen.update_state = manager, payload
    text = "".join(line.plain for line in screen.topbar(160))
    assert text.count("Update available") == 1
    assert text.count("↻") == 1
    assert [stop for stop in screen._v_stops() if stop[0] in {"MUP", "UPD"}] == [
        ("MUP" if manager == "available" else "UPD", 0),
    ]


def test_configuration_highlight_activates_view_and_escape_returns_without_apply(monkeypatch):
    monkeypatch.setattr(PickerScreen, "_tick", lambda self: None)
    async def run():
        app = PickerApp(_profiles_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(PickerScreen)
            applied = []
            screen._apply_profiles = lambda *args: applied.append(args)
            screen.pivots.insert(0, {"kind": "worktrees", "label": "Other"})
            start = screen._left_pivots()[-1]
            screen.htab, screen.sel = start, ("V", 0)
            screen._nav_view_row(1)
            assert screen._in_configuration() and screen.sel == ("CFG", 0)
            screen._dispatch_key("escape")
            assert screen.htab == start and not screen._in_configuration()
            assert screen.sel == ("V", 0)
            assert not applied
    asyncio.run(run())


def test_actions_resume_inline_prompt_and_options_reach_origin_callback(monkeypatch, tmp_path):
    from worktree_manager.seed_history import SeedHistory

    monkeypatch.setattr(context, "_project", "example")
    monkeypatch.setattr(context, "_bootstrap", None)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    monkeypatch.setattr(PickerScreen, "_tick", lambda self: None)
    remembered = []
    monkeypatch.setattr(SeedHistory, "remember", lambda self, text: (
        remembered.append(text) or True
    ))
    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 45)) as pilot:
            await pilot.pause()
            screen = app.query_one(PickerScreen)
            rec = dict(screen.list_records()[0], mux_live=False, sessionless=False, is_local=True)
            decisions = []
            screen._decide = decisions.append
            menu = screen._push_wt_submenu(rec)
            await pilot.pause()
            assert "Bare resume" not in menu._actions
            assert "Resume prompt…" not in menu._actions
            prompt = menu.query_one(SeedPromptField)
            prompt.text = "inspect the current diff"
            prompt.focus()
            await pilot.pause()
            menu._no_mux = True
            menu.refresh_actions(list(menu._actions), status="verified")
            assert prompt.text == "inspect the current diff"
            assert menu.focused is prompt
            assert menu._no_mux
            options = menu.query_one("#sub-list", OptionList)
            options.highlighted = menu._actions.index("Resume")
            options.focus()
            await pilot.press("enter")
            async with asyncio.timeout(10):
                while not decisions:
                    await pilot.pause(.01)
            assert app.screen is not menu
            assert decisions[0]["options"] == {"no_mux": True, "seed_prompt": prompt.text}
            assert remembered[0] == prompt.text
    asyncio.run(run())


def test_retained_selection_cursor_style_depends_on_actual_focus(monkeypatch):
    monkeypatch.setattr(PickerScreen, "_tick", lambda self: None)
    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(PickerScreen)
            native = screen.query_one("#nf-body-data", OptionList)
            screen.sel = ("L", 0)
            screen._wt_track_focus()
            screen._sync_focus_to_sel()
            await pilot.pause()
            selected = set(screen.wt_sel.ids)
            assert selected
            active = native.get_component_rich_style("option-list--option-highlighted")
            screen.sel = ("BTN", 0)
            screen.btn_idx = screen.button_set().index("K")
            screen._sync_focus_to_sel()
            await pilot.pause()
            passive = native.get_component_rich_style("option-list--option-highlighted")
            assert selected == set(screen.wt_sel.ids)
            assert active.bgcolor != passive.bgcolor
            assert not native.has_focus
    asyncio.run(run())


def test_filtered_live_publication_keeps_query_and_source_qualified_action_target(monkeypatch):
    monkeypatch.setattr(PickerScreen, "_tick", lambda self: None)
    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(PickerScreen)
            screen.machine_idx = 0
            target = next(row for row in screen.data if row["id4"] == "cccc")
            rows = [dict(target)]
            screen.data = rows
            screen.list_view.query = "cccc"
            screen.sel = ("L", 0)
            screen._wt_track_focus()
            identity = screen._row_key(target)
            screen.refresh()
            await pilot.pause()
            # A newly admitted live row must remain visible by the existing
            # must-show contract, without stealing the filtered target's focus.
            live = next(row for row in _fixture_source().load() if row["id4"] == "aaaa")
            screen.loader = SimpleNamespace(
                records=lambda: [live, dict(target, title="Refreshed completed task")],
                counts=lambda: (1, 0, 0),
                state=lambda machine, env: "ready",
            )
            screen.live = True
            screen._maybe_repoll = screen._maybe_repoll_pivot = lambda: None
            screen._maybe_refresh_worker_pivots = lambda: None
            screen._nav_dirty = True
            PickerScreenRuntimeMixin._tick(screen)
            assert screen.list_view.query == "cccc"
            assert screen._wt_focused_id() == identity
            assert screen._row_key(screen._submenu_target()) == identity
            screen.refresh()
            await pilot.pause()
            screen._sync_focus_to_sel()
            await pilot.press("down")
            await pilot.pause()
            assert screen.list_view.query == "cccc"
            assert screen._row_key(screen._submenu_target()) == identity
            screen.live = False
            screen.loader = None
    asyncio.run(run())


@pytest.mark.parametrize("options", [{"ahp": True}, {"execution_leg": {"state": "unknown"}}])
def test_inline_prompt_refusal_and_escape_never_dispatch(options, monkeypatch):
    monkeypatch.setattr(PickerScreen, "_tick", lambda self: None)
    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 45)) as pilot:
            await pilot.pause()
            screen = app.query_one(PickerScreen)
            screen.real_ops = False
            rec = dict(screen.list_records()[0], mux_live=False, sessionless=False, is_local=True)
            if "execution_leg" in options:
                rec["execution_leg"] = options["execution_leg"]
            decisions = []
            screen._decide = decisions.append
            menu = screen._push_wt_submenu(rec)
            await pilot.pause()
            menu._ahp = bool(options.get("ahp"))
            menu.query_one(SeedPromptField).text = "must remain a draft"
            actions = menu.query_one("#sub-list", OptionList)
            actions.highlighted = menu._actions.index("Resume")
            actions.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is menu and not decisions
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is not menu and not decisions
    asyncio.run(run())


def test_claim_warnings_qualify_scope_freshness_and_read_only_inspection():
    orphan = OrphanageScreen([{"kind": "worktree", "ref": "example"}])._body().plain
    assert "local claims journal" in orphan and "Freshness" in orphan
    assert "not confirmed stopped or absent" in orphan
    assert "retained obligations, not ORPHAN worktree states or failed machines" in orphan
    assert "handoff/custody remains in force" in orphan
    detail = WtDetailsScreen({"claims_summary": "one claim", "asset_hints": {"held_count": 1}})
    text = detail._body().plain
    assert "last loaded projection" in text and "Read-only" in text
    assert "Unreachable does not mean stopped or absent" in text
