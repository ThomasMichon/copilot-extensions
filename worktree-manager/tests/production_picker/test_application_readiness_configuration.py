"""Production Configuration consumes a contained native repair owner."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

from agent_procutil import no_window_kwargs
import pytest
import yaml

from test_config_actions import _wait
from test_picker_tui import _fixture_source, _wait_for_initial_setup
from worktree_manager.pivot_progress import PivotProgressScreen
from worktree_manager.production_picker import context
from worktree_manager.production_picker.picker_tui import pivots
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.engine_action_confirm import ActionConfirmScreen

pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.contract("worktree_manager.application_readiness_configuration"),
]


@pytest.mark.timeout(120)
def test_native_readiness_navigation_cancel_confirm_repair_and_refresh(tmp_path, monkeypatch):
    owner_python = os.environ.get("APPLICATION_READINESS_TEST_PYTHON")
    if not owner_python:
        pytest.skip("select the contained Machines test interpreter for native owner consumption")
    root = Path(__file__).resolve().parents[3]
    preparation = subprocess.run(
        [owner_python, "-c",
         "import importlib.util,json,sys\nfrom pathlib import Path\n"
         "path=Path(sys.argv[1])/'plugins'/'agent-machines'/'tests'/'test_installation_repair.py'\n"
         "spec=importlib.util.spec_from_file_location('repair_fixture_support',path)\n"
         "module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)\n"
         "fixture=module.repair_fixture.__wrapped__(Path(sys.argv[2]))\n"
         "print(json.dumps([str(value) for value in fixture[:3]]))\n",
         str(root), str(tmp_path)],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert preparation.returncode == 0, preparation.stderr
    home, project, payload = map(Path, json.loads(preparation.stdout))
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(key, str(home))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(context, "_project", "sample")
    monkeypatch.setattr(context, "_bootstrap", None)
    registry = home / ".agent-worktrees" / "repos.yaml"
    registry.parent.mkdir()
    registry.write_text(yaml.safe_dump({"repos": {"sample": {
        "class": "worktree", "windows" if os.name == "nt" else "linux": str(project),
    }}}), encoding="utf-8")
    protected = {path: path.read_bytes() for path in (
        registry, project / ".github" / "copilot" / "settings.json",
        payload / "installer-readiness.json",
    )}
    trace = tmp_path / "calls.jsonl"
    bridge = tmp_path / "native-owner.py"
    bridge.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        f"with Path({str(trace)!r}).open('a',encoding='utf-8') as stream:\n"
        " stream.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "from agent_machines.__main__ import main\nraise SystemExit(main(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    prefix = [owner_python, str(bridge)]
    raw = json.loads((root / "plugins" / "agent-machines" / "pivots" / "application-readiness.json").read_text(
        encoding="utf-8",
    ))
    raw["list"] = [*prefix, *raw["list"][1:]]
    for action in raw["view_actions"]:
        action["run"] = [*prefix, *action["run"][1:]]
    reg = replace(pivots.parse_manifest(raw, name="application-readiness", source_path="fixture"),
                  source_owner="agent-machines@fixture")
    monkeypatch.setattr(PickerScreen, "_scan_pivot_payload", staticmethod(lambda: (
        [reg], [{"label": "Worktrees", "kind": "worktrees", "pivot": None},
                {"label": reg.label, "kind": "registered", "pivot": reg}], [], [],
    )))

    def calls():
        return [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            screen = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, screen)
            assert calls() == []
            screen._open_cfgmenu()
            for _ in range(len(screen.pivots) + 1):
                if screen._reg_pivot() is reg:
                    break
                screen._rotate_config_section(1)
                await pilot.pause()
            await _wait(pilot, lambda: screen._task_summary().get("repairable") == "true"
                        or screen._task_state()[0] == "error")
            assert screen._task_state()[0] == "ready", screen._task_state()
            assert len(screen._task_rows()) == 2
            assert {row["state"] for row in screen._task_rows()} == {"not-ready", "blocked"}
            assert not list(home.glob("*.ready"))
            assert all(path.read_bytes() == value for path, value in protected.items())

            def activate():
                screen.sel = ("BTN", 0)
                screen.btn_idx = screen.button_set().index("VA:0")
                screen._activate()

            activate()
            await _wait(pilot, lambda: isinstance(app.screen, ActionConfirmScreen))
            await pilot.press("enter")
            await pilot.pause()
            assert not list(home.glob("*.ready")) and all("--apply" not in call for call in calls())
            activate()
            await _wait(pilot, lambda: isinstance(app.screen, ActionConfirmScreen))
            await pilot.press("left", "enter")
            await _wait(pilot, lambda: isinstance(app.screen, PivotProgressScreen) and app.screen.finished)
            assert len(list(home.glob("*.ready"))) == 2
            app.screen.action_cancel()
            await _wait(pilot, lambda: screen._task_summary().get("required_satisfied") is True
                        or screen._task_state()[0] == "error")
            assert screen._task_state()[0] == "ready", screen._task_state()
            assert all(row["state"] == "ready" for row in screen._task_rows())
            assert screen._pivot_view_actions() == []
            assert sum("--apply" in call for call in calls()) == 1
            assert all(path.read_bytes() == value for path, value in protected.items())

    asyncio.run(run())
