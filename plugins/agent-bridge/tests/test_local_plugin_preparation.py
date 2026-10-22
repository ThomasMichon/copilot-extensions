"""Static local ACP prepares the real allocated checkout before launch."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import plugin_resolve

from agent_bridge import transport, venue_plugins
from agent_bridge.spawn_target import PluginRef, SpawnTarget
from plugin_resolve.registration import atomic_json


def checkout(root):
    subprocess.run(
        ["git", "init", "--quiet", str(root)], check=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return root


def marketplace(root, text="selected"):
    payload = root / ".ai" / "sample"
    payload.mkdir(parents=True)
    atomic_json(payload / "plugin.json", {"name": "sample", "version": "1.0.0"})
    (payload / "selected.txt").write_text(text)
    atomic_json(payload.parent / "marketplace.json", {
        "name": "local", "owner": {"name": "Synthetic Publisher"},
        "plugins": [{"name": "sample", "source": "sample"}],
    })
    settings = root / ".github" / "copilot"
    settings.mkdir(parents=True)
    atomic_json(settings / "settings.json", {
        "extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": "./.ai"}}},
    })
    return payload.resolve()


@pytest.fixture
def isolated_selection(monkeypatch):
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    monkeypatch.setattr(
        "agent_bridge.related_plugins.related_plugins_for_repo",
        lambda repo, anchors: [PluginRef("sample@local")] if anchors else [],
    )


def target_for(root, controller=None):
    return SpawnTarget(
        cwd=str(root), explicit_cwd=True, project="sample-project",
        caller_worktree=str(controller) if controller else None,
        copilot_path=sys.executable, local_plugin_preparation=True,
        static_plugin_args={"base": ["--plugin-dir", "explicit"], "automatic": ["--plugin-dir", "old"]},
        copilot_args=["--plugin-dir", "explicit", "--plugin-dir", "old", "--plugin-dir", "request"],
    )


def test_static_plugin_arguments_survive_typed_runtime_snapshot(tmp_path):
    from pydantic import TypeAdapter

    target = target_for(tmp_path)
    adapter = TypeAdapter(SpawnTarget)
    restored = adapter.validate_json(adapter.dump_json(target), strict=True)
    assert restored == target
    assert restored.static_plugin_args == {
        "base": ["--plugin-dir", "explicit"],
        "automatic": ["--plugin-dir", "old"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["bin", "", "."])
@pytest.mark.parametrize("checkout_name", ["execution", "execution checkout"])
async def test_real_launch_resolves_relative_path_from_execution_checkout(
    tmp_path, monkeypatch, entry, checkout_name,
):
    daemon, execution, fallback = (tmp_path / name for name in ("daemon", checkout_name, "fallback"))
    child_bin = execution / entry if entry else execution
    daemon.mkdir()
    for directory, marker in ((child_bin, "execution"), (fallback, "fallback")):
        directory.mkdir(parents=True, exist_ok=True)
        binary = directory / ("copilot.cmd" if os.name == "nt" else "copilot")
        binary.write_text(
            f"@echo {marker}\n" if os.name == "nt" else f"#!/bin/sh\nprintf '{marker}\\n'\n",
        )
        binary.chmod(0o700)
    monkeypatch.chdir(daemon)
    path = os.pathsep.join([entry, str(fallback)])
    target = SpawnTarget(
        cwd=str(execution), explicit_cwd=True, env={"PATH": path, "COPILOT_PATH": ""},
    )
    args, cwd, environment = await transport.resolve_local_launch(target)
    result = subprocess.run(
        args, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0 and result.stdout.strip() == "execution", result.stderr
    expected = str(child_bin / ("copilot.cmd" if os.name == "nt" else "copilot"))
    actual = environment["COPILOT_VENUE_BATCH_EXECUTABLE"].strip('"') if os.name == "nt" else args[0]
    assert os.path.normcase(actual) == os.path.normcase(expected)
    assert environment["PATH"] == path and Path.cwd() == daemon


@pytest.mark.asyncio
async def test_fresh_repeat_direct_reference_preserves_explicit_flags_and_source(
    tmp_path, monkeypatch, isolated_selection,
):
    root, controller, anchor = checkout(tmp_path / "target"), tmp_path / "controller", tmp_path / "anchor"
    payload = marketplace(controller, "controller")
    marketplace(anchor, "stale")
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [anchor])
    target = target_for(root, controller)
    original = target.to_json()
    source = (controller / ".ai" / "marketplace.json").read_bytes()
    for _ in range(2):
        args, cwd, env = await transport.resolve_local_launch(target)
        assert args[-6:] == ["--plugin-dir", "explicit", "--plugin-dir", str(payload),
                              "--plugin-dir", "request"]
        assert cwd == str(root) and target.to_json() == original
        assert (root / ".github" / "copilot" / "settings.local.json").exists()
        consumer = subprocess.run(
            [sys.executable, "-c",
             "import json,sys;from plugin_resolve.resolve import resolve_repo_plugins;"
             "r=resolve_repo_plugins(sys.argv[1]);assert not r.unresolved;"
             "print(json.dumps({k:str(v) for k,v in r.resolved.items()}))", str(root)],
            env={**env, "PYTHONPATH": str(Path(plugin_resolve.__file__).parents[1])},
            capture_output=True, text=True, check=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert json.loads(consumer.stdout) == {"sample@local": str(payload)}
    assert (controller / ".ai" / "marketplace.json").read_bytes() == source
    assert not list((root / ".git").rglob("snapshots"))
    assert not list((root / ".git").rglob("marketplace.json"))


@pytest.mark.asyncio
async def test_real_allocation_and_plan_environment_precede_barrier(tmp_path, monkeypatch):
    allocated = tmp_path / "allocated"
    target = target_for(tmp_path / "anchor")
    target.explicit_cwd = False
    target.env = {"CHILD": "target"}
    observed = []

    async def allocate(received, env):
        assert not allocated.exists()
        checkout(allocated)
        return {"launch": {"cmd": [sys.executable], "work_dir": str(allocated),
                           "worktree_id": "allocated-id", "env": {"CHILD": "plan"}}}

    async def prepare(received, cwd, env, *, prepared_context=None):
        assert Path(cwd).is_dir() and env["CHILD"] == "plan"
        assert received.worktree_id == "allocated-id"
        observed.append(cwd)
        return ["--plugin-dir", "prepared"]

    monkeypatch.setattr(transport, "_resolve_worktree", allocate)
    monkeypatch.setattr(venue_plugins, "prepare_local_plugins", prepare)
    args, cwd, env = await transport.resolve_local_launch(target)
    assert observed == [str(allocated)] and cwd == str(allocated)
    assert args[-6:] == ["--plugin-dir", "explicit", "--plugin-dir", "prepared",
                         "--plugin-dir", "request"]
    assert env["CHILD"] == "plan"


@pytest.mark.asyncio
@pytest.mark.parametrize("excluded", ["ssh", "native", "legacy-elevated", "command", "receiver", "legacy"])
async def test_deferred_paths_do_not_prepare(tmp_path, monkeypatch, excluded):
    target = target_for(tmp_path)
    options = {}
    if excluded == "ssh":
        target.type = "ssh"
    elif excluded in {"native", "legacy"}:
        target.local_plugin_preparation = False
    elif excluded == "legacy-elevated":
        target.elevated = True
        target.local_plugin_preparation = False
    elif excluded == "command":
        target.spawn_command = ["wrapper"]
    else:
        options["prepare_plugins"] = False
    barrier = AsyncMock()
    monkeypatch.setattr(venue_plugins, "prepare_local_plugins", barrier)
    await transport.resolve_local_launch(target, **options)
    barrier.assert_not_awaited()


@pytest.mark.asyncio
async def test_gitless_and_opaque_affinity_remain_not_applicable(tmp_path, isolated_selection):
    target = target_for(tmp_path)
    target.caller_worktree = "driver/acquisition:delivery"
    target = venue_plugins.with_controller_origin(target, "affinity")
    args, _, _ = await transport.resolve_local_launch(target)
    assert args[-len(target.copilot_args):] == target.copilot_args
    assert not (tmp_path / ".github").exists()
    assert await venue_plugins.resolve_controller_cwd(target) is None


@pytest.mark.asyncio
async def test_missing_selected_payload_blocks_before_spawn(tmp_path, monkeypatch, isolated_selection):
    root, controller = checkout(tmp_path / "target"), tmp_path / "controller"
    payload = marketplace(controller)
    (payload / "plugin.json").unlink()
    child = AsyncMock()
    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", child)
    with pytest.raises(ValueError, match="Required local plugin is absent"):
        await transport.spawn_local(target_for(root, controller))
    child.assert_not_awaited()
    assert not (root / ".github" / "copilot" / "settings.local.json").exists()


@pytest.mark.asyncio
async def test_cancel_waits_for_worker_quiescence(tmp_path, monkeypatch, isolated_selection):
    root = checkout(tmp_path / "target")
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

    def prepare(request, **kwargs):
        entered.set()
        assert release.wait(5)
        try:
            kwargs["check_cancelled"]()
        finally:
            stopped.set()

    monkeypatch.setattr("plugin_resolve.preparation.prepare", prepare)
    pending = asyncio.create_task(venue_plugins.prepare_local_plugins(target_for(root), str(root), dict(os.environ)))
    for _ in range(100):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set()
    pending.cancel()
    await asyncio.sleep(0.02)
    assert not pending.done()
    pending.cancel()
    await asyncio.sleep(0.02)
    assert not pending.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert stopped.is_set()


def test_installer_uses_child_override_not_acp_command(monkeypatch):
    assert transport._find_copilot({"COPILOT_PATH": "child-installer.cmd"}) == "child-installer.cmd"
    monkeypatch.setattr(transport.sys, "platform", "win32")
    monkeypatch.setattr(transport.shutil, "which", lambda exe, path: exe)
    environment = {"PATH": "child-path", "COMSPEC": "child-cmd.exe"}
    assert transport._wrap_batch_for_windows(["child-installer.cmd"], environment) == [
        "child-cmd.exe", "/d", "/v:off", "/s", "/c", "%COPILOT_VENUE_BATCH_EXECUTABLE%",
    ]
    assert environment["COPILOT_VENUE_BATCH_EXECUTABLE"] == '"child-installer.cmd"'


@pytest.mark.parametrize("admin", [False, True])
def test_static_base_and_final_binding_provenance(tmp_path, monkeypatch, admin):
    from agent_bridge.agent_registry import AgentConfig, AgentResolver

    resolver = AgentResolver({
        "local": AgentConfig(
            name="local", project="default", cwd=str(tmp_path),
            copilot_args=["--plugin-dir", "configured"], requires_admin=admin,
        ),
    }, {}, resolved_local_identity=None)
    monkeypatch.setattr(resolver, "_own_plugin_args", lambda repo, cwd=None: ["--plugin-dir", repo])
    monkeypatch.setattr(resolver, "_related_plugin_args", lambda repo: ["--plugin-dir", "related-" + repo])
    target = resolver._resolve_static("local")
    bound = resolver._bind_repo(target, "final", "local")
    assert bound.local_plugin_preparation is (not admin)
    assert bound.static_plugin_args == {
        "base": ["--plugin-dir", "configured"],
        "automatic": ["--plugin-dir", "final", "--plugin-dir", "related-final"],
    }
    assert SpawnTarget.from_json(bound.to_json()).static_plugin_args == bound.static_plugin_args
    assert "default" not in bound.copilot_args


@pytest.mark.parametrize("login,configured,eligible", [
    (None, None, False),
    ("current", None, True),
    ("other", None, False),
    (None, "current", True),
    (None, "other", False),
    ("current", "other", True),
    ("other", "current", False),
    ("CURRENT", None, False),
])
def test_static_loopback_account_boundary(monkeypatch, login, configured, eligible):
    from agent_bridge.agent_registry import AgentConfig, AgentResolver
    from agent_bridge.topology import MachineConfig, SshEnvironment

    machine = MachineConfig(
        key="synthetic", display_name="Synthetic",
        ssh_environments=[SshEnvironment(name="linux", alias="synthetic", user=login)],
    )
    monkeypatch.setattr(
        "agent_bridge.agent_registry._detect_local_machine", lambda machines: (machine, "linux"),
    )
    monkeypatch.setattr("getpass.getuser", lambda: "current")
    resolver = AgentResolver({
        "agent": AgentConfig(name="agent", host="synthetic", ssh_user=configured),
    }, {"synthetic": machine})
    monkeypatch.setattr(resolver, "_own_plugin_args", lambda *args: [])
    monkeypatch.setattr(resolver, "_related_plugin_args", lambda *args: [])
    assert resolver._resolve_static("agent").local_plugin_preparation is eligible


@pytest.mark.asyncio
async def test_controller_session_qualifier_survives_local_preparation(tmp_path, monkeypatch, isolated_selection):
    root, controller = checkout(tmp_path / "target"), tmp_path / "controller"
    payload = marketplace(controller)
    target = target_for(root, controller)
    target.caller_owner_ref = "synthetic/control/controller#caller-session"
    target = venue_plugins.with_controller_origin(target, "explicit", caller_session_id="caller-session")
    owner = AsyncMock(return_value={"worktrees": [{
        "id": "controller", "repo": "control", "machine": "synthetic",
        "path": str(controller), "status": "active",
    }]})
    monkeypatch.setattr("agent_bridge.acquisition_authority.worktree_command", owner)
    args, _, _ = await transport.resolve_local_launch(target)
    assert str(payload) in args
    owner.assert_awaited_once()


@pytest.mark.asyncio
async def test_target_disable_and_native_origin_override_are_effective(tmp_path, isolated_selection):
    root, controller = checkout(tmp_path / "target"), tmp_path / "controller"
    marketplace(controller)
    native = marketplace(root, "native")
    settings = root / ".github" / "copilot" / "settings.local.json"
    atomic_json(settings, {"unrelated": 42, "enabledPlugins": {"sample@local": False}})
    target = target_for(root, controller)
    args, _, _ = await transport.resolve_local_launch(target)
    assert str(native) not in args and "old" not in args
    assert json.loads(settings.read_text())["unrelated"] == 42
    value = json.loads(settings.read_text())
    value["enabledPlugins"]["sample@local"] = True
    atomic_json(settings, value)
    args, _, _ = await transport.resolve_local_launch(target)
    assert str(native) in args


@pytest.mark.asyncio
async def test_malformed_overlay_preserves_previous_bytes_and_prevents_child(
    tmp_path, monkeypatch, isolated_selection,
):
    root = checkout(tmp_path / "target")
    directory = root / ".github" / "copilot"
    directory.mkdir(parents=True)
    path = directory / "settings.local.json"
    path.write_text("{broken")
    child = AsyncMock()
    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", child)
    with pytest.raises(ValueError):
        await transport.spawn_local(target_for(root))
    assert path.read_text() == "{broken"
    child.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [
    "environment", "explicit", "managed-plan", "environment-relative",
    "explicit-relative", "path-relative", "managed-relative",
])
async def test_real_child_environment_installer_and_direct_reference_barrier(
    tmp_path, monkeypatch, isolated_selection, mode,
):
    import shlex

    root, controller = checkout(tmp_path / "target"), tmp_path / "controller"
    payload = marketplace(controller)
    fixture = (Path(__file__).resolve().parents[3] / "libs" / "plugin-resolve" /
               "tests" / "fixtures" / "venue_installer.py")
    relative = mode.endswith("-relative")
    directory = root / "bin" if relative else tmp_path
    directory.mkdir(exist_ok=True)
    name = "copilot" if mode == "path-relative" else "native-installer"
    installer = directory / (name + ".cmd" if os.name == "nt" else name)
    if os.name == "nt":
        installer.write_text(f'@"{sys.executable}" "{fixture}" %*\n')
    else:
        installer.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(fixture))} \"$@\"\n",
        )
        installer.chmod(0o700)
    settings = root / ".github" / "copilot"
    settings.mkdir(parents=True)
    atomic_json(settings / "settings.json", {
        "enabledPlugins": {"native@remote": True},
        "extraKnownMarketplaces": {
            "remote": {"source": {"source": "github", "repo": "example/synthetic"}},
        },
    })
    target = target_for(root, controller)
    log = tmp_path / "native-calls.jsonl"
    selected = os.path.relpath(installer, root) if relative else str(installer)
    explicit = mode in {"explicit", "explicit-relative"}
    managed = mode in {"managed-plan", "managed-relative"}
    target.copilot_path = selected if explicit else None
    target.env = {
        "COPILOT_PATH": str(tmp_path / "must-not-run") if explicit else selected,
        "VENUE_CALL_LOG": str(log),
    }
    if relative:
        daemon, fallback = tmp_path / "daemon", tmp_path / "fallback"
        daemon.mkdir()
        fallback.mkdir()
        wrong = fallback / ("copilot.cmd" if os.name == "nt" else "copilot")
        wrong.write_text("@exit /b 77\n" if os.name == "nt" else "#!/bin/sh\nexit 77\n")
        wrong.chmod(0o700)
        monkeypatch.chdir(daemon)
        target.env["PATH"] = os.pathsep.join(["bin", str(fallback), os.environ.get("PATH", "")])
        if mode == "path-relative":
            target.env["COPILOT_PATH"] = ""
    if managed:
        target.explicit_cwd = False
        target.copilot_path = str(tmp_path / "ignored-direct-copilot")
        target.env["COPILOT_PATH"] = str(tmp_path / "overridden-packaging-cli")
        monkeypatch.setattr(transport, "_resolve_worktree", AsyncMock(return_value={
            "launch": {
                "cmd": ["opaque-runtime-wrapper", "--runtime-only-option"],
                "work_dir": str(root), "worktree_id": "synthetic-allocated",
                "env": {"COPILOT_PATH": selected},
            },
        }))
    for _ in range(2):
        args, _, environment = await transport.resolve_local_launch(target)
        if relative:
            assert environment["PATH"] == target.env["PATH"] and Path.cwd() == daemon
        if managed:
            assert args[:2] == ["opaque-runtime-wrapper", "--runtime-only-option"]
            assert str(installer) not in args
            assert environment.get("COPILOT_VENUE_BATCH_EXECUTABLE") == os.environ.get(
                "COPILOT_VENUE_BATCH_EXECUTABLE",
            )
        else:
            actual = (
                environment["COPILOT_VENUE_BATCH_EXECUTABLE"].strip('"')
                if os.name == "nt" else args[0]
            )
            assert os.path.normcase(str(installer)) == os.path.normcase(actual)
        assert str(payload) in args
        native = [value for value in args if "installed-plugins" in value]
        assert len(native) == 1 and (Path(native[0]) / "plugin.json").exists()
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [call["argv"][:2] for call in calls] == [
        ["plugin", "install"],
    ]
    assert all(not Path(call["cwd"]).is_relative_to(root) for call in calls)
    assert target.copilot_args[-2:] == ["--plugin-dir", "request"]


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [False, True])
async def test_missing_packaging_cli_is_required_only_for_installation(
    tmp_path, isolated_selection, remote,
):
    root, controller = checkout(tmp_path / "target"), tmp_path / "controller"
    payload = marketplace(controller)
    target = target_for(root, controller)
    target.copilot_path = str(tmp_path / "missing-packaging-cli")
    if remote:
        directory = root / ".github" / "copilot"
        directory.mkdir(parents=True)
        atomic_json(directory / "settings.json", {
            "enabledPlugins": {"native@remote": True},
            "extraKnownMarketplaces": {
                "remote": {"source": {"source": "github", "repo": "example/synthetic"}},
            },
        })
        with pytest.raises(FileNotFoundError):
            await transport.resolve_local_launch(target)
        assert not (directory / "settings.local.json").exists()
    else:
        args, _, _ = await transport.resolve_local_launch(target)
        assert str(payload) in args


@pytest.mark.asyncio
async def test_real_related_file_selection_from_actual_local_registry_target(tmp_path):
    from agent_bridge.agent_registry import AgentConfig, AgentResolver
    from agent_bridge.related_plugins import related_plugins_for_repo
    from plugin_resolve.resolve import resolve_repo_plugins

    root, controller = checkout(tmp_path / "target"), tmp_path / "controller"
    payload = marketplace(controller)
    related = controller / ".agent-worktrees"
    related.mkdir()
    (related / "related.yaml").write_text(
        "related:\n"
        "  synthetic-target:\n"
        "    plugins:\n"
        "      - source: sample@local\n"
        "        enable: true\n",
        encoding="utf-8",
    )
    assert related_plugins_for_repo("synthetic-target", anchors=[controller]) == [
        PluginRef("sample@local", True),
    ]
    resolver = AgentResolver({
        "synthetic": AgentConfig(
            name="synthetic", cwd=str(root), project="synthetic-target", copilot_path=sys.executable,
        ),
    }, {})
    target = resolver._resolve_static("synthetic")
    target.explicit_cwd = True
    target.caller_worktree = str(controller)
    args, cwd, environment = await transport.resolve_local_launch(target)
    assert args[0] == sys.executable and cwd == str(root)
    assert args[-2:] == ["--plugin-dir", str(payload)]
    assert resolve_repo_plugins(root).resolved == {"sample@local": payload}
    consumer = subprocess.run(
        [sys.executable, "-c",
         "import sys;from plugin_resolve.resolve import resolve_repo_plugins;"
         "r=resolve_repo_plugins(sys.argv[1]);assert not r.unresolved;"
         "assert set(r.resolved)=={'sample@local'};"
         "assert str(r.resolved['sample@local'])==sys.argv[2]", str(root), str(payload)],
        env={**environment, "PYTHONPATH": str(Path(plugin_resolve.__file__).parents[1])},
        capture_output=True, text=True, check=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert consumer.returncode == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["own", "related"])
async def test_existing_installed_fallback_uses_actual_child_inventory(
    tmp_path, monkeypatch, selection,
):
    root = checkout(tmp_path / "target")
    controller = tmp_path / "controller"
    controller.mkdir()
    if selection == "related":
        directory = controller / ".agent-worktrees"
        directory.mkdir()
        (directory / "related.yaml").write_text(
            "related:\n  sample-project:\n    plugins:\n      - source: helper@catalog\n",
        )
    else:
        directory = root / ".github" / "copilot"
        directory.mkdir(parents=True)
        atomic_json(directory / "settings.json", {"enabledPlugins": {"helper@catalog": True}})
    child_home, daemon_home = tmp_path / "child-home", tmp_path / "daemon-home"
    for home, namespace in ((child_home, "catalog"), (child_home, "other"), (daemon_home, "catalog")):
        payload = home / "installed-plugins" / namespace / "helper"
        payload.mkdir(parents=True)
        atomic_json(payload / "plugin.json", {"name": "helper"})
    monkeypatch.setenv("COPILOT_HOME", str(daemon_home))
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    target = target_for(root, controller)
    target.env = {"COPILOT_HOME": str(child_home), "COPILOT_CACHE_HOME": str(tmp_path / "child-cache")}
    selected = str(child_home / "installed-plugins" / "catalog" / "helper")
    for _ in range(2):
        args, _, _ = await transport.resolve_local_launch(target)
        assert selected in args
        assert not any(str(daemon_home) in arg for arg in args)
        assert not any(str(child_home / "installed-plugins" / "other") in arg for arg in args)
        settings = json.loads((root / ".github" / "copilot" / "settings.local.json").read_text())
        assert settings["enabledPlugins"]["helper@catalog"] is True
        assert settings["extraKnownMarketplaces"] == {}
    assert not list(child_home.rglob("marketplace.json"))


@pytest.mark.asyncio
async def test_mixed_physical_reference_origins_reach_local_acp(
    tmp_path, isolated_selection,
):
    root, controller = checkout(tmp_path / "target"), tmp_path / "controller"
    host = marketplace(controller)
    native = root / ".ai" / "native"
    native.mkdir(parents=True)
    atomic_json(native / "plugin.json", {"name": "native", "version": "1.0.0"})
    atomic_json(native.parent / "marketplace.json", {
        "name": "local", "owner": {"name": "Synthetic"},
        "plugins": [{"name": "native", "source": "native"}],
    })
    directory = root / ".github" / "copilot"
    directory.mkdir(parents=True)
    atomic_json(directory / "settings.json", {
        "enabledPlugins": {"native@local": True},
        "extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": "./.ai"}}},
    })
    target = target_for(root, controller)
    for _ in range(2):
        args, _, _ = await transport.resolve_local_launch(target)
        assert str(host) in args and str(native) in args
    assert not list((root / ".git").rglob("marketplace.json"))


@pytest.mark.asyncio
async def test_local_producer_rebinds_only_proven_target_owned_absolute_catalog(
    tmp_path, isolated_selection,
):
    anchor = checkout(tmp_path / "anchor")
    callee, controller = tmp_path / "callee", tmp_path / "controller"
    original = marketplace(anchor, "anchor")
    subprocess.run(
        ["git", "-C", str(anchor), "worktree", "add", "--orphan", str(callee)],
        check=True, capture_output=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    target_payload = marketplace(callee, "actual-target")
    (callee / ".github" / "copilot" / "settings.json").unlink()
    marketplace(controller)
    path = controller / ".github" / "copilot" / "settings.json"
    data = json.loads(path.read_text())
    data["extraKnownMarketplaces"]["local"]["source"]["path"] = str(original.parent)
    atomic_json(path, data)
    args, cwd, _environment = await transport.resolve_local_launch(target_for(callee, controller))
    assert cwd == str(callee)
    assert str(target_payload) in args and str(original) not in args
    settings = json.loads((callee / ".github" / "copilot" / "settings.local.json").read_text())
    assert settings["extraKnownMarketplaces"]["local"]["source"]["path"] == "./.ai"
