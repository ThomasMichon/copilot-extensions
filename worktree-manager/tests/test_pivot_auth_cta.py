"""The lightweight Manager consumes the same summary/action contract."""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from worktree_manager.picker_app import WorktreeManagerApp
from worktree_manager.pivot_progress import PivotProgressScreen
from worktree_manager.pivot_runtime import PivotPayload
from worktree_manager.plugin_contracts import parse_manifest
from worktree_manager.production_picker.picker_tui.tasks import RegisteredPivotRuntime


async def _select_contribution(app, contribution, pilot):
    from textual.widgets import Tabs

    app.query_one(Tabs).active = next(
        tab for tab, descriptor in app._pivot_by_tab.items()
        if descriptor.contribution is contribution
    )
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    return app._active_pivot()


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
            descriptor = await _select_contribution(app, contribution, pilot)
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


@pytest.mark.parametrize("kind", ["form", "internal"])
def test_preview_never_executes_unsupported_view_action_kinds(monkeypatch, kind):
    from textual.widgets import Button

    action = (
        {"kind": "form", "fields_from": "fields", "run": ["example-provider", "{field.value}"]}
        if kind == "form" else
        {"kind": "internal", "verb": "example", "args": ["not-an-executable"]}
    )
    contribution = parse_manifest({
        "label": "Example", "list": ["example-provider", "list"],
        "view_actions": [{"key": "unsupported", "label": "Unsupported", **action}],
    }, name="example", marketplace="example", plugin="example", source_path="fixture")
    run = Mock(side_effect=AssertionError("unsupported actions must not execute"))
    monkeypatch.setattr(RegisteredPivotRuntime, "run_action_stream", run)

    async def check():
        app = WorktreeManagerApp(
            lambda: [], project="example", contributions=[contribution],
            pivot_loader=lambda *_: PivotPayload((), {}),
        )
        async with app.run_test(size=(100, 30)) as pilot:
            await _select_contribution(app, contribution, pilot)
            assert not list(app.query("#view-contribution-0-unsupported"))
            # Also enforce dispatch, even if a stale/foreign button emits the ID.
            container = app.query_one("#view-actions")
            container.display = True
            await container.mount(Button("Unsupported", id="view-contribution-0-unsupported"))
            await pilot.pause()
            await pilot.click("#view-contribution-0-unsupported")
            await pilot.pause()
            run.assert_not_called()

    asyncio.run(check())


def _installed_provider(tmp_path, monkeypatch):
    from worktree_manager.plugin_contracts import discover_contracts

    home = tmp_path / "home"
    root = home / ".copilot/installed-plugins/example/agent-example"
    (root / "pivots").mkdir(parents=True)
    (root / "bin").mkdir()
    (home / ".copilot/settings.json").write_text(json.dumps({
        "enabledPlugins": {"agent-example@example": True},
    }), encoding="utf-8")
    peer = tmp_path / "unrelated"
    peer.mkdir()
    filename = "agent-example.cmd" if os.name == "nt" else "agent-example"
    for directory, message in ((root / "bin", "owned"), (peer, "wrong")):
        path = directory / filename
        envelope = json.dumps({"type": "done", "message": message})
        path.write_text(
            "@echo off\necho " + envelope + "\n" if os.name == "nt"
            else "#!/bin/sh\nprintf '%s\\n' '" + envelope + "'\n",
            encoding="utf-8",
        )
        path.chmod(0o700)
    (root / "payload-invocation.json").write_text(json.dumps({
        "schema": "copilot-extensions.payload-invocation", "version": 2,
        "command": "agent-example",
    }), encoding="utf-8")
    (root / "pivots/example.json").write_text(json.dumps({
        "schema_version": 1, "label": "Example",
        "list": ["agent-example", "list"],
        "view_actions": [{
            "key": "authorize", "label": "Authorize", "run": ["agent-example", "authorize"],
            "when": {"auth_status": "scope_blocked"}, "progress": True,
        }],
    }), encoding="utf-8")
    monkeypatch.setenv("PATH", str(peer) + os.pathsep + os.environ.get("PATH", ""))
    def discover(**kwargs):
        return discover_contracts(
            plugins_root=home / ".copilot/installed-plugins", **kwargs,
        )

    return home, root / "bin" / filename, discover


def test_preview_click_uses_contributing_payload_not_first_provider_on_path(tmp_path, monkeypatch):
    home, owned, discover = _installed_provider(tmp_path, monkeypatch)
    [contribution] = discover(home_dir=home).contributions
    assert contribution.pivot.view_actions[0].run[0] == str(owned)

    async def check():
        app = WorktreeManagerApp(
            lambda: [], project="example", contributions=[contribution],
            pivot_loader=lambda *_: PivotPayload((), {"auth_status": "scope_blocked"}),
        )
        async with app.run_test(size=(100, 30)) as pilot:
            await _select_contribution(app, contribution, pilot)
            await pilot.click("#view-contribution-0-authorize")
            for _ in range(50):
                await pilot.pause()
                if isinstance(app.screen, PivotProgressScreen) and app.screen.finished:
                    break
            assert app.screen.finished
            assert "Succeeded: owned" in str(app.screen.query_one("#view-progress").render())

    asyncio.run(check())


def test_missing_owned_payload_command_never_uses_a_peer_on_path(tmp_path, monkeypatch):
    home, owned, discover = _installed_provider(tmp_path, monkeypatch)
    owned.unlink()
    [contribution] = discover(home_dir=home).contributions
    assert contribution.pivot.view_actions[0].run[0] == str(owned)
    assert not contribution.command_available
    assert not contribution.pivot.view_actions[0].available
