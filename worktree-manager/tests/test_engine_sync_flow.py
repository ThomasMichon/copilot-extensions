"""Source-only real paired Sync through the Manager's native progress screen."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import textwrap
import threading
import time
from types import SimpleNamespace

import pytest
from textual.app import App

from worktree_manager import engine_client, engine_sync
from worktree_manager.production_picker.picker_tui import maintenance
from worktree_manager.production_picker.picker_tui.engine_input import PickerScreenInputMixin
from worktree_manager.production_picker.picker_tui.engine_live_screens import ProgressScreen
from worktree_manager.production_picker.picker_tui.engine_maintenance_actions import (
    PickerScreenMaintenanceActionsMixin,
)


@pytest.mark.parametrize("blocked", [False, True])
def test_real_pair_stage_precedes_completion_in_live_progress_screen(
    tmp_path, monkeypatch, blocked
):
    root = Path(__file__).resolve().parents[2]
    release = tmp_path / "release"
    fixture = tmp_path / "pair"
    fixture.mkdir()
    script = textwrap.dedent("""
        import argparse,sys,time
        from pathlib import Path
        root,fixture,release,blocked = sys.argv[1:5]
        sys.path[:0] = [str(Path(root)/'plugins/agent-worktrees/src'),
                        str(Path(root)/'plugins/agent-worktrees/tests')]
        sys.path[:0] = [str(path) for path in (Path(root)/'libs').glob('*/src')]
        import pytest
        from test_worktree_sync import _pair
        from test_squash_abort import _git
        from agent_worktrees import git_ops,worktree_identity,worktree_ops_cli,worktree_sync
        parser=argparse.ArgumentParser(allow_abbrev=False)
        parser.add_argument('--project')
        worktree_ops_cli.add_parsers(parser.add_subparsers(dest='command'))
        args=parser.parse_args(sys.argv[5:])
        with pytest.MonkeyPatch.context() as patch:
            records,paths,targets,overlay,calls=_pair(Path(fixture),patch)
            patch.setattr(worktree_identity,'_resolve_worktree_id',lambda value:value)
            if blocked == 'False':
                for role,path in paths.items():
                    (path/'.git/refs/heads/worktree'/role).write_text(targets[role]+'\\n')
                    _git(path,'read-tree',targets[role])
                    (path/'f.txt').write_text('new\\n')
            else:
                advance=git_ops.fast_forward_worktree
                def refuse(path,**kwargs):
                    if str(path)==str(paths['knowledge']):
                        return git_ops.FastForwardResult(False,'ff-failed')
                    return advance(path,**kwargs)
                patch.setattr(git_ops,'fast_forward_worktree',refuse)
            fetch=git_ops.fetch
            def wait(*args,**kwargs):
                deadline=time.monotonic()+15
                while not Path(release).exists():
                    if time.monotonic()>deadline: raise RuntimeError('fixture gate not released')
                    time.sleep(.02)
                return fetch(*args,**kwargs)
            patch.setattr(git_ops,'fetch',wait)
            code=worktree_ops_cli.cmd_sync(args)
        raise SystemExit(code)
    """)
    command = [
        sys.executable,
        "-B",
        "-c",
        script,
        str(root),
        str(fixture),
        str(release),
        str(blocked),
    ]
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: command)
    # This source fixture invokes the real parser/owner, not an installed slot.
    # Descriptor/selection tests separately qualify runtime negotiation.
    monkeypatch.setattr(engine_sync, "supports_progress", lambda selected: selected == command)
    executor = None
    row = {
        "raw": {"id": "harness"},
        "selection_id": "local/harness",
        "machine": "local",
        "env": "Win",
    }
    tasks = maintenance.build_tasks(
        "sync",
        [row],
        None,
        project="demo",
        local=("local", "Win"),
        on_progress=lambda key, update: executor.report(key, update),
    )
    executor = maintenance.MaintenanceExecutor("sync", tasks)
    screen = SimpleNamespace(
        executor=executor,
        spin=lambda: "*",
        debug="",
        progress={
            "op": "sync",
            "verb": "Sync",
            "scope": "fixture",
            "done": False,
            "armed": True,
            "items": [
                {"key": "local/harness", "id4": "harness", "title": "Task", "state": "pending"}
            ],
        },
    )
    screen._advance_progress = lambda: PickerScreenMaintenanceActionsMixin._poll_executor(screen)
    screen._key_progress = lambda key: PickerScreenInputMixin._key_progress(screen, key)

    class FixtureApp(App):
        ticks = 0

        def on_mount(self):
            self.push_screen(ProgressScreen(screen))
            self.set_interval(0.05, self.tick)

        def tick(self):
            self.ticks += 1

    async def exercise():
        app = FixtureApp()
        thread = threading.Thread(target=executor._run)
        async with app.run_test(size=(96, 36)) as pilot:
            thread.start()
            try:
                deadline = time.monotonic() + 15
                text = ""
                while time.monotonic() < deadline:
                    await pilot.pause(0.05)
                    text = app.screen._panel().renderable.plain
                    if "Fetching" in text:
                        break
                    if executor.is_done():
                        pytest.fail(str(executor.result("local/harness")))
                assert "Fetching" in text and not screen.progress["done"]
                ticks = app.ticks
                await pilot.press("escape")
                await pilot.pause(0.15)
                assert app.ticks > ticks and isinstance(app.screen, ProgressScreen)
                assert not executor.is_done() and "does not cancel" in screen.debug
            finally:
                release.touch()
                await asyncio.to_thread(thread.join, 10)
            assert not thread.is_alive()
            await pilot.pause(0.15)
            result = executor.result("local/harness")
            text = app.screen._panel().renderable.plain
            assert result["ok"] is not blocked and len(result["pair_results"]) == 2, result
            assert screen.progress["done"]
            if blocked:
                assert "harness: updated" in text and "knowledge: ff-failed" in text
                assert result["plugins"] == "skipped"
            else:
                assert result["reason"] == "up-to-date" and result["plugins"] == "ready"

    asyncio.run(exercise())
