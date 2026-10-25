"""Identifier-neutral Configuration walkthrough through the production compositor."""

import asyncio
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import patch

from worktree_manager.production_picker.picker_tui import capture, pivots, tasks
from worktree_manager.production_picker.picker_tui.configuration_form import (
    ConfigurationResultScreen,
)
from worktree_manager.production_picker.picker_tui.engine import PickerScreen

SCENARIOS = (
    "profiles", "profiles-confirm", "loading", "catalog", "actions", "details",
    "instantiate", "declaration", "edit", "confirm", "receipt", "error", "compact",
)
ROOT = Path(__file__).resolve().parents[3]


def _catalog():
    # Reuse provider-owned scaffolds, fields, and effects, never operator state.
    import sys

    sys.path.insert(0, str(ROOT / "plugins" / "agent-dispatch" / "src"))
    from agent_dispatch.workers_catalog import (
        TEMPLATE_DESCRIPTIONS, _cards, _fields, _template_defaults, templates,
    )

    entries = []
    for name, fragment in templates().items():
        entries.append(_cards({
            "id": f"template:{name}", "title": name, "group": "Available templates",
            "description": TEMPLATE_DESCRIPTIONS[name],
            "provenance": "built-in recipe", "validity": "template",
            "enabled": "not-instantiated", "activity": "not-applicable",
            "editable": "false", "instantiable": "true", "revision": "fixture",
            "fields": _fields("", _template_defaults(fragment)),
        }, "Supply a unique local name and a complete declaration. "
           "Required dependencies must resolve before saving. Source remains unchanged."))
    document = copy.deepcopy(templates()["worker-pool"])
    document["description"] = "One lane for queued documentation tasks"
    instance = _cards({
        "id": "instance:docs-worker", "title": "docs-worker", "group": "Configured",
        "description": document["description"], "provenance": "template:worker-pool",
        "validity": "valid", "enabled": "enabled", "activity": "unknown",
        "lanes": 1, "editable": "true", "instantiable": "false",
        "revision": "fixture", "publication": "published",
        "fields": _fields("docs-worker", document),
    }, "User-local instance; published configuration, observed activity unknown.\n"
       "Statistics: 1 declaration / 1 lane. No fresh supervisor receipt.")
    source = _cards({
        "id": "source:plugin-worker", "title": "plugin-worker",
        "group": "Source declarations", "description": "Plugin-owned pool",
        "provenance": "example-plugin", "validity": "valid", "enabled": "disabled",
        "activity": "unknown", "lanes": 2, "editable": "false",
        "instantiable": "false", "pool_toggleable": "true",
        "pool_name": "plugin-worker", "owner": "example-plugin",
    }, "Read-only plugin declaration; disabled by a local override. "
       "Edit through its owning plugin, not this surface.")
    return [instance, source, *entries], {
        "configured": 1, "source_declarations": 1, "templates": len(entries),
    }


async def _wait(pilot, condition):
    for _ in range(200):
        await pilot.pause()
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Configuration snapshot did not reach its requested state")


