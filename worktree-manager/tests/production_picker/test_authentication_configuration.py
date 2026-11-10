"""Production Configuration consumes a contained native parser/provider owner."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import time
import venv

import pytest
from _installation_context_fixtures import namespaced_fixture
from test_config_actions import _wait
from test_picker_tui import _fixture_source, _wait_for_initial_setup

from worktree_manager import agent_plugin_runtime, engine_client, repository_authentication
from worktree_manager.production_picker import context
from worktree_manager.production_picker.picker_tui.configuration_form import ConfigurationDetailScreen
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.engine_dialogs import TaskMenuScreen

pytestmark = pytest.mark.contract("worktree_manager.configuration_backstage")


@pytest.mark.parametrize("namespaced", [
    False,
    pytest.param(True, marks=pytest.mark.skipif(
        os.name != "nt", reason="Windows USERPROFILE namespaced list-process fixture",
    )),
], ids=["legacy", "namespaced"])
def test_native_authentication_navigation_stale_errors_refresh_and_rollback(
    tmp_path, monkeypatch, namespaced,
):
    source = Path(__file__).resolve().parents[3] / "plugins" / "agent-worktrees" / "src"
    home = tmp_path / "home"
    for key in ("AGENT_HOME", "HOME", "USERPROFILE"):
        monkeypatch.setenv(key, str(home))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "WORKTREE_MANAGER_ENGINE_CMD",
                "WORKTREE_MANAGER_ENGINE_ARGV"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(source.parent))
    root = home / ".agent-worktrees"
    slot = root / "versions" / "1.2.3"
    install = None
    if namespaced:
        install, python = namespaced_fixture(home, windows=os.name == "nt")
        python.unlink()
        slot = python.parent.parent
        root = slot.parent.parent
        receipt = json.loads(install.read_text("utf-8"))
        receipt["payload"]["root"] = str(source.parent.resolve())
        install.write_text(json.dumps(receipt), encoding="utf-8")
        (home / ".copilot-extensions" / "installation-mode.json").write_text(json.dumps({
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": True},
        }), encoding="utf-8")
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(install))
    venv.EnvBuilder(with_pip=False).create(slot)
    site = slot / (
        "Lib/site-packages" if os.name == "nt"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    package = site / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text(
        f"__path__.append({str(source / 'agent_worktrees')!r})\n", encoding="utf-8",
    )
    descriptor = package / "repository-authentication-v1.json"
    descriptor.write_bytes((source / "agent_worktrees" / descriptor.name).read_bytes())
    parent_site = Path(sys.prefix) / (
        "Lib/site-packages" if os.name == "nt"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    control, release, trace = (tmp_path / name for name in ("control.json", "release", "trace.jsonl"))
    trace.mkdir()
    (package / "__main__.py").write_text(
        "import json,os,site,sys,time,uuid\nfrom pathlib import Path\n"
        f"site.addsitedir({str(parent_site)!r})\n"
        "from agent_worktrees import github_credentials\n"
        "from agent_worktrees.repos_authentication import command\n"
        "if sys.argv[1:3]!=['repos','authentication']:\n"
        " print(json.dumps({'error':'unsupported-fixture-operation'}));raise SystemExit(2)\n"
        f"control=json.loads(Path({str(control)!r}).read_text())\n"
        f"receipt=Path({str(trace)!r})/(str(time.time_ns())+'-'+uuid.uuid4().hex)\n"
        "receipt.write_text(json.dumps({'argv':sys.argv[1:],'prefix':sys.prefix,"
        "'context':os.environ.get('COPILOT_EXTENSIONS_CONTEXT')}))\n"
        "receipt.rename(receipt.with_suffix('.json'))\n"
        "def observe(host,login,repository):\n"
        " deadline=time.monotonic()+12\n"
        f" while control.get('wait') and not Path({str(release)!r}).exists():\n"
        "  if time.monotonic()>deadline: raise RuntimeError('fixture release expired')\n"
        "  time.sleep(0.02)\n"
        " if control.get('status')=='unavailable':\n"
        "  raise github_credentials.CredentialObservationError('identity','access-unconfirmed')\n"
        " return {'status':'observed','credential':{'status':'available'},"
        "'identity':{'status':'verified'},'repository_access':{'status':'verified'}}\n"
        "github_credentials.inspect_github_account=observe\n"
        "if control.get('malformed'):\n"
        " print('private-malformed-provider-output');raise SystemExit(0)\n"
        "raise SystemExit(command(sys.argv[3:]))\n",
        encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    if not namespaced:
        (root / "deploy-manifest.json").write_text(json.dumps({
            "service": "agent-worktrees", "source": {"plugin": "agent-worktrees"},
        }), encoding="utf-8")
    registry = root / "repos.yaml"
    if namespaced:
        from agent_worktrees import registry_paths

        registry = registry_paths.registry_path("repos.yaml")
        assert registry.is_relative_to(home)
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        "repos:\n"
        "  sample:\n    remote: https://github.com/example/sample.git\n    account: selected-user\n"
        "  second:\n    remote: https://github.com/example/second.git\n    account: selected-user\n",
        encoding="utf-8",
    )
    sentinel = home / "credential-sentinel"
    sentinel.write_bytes(b"private-sentinel-do-not-render")
    before = {path: path.read_bytes() for path in (registry, sentinel, descriptor)}
    if install is not None:
        before[install] = install.read_bytes()
        assert agent_plugin_runtime.resolve_installed_plugin_slot("agent-worktrees") == slot
    monkeypatch.setattr(engine_client, "_ENGINE_CMD_OVERRIDE", None)
    monkeypatch.setattr(engine_client, "_INHERITED_ENGINE_COMMAND", None)
    monkeypatch.setattr(context, "_project", "sample")
    monkeypatch.setattr(context, "_bootstrap", None)
    control.write_text(json.dumps({"wait": True}), encoding="utf-8")

    def calls():
        return [json.loads(path.read_text()) for path in sorted(trace.glob("*.json"))]

    def body(screen):
        return "\n".join(row.text.plain for row in screen.build_body(118))

    milestones = []
    started = time.monotonic()

    def mark(stage):
        milestones.append((stage, round(time.monotonic() - started, 3)))

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            screen = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, screen)
            mark("initial setup")
            assert calls() == []
            screen._open_cfgmenu()
            await pilot.pause()
            assert calls() == [], "Opening Profiles must not probe Authentication"
            for _ in range(len(screen.pivots) + 1):
                if getattr(screen._reg_pivot(), "name", None) == repository_authentication.PIVOT_NAME:
                    break
                screen._rotate_config_section(1)
                await pilot.pause()
            runtime = screen._pivot_runtime(screen._reg_pivot())
            try:
                await _wait(pilot, lambda: calls() or runtime.get("")[0] in ("ready", "error"))
                assert len(calls()) == 1, runtime.get("")
                assert screen._task_state()[0] == "loading"
                assert "Authentication" in screen.tab_bar(118, True).plain
                assert "loading" in body(screen).lower()
                screen._rotate_config_section(1)
                await pilot.pause()
                assert getattr(screen._reg_pivot(), "name", None) != repository_authentication.PIVOT_NAME
                release.touch()
                await _wait(pilot, lambda: runtime.get("")[0] in ("ready", "error"))
                assert runtime.get("")[0] == "ready", runtime.get("")
                mark("initial observation")
                assert len(calls()) == 1
                auth_index = next(i for i, item in enumerate(screen.pivots)
                                  if getattr(item.get("pivot"), "name", None) == repository_authentication.PIVOT_NAME)
                screen.htab = auth_index
                screen._task_rows()
                screen.sel = ("T", 0)
                screen.refresh()
                await pilot.pause()
                assert "verified" in body(screen) and "observed" in body(screen)
                assert screen._selected_task() is not None, (screen.sel, screen._task_state())
                screen._open_task_menu()
                await _wait(pilot, lambda: isinstance(app.screen, TaskMenuScreen))
                app.screen.dismiss(0)
                await _wait(pilot, lambda: isinstance(app.screen, ConfigurationDetailScreen))
                card = str(app.screen.card)
                assert "sample/sample" in card and "Git transport" in card
                assert "not observed" in card and "no sign-in" in card
                assert "private-sentinel" not in card
                app.screen.dismiss(None)
                await pilot.pause()
                mark("details returned")
                release.unlink()
                control.write_text(json.dumps({"wait": True, "status": "unavailable"}), encoding="utf-8")
                screen._dispatch_key("r")
                await _wait(pilot, lambda: len(calls()) == 2)
                assert "stale" in body(screen)
                screen._rotate_config_section(1)
                release.touch()
                await _wait(pilot, lambda: not runtime.read_pending(""))
                assert runtime.get("")[0] == "ready", runtime.get("")
                assert runtime.get_summary("")["status"] == "unavailable"
                mark("unavailable observation")
                assert screen.htab != auth_index, "Read completion must not steal navigation"
                screen.htab = auth_index
                screen.refresh()
                await pilot.pause()
                control.write_text(json.dumps({"malformed": True}), encoding="utf-8")
                screen._dispatch_key("r")
                await _wait(pilot, lambda: runtime.get("")[0] == "error")
                assert "invalid or failed" in body(screen)
                assert "private-malformed-provider-output" not in body(screen)
                mark("malformed observation")
                control.write_text("{}", encoding="utf-8")
                monkeypatch.setattr(context, "_project", "second")
                screen._dispatch_key("r")
                await _wait(pilot, lambda: not runtime.read_pending(""))
                assert runtime.get("")[0] == "ready", (runtime.get("")[2], milestones)
                mark("changed project observation")
                assert all("second/second" in row["card"]["body"] for row in runtime.get("")[1])
                assert calls()[-1]["argv"][-5:] == [
                    "--context-project", "second", "--repository", "second", "--json",
                ]
                assert all(Path(call["prefix"]) == slot for call in calls())
                assert all(call["context"] == (str(install) if install else None) for call in calls())
                assert {path: path.read_bytes() for path in before} == before
                count = len(calls())
                descriptor.unlink()
                screen._dispatch_key("r")
                await _wait(pilot, lambda: runtime.get("")[1][0]["status"] == "unsupported")
                assert len(calls()) == count, "Rollback must refuse before invoking optional syntax"
                assert "unsupported" in body(screen)
                assert registry.read_bytes() == before[registry] and sentinel.read_bytes() == before[sentinel]
            finally:
                release.touch()

    async def bounded():
        try:
            await asyncio.wait_for(run(), timeout=20)
        except TimeoutError:
            pytest.fail(f"Authentication UI did not settle within 20s; phases: {milestones}")

    asyncio.run(bounded())
