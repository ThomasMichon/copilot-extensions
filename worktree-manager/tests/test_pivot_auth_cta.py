"""The lightweight Manager consumes the same summary/action contract."""
import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

from worktree_manager.picker_app import WorktreeManagerApp
from worktree_manager.pivot_progress import PivotProgressScreen
from worktree_manager.pivot_runtime import PivotPayload
from worktree_manager.plugin_contracts import parse_manifest
from worktree_manager.production_picker.picker_tui.tasks import RegisteredPivotRuntime


def test_preview_cta_click_and_refresh(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    raw = json.loads((root / "plugins/agent-codespaces/pivots/agent-codespaces.json").read_text("utf-8"))
    contribution = parse_manifest(
        raw, name="codespaces", marketplace="example", plugin="agent-codespaces",
        source_path="fixture",
    )
    calls = []
    completed = False

    def load(*_):
        return PivotPayload((), {
            "auth_status": "ready" if completed else "scope_blocked",
            "auth_account": "example-user",
            "banner_text": "" if completed else "Authorize the codespace scope.",
        })

    def run(self, action, ctx, on_frame, should_cancel):
        nonlocal completed
        calls.append(dict(ctx))
        on_frame(None, "ABCD-EFGH")
        completed = True
        return True, "Authorized"

    monkeypatch.setattr(RegisteredPivotRuntime, "run_action_stream", run)

    async def check():
        app = WorktreeManagerApp(lambda: [], project="example", contributions=[contribution],
                                 pivot_loader=load)
        async with app.run_test(size=(100, 30)) as pilot:
            descriptor = next(p for p in app._pivots if p.contribution is contribution)
            app._activate(descriptor)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert calls == []
            assert app.query_one("#view-contribution-0-authorize").display
            assert app._last_status == "Authorize the codespace scope."
            await pilot.click("#view-contribution-0-authorize")
            for _ in range(30):
                await pilot.pause()
                if isinstance(app.screen, PivotProgressScreen) and app.screen.finished:
                    break
            assert calls[0]["auth_account"] == "example-user"
            assert app.screen.finished
            await pilot.click("#view-cancel")
            await app.workers.wait_for_complete()
            assert not app.query_one("#view-contribution-0-authorize").display
            assert app._states[descriptor.key].summary["auth_status"] == "ready"

    asyncio.run(check())


def test_read_does_not_start_authentication(monkeypatch):
    run = Mock(side_effect=AssertionError("render must not authorize"))
    monkeypatch.setattr(RegisteredPivotRuntime, "run_action_stream", run)
    app = WorktreeManagerApp(lambda: [], project="example")

    async def check():
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            run.assert_not_called()

    asyncio.run(check())


def test_view_action_commands_use_the_payload_root(tmp_path):
    from worktree_manager.production_picker.picker_tui.pivot_manifest import (
        parse_manifest as parse_production,
    )
    from worktree_manager.production_picker.picker_tui.pivot_registry_materialization import (
        _rewrite_manifest_commands,
    )

    executable = tmp_path / "provider"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    manifest = {
        "label": "Example", "list": ["./provider", "list"],
        "view_actions": [{
            "label": "Authorize", "run": ["./provider", "authorize", "{auth_account}"],
            "when": {"auth_status": "scope_blocked"}, "progress": True,
        }],
    }
    resolved = _rewrite_manifest_commands(manifest, root=tmp_path, require_targets=True)
    production = parse_production(resolved, name="example", source_path="fixture")
    assert production.view_actions[0].run == (str(executable), "authorize", "{auth_account}")
    assert production.view_actions[0].progress
    assert production.view_actions[0].when == {"auth_status": "scope_blocked"}