def capture_configuration(source, scenario):
    """Capture UI-only fixtures; no real provider command or configuration write."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown Configuration scenario: {scenario}")
    rows, summary = _catalog()
    release = threading.Event()

    def read(runtime, context):
        if scenario == "loading":
            if not release.wait(15):
                raise AssertionError("Snapshot did not release the blocked provider")
        if scenario == "error":
            return "error", [], "Fixture: provider unavailable; retry after repair", {}
        return "ready", copy.deepcopy(rows), "", summary

    def read_stream(runtime, machine, context):
        state, entries, error, details = read(runtime, context)
        runtime._finish(machine, (state, entries, error), details)

    async def open_state(scr, pilot):
        scr._update_state_pinned = scr._manager_update_state_pinned = True
        scr.update_state = scr.manager_update_state = "current"
        scr._open_cfgmenu()
        await pilot.pause()
        if scenario.startswith("profiles"):
            assert scr._kind() == "profiles"
            if scenario == "profiles-confirm":
                scr.grid[(2, 0)] = True
                scr._apply_profiles()
                await pilot.pause()
                assert "Apply" in capture.screen_to_text(scr.app.screen)
                assert scr.app.screen is not scr
            return
        while scr.htabs[scr.htab] != "Workers":
            scr._rotate_machine(1)
            await pilot.pause()
        if scenario == "loading":
            assert scr._task_state()[0] == "loading"
            return
        await _wait(pilot, lambda: scr._task_state()[0] in ("ready", "error"))
        if scenario in ("catalog", "compact", "error"):
            return
        reg = scr._reg_pivot()
        row = next(r for r in scr._task_rows() if r["id"] == (
            "instance:docs-worker" if scenario == "edit" else "template:worker-pool"
        ))
        scr.sel = ("T", scr._task_rows().index(row))
        if scenario == "actions":
            scr._open_task_menu()
            await pilot.pause()
            assert "Instantiate" in capture.screen_to_text(scr.app.screen)
            return
        if scenario == "details":
            action = next(a for a in reg.actions if a.key == "details")
            scr._open_pivot_card(reg, action, row)
            await pilot.pause()
            return
        action = next(a for a in reg.actions if a.key == (
            "edit-save" if scenario == "edit" else "instantiate-activate"
        ))
        scr._open_pivot_form(
            reg, action, row, scr._task_action_ctx(reg, row), row["title"],
        )
        await pilot.pause()
        editor = scr.app.screen
        if scenario != "edit":
            editor._q[0]["primary"].value = "docs-worker-new"
        if scenario == "declaration":
            await pilot.press("ctrl+right")
            await pilot.pause()
        if scenario in ("instantiate", "declaration", "edit"):
            return
        editor._show_confirm_prompt()
        await pilot.pause()
        if scenario == "confirm":
            return
        await pilot.press("left", "enter")
        await _wait(pilot, lambda: isinstance(scr.app.screen, ConfigurationResultScreen)
                    and not scr.app.screen.pending)
        assert "pending" in capture.screen_to_text(scr.app.screen)

    manifest = ROOT / "plugins" / "agent-dispatch" / "pivots" / "workers-config.json"
    registered = pivots.parse_manifest(
        json.loads(manifest.read_text(encoding="utf-8")),
        name="workers", source_path=str(manifest),
    )
    report = SimpleNamespace(
        pivots=[registered], worktree_actions=[], config_sections=[], findings=(),
    )
    source = copy.copy(source)
    source.host_cols = lambda: [("Demo host", *source.LOCAL)]
    source.target_envs = lambda: [source.LOCAL, ("example-remote", "Linux")]

    def refuse_apply(*args):
        raise AssertionError("Profile snapshots must never apply a configuration")

    source.apply_profile_column = refuse_apply
    with ExitStack() as stack:
        stack.enter_context(patch.object(pivots, "scan_pivot_registry", return_value=report))
        for method in ("_poll_update_state", "_poll_manager_update_state", "_prewarm_machine_key_map"):
            stack.enter_context(patch.object(PickerScreen, method, return_value=None))
        stack.enter_context(patch.object(tasks.RegisteredPivotRuntime, "_exec_list", read))
        stack.enter_context(patch.object(tasks.RegisteredPivotRuntime, "_run_list_stream", read_stream))
        stack.enter_context(patch.object(
            tasks.RegisteredPivotRuntime, "run_resolved",
            return_value=(True, json.dumps({
                "id": "instance:docs-worker-new", "persisted": True,
                "activation": "pending",
                "message": "Published; existing supervisor reconciliation is pending.",
                "effects": "Workers may bind queued tasks; publication is not running.",
            }, indent=2)),
        ))
        try:
            return capture.capture_modal(
                source, open_state, size=(80, 24) if scenario == "compact" else (118, 40),
                title=f"Configuration - {scenario}",
            )
        finally:
            release.set()
