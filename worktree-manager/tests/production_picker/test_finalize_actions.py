"""Safe Finalize stays discoverable without retiring protected work."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from worktree_manager.production_picker.picker_tui import maintenance
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.engine_live_screens import ProgressScreen
from test_picker_tui import _maint_source


@pytest.mark.parametrize("values", [
    {"cleanup_bucket": "clean"},
    {"cleanup_bucket": "dirty", "dirty": 2},
    {"cleanup_bucket": "active", "ahead": 1},
    {"cleanup_bucket": "active", "held_claims": 1},
    {"cleanup_bucket": "active", "mux_live": True},
    {"cleanup_bucket": "active", "mux_observation_unknown": True},
])
def test_finalize_is_discoverable_outside_the_empty_worktree_buckets(values):
    row = {"sessionless": False, **values}
    assert "Finalize" in PickerScreen._session_action_verbs(row)
    calls = []
    screen = SimpleNamespace(
        _record_supports=PickerScreen._record_supports,
        _run_op_progress=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    PickerScreen._start_finalize(screen, row)
    assert calls == [(("Finalize", "finalize", [row]), {"armed": False})]


@pytest.mark.parametrize("values", [
    {"managed_child": True},
    {"mux_live": True},
    {"mux_observation_unknown": True},
    {"session_bound_live": True},
    {"session_ahp_live": True},
    {"execution_leg_live": True},
    {"session_lock_stale": True},
])
def test_protected_finalize_never_invokes_an_engine(values, monkeypatch):
    monkeypatch.setattr(maintenance.engine_client, "run_json", lambda *_a, **_kw: pytest.fail("engine effect"))
    row = {"raw": {"id": "fixture-wt"}, "machine": "fixture", "env": "Win", **values}
    tasks = maintenance.build_tasks("finalize", [row], None, project="fixture", local=("fixture", "Win"))
    result = tasks[0][1]()
    assert not maintenance._result_ok("finalize", result)
    assert result.get("reason")


@pytest.mark.parametrize("remote", [False, True])
def test_finalize_retains_the_real_process_refusal_output(remote, monkeypatch):
    from worktree_manager import engine_client

    detail = "Unmerged work remains.\nHeld claim: workdir:fixture-child\nUse claims show before cleanup."
    script = (
        "import json,sys;"
        f"print({detail!r},file=sys.stderr);"
        "print(json.dumps({'success':False,'status':'active'}));sys.exit(1)"
    )
    command = [sys.executable, "-c", script]
    if remote:
        from worktree_manager.production_picker.picker_tui import data_ssh
        monkeypatch.setattr(data_ssh, "remote_op_argv", lambda *_a, **_kw: command)
    else:
        monkeypatch.setattr(engine_client, "engine_base_command", lambda: command)
    result = maintenance._make_task(
        "finalize", "fixture-wt", "fixture", "Win", not remote, project="fixture",
    )()
    assert result["success"] is False
    assert detail in result["detail"]
    assert not maintenance._result_ok("finalize", result)


@pytest.mark.parametrize("remote,stdout,exit_code", [
    (False, "engine refused before JSON", 1),
    (False, '{"unfinished":', 0),
    (True, "remote refused before JSON", 1),
    (True, '{"unfinished":', 0),
])
def test_finalize_retains_both_streams_when_json_is_unusable(remote, stdout, exit_code, monkeypatch):
    from worktree_manager import engine_client

    stderr = "original refusal context\nfinal diagnostic line"
    script = (
        "import sys;"
        f"print({stdout!r});print({stderr!r},file=sys.stderr);sys.exit({exit_code})"
    )
    command = [sys.executable, "-c", script]
    if remote:
        from worktree_manager.production_picker.picker_tui import data_ssh
        monkeypatch.setattr(data_ssh, "remote_op_argv", lambda *_a, **_kw: command)
    else:
        monkeypatch.setattr(engine_client, "engine_base_command", lambda: command)
    result = maintenance._make_task(
        "finalize", "fixture-wt", "fixture", "Win", not remote, project="fixture",
    )()
    assert not maintenance._result_ok("finalize", result)
    assert stdout in result["detail"] and stderr in result["detail"]


@pytest.mark.parametrize("with_reason", [True, False])
def test_actual_finalize_confirmation_refusal_and_full_details(monkeypatch, with_reason):
    from worktree_manager.production_picker.picker_tui.configuration_form import CommandResultScreen

    calls = []
    detail = (
        "Checking worktree fixture-wt...\n"
        "  \x1b[31m✗\x1b[0m Worktree fixture-wt still owns 80 unsettled resource obligations:\n"
        + "\n".join(f"Held claim: workdir:fixture-child-{index}" for index in range(80))
        + "\nClose each resource out, then retry -- or pass --abandon to re-home them."
    )

    def run_json(_project, argv, **_kwargs):
        if argv[0] != "finalize":
            return {}
        calls.append(argv)
        return {
            "success": False, "detail": detail,
            **({"reason": "unsettled obligations"} if with_reason else {}),
        }

    monkeypatch.setattr(maintenance.engine_client, "run_json", run_json)
    monkeypatch.setattr(maintenance.context, "project", lambda: "fixture")

    async def run():
        app = PickerApp(_maint_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            screen = app.query_one(PickerScreen)
            screen.machine_idx = screen.local_index()
            await pilot.pause()
            row = next(row for row in screen.list_records() if row["cleanup_bucket"] == "clean")
            screen._start_finalize(row)
            await pilot.pause()
            assert isinstance(app.screen, ProgressScreen)
            assert not calls and not screen.progress["armed"]
            await pilot.press("escape")
            await pilot.pause()
            assert not calls and screen.progress is None
            screen._start_finalize(row)
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(30):
                await pilot.pause(0.05)
                if screen.progress["done"]:
                    break
            assert screen.progress["done"]
            assert len(calls) == 1
            assert calls[0] == ["finalize", row["raw"]["id"], "--json"]
            text = app.screen._panel().renderable.plain
            assert ("unsettled obligations" if with_reason else "still owns 80 unsettled") in text
            assert "--abandon" not in text
            assert "help clean up worktree" in text
            await pilot.press("v")
            await pilot.pause()
            assert isinstance(app.screen, CommandResultScreen)
            result = str(app.screen.query_one("#command-result").render())
            assert "fixture-child-0" in result and "fixture-child-79" in result
            viewer = app.screen.query_one("VerticalScroll")
            viewer.focus()
            await pilot.press("end")
            await pilot.pause()
            assert viewer.scroll_y > 0
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ProgressScreen)
            assert len(calls) == 1

    asyncio.run(run())


def test_bulk_finalize_is_available_without_any_empty_worktree_bucket():
    async def run():
        app = PickerApp(_maint_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            screen = app.query_one(PickerScreen)
            screen.machine_idx = screen.local_index()
            await pilot.pause()
            rows = [
                next(row for row in screen.list_records() if row["cleanup_bucket"] == bucket)
                for bucket in ("clean", "active")
            ]
            screen.wt_sel.replace({screen._row_key(row) for row in rows})
            screen._open_wt_action_menu()
            await pilot.pause()
            menu = app.screen
            assert "Finalize" in menu._actions
            for _ in range(menu._actions.index("Finalize")):
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ProgressScreen)
            assert screen.progress["recs"] == rows
            assert not screen.progress["armed"]
            await pilot.press("escape")
            await pilot.pause()
            assert screen.progress is None

    asyncio.run(run())
