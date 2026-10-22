"""Static SSH's existing owner prepares before opening productive ACP stdio."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from agent_procutil import no_window_flags

from agent_bridge import ssh_plugin_preparation, transport
from agent_bridge.connect import ConnectError, ConnectStage
from agent_bridge.spawn_target import PluginRef, SpawnTarget


@pytest.mark.parametrize("privileged", [False, True])
def test_static_ssh_resolver_retains_preparation_provenance_without_new_fields(
    monkeypatch, privileged,
):
    from agent_bridge.agent_registry import AgentResolver, parse_agent_registry
    from agent_bridge.topology import parse_machines_yaml

    monkeypatch.setattr("socket.gethostname", lambda: "controller")
    machines = parse_machines_yaml({"machines": {"receiver": {
        "ssh": {"ready": True, "environments": [
            {"name": "linux", "alias": "receiver-profile", "shell": "bash"},
        ]},
    }}})
    agents = parse_agent_registry({"worker": {
        "host": "receiver", "project": "app", "requires_admin": privileged,
        "copilot_args": ["--plugin-dir", "configured"],
    }})
    target = AgentResolver(agents, machines).resolve("worker")
    assert target.type == "ssh" and target.host == "receiver-profile"
    expected = None if privileged else {
        "base": ["--plugin-dir", "configured"], "automatic": [],
    }
    assert target.static_plugin_args == expected
    restored = SpawnTarget.from_json(target.to_json())
    assert restored.static_plugin_args == expected
    assert restored.local_plugin_preparation is False
    assert "static_plugin_args" not in json.loads(target.to_json())


def _checkout(path: Path, *, plugin: bool = False, enabled: bool = True):
    subprocess.run(
        ["git", "init", "--quiet", str(path)], check=True, capture_output=True,
        timeout=20, creationflags=no_window_flags(),
    )
    if not plugin:
        return
    payload = path / ".ai" / "sample"
    payload.mkdir(parents=True)
    (payload / "plugin.json").write_text(json.dumps({"name": "sample", "version": "1.0.0"}))
    (payload / "marker").write_text(path.name)
    (payload.parent / "marketplace.json").write_text(json.dumps({
        "name": "local", "owner": {"name": "Synthetic"},
        "plugins": [{"name": "sample", "source": "./sample"}],
    }))
    settings = path / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": "./.ai"}}},
        "enabledPlugins": {"sample@local": enabled},
    }))


class _Receiver:
    """Executes the actual emitted stdin program, not an installed Bridge."""

    def __init__(self, directory: Path, environment: dict[str, str]):
        self.directory = directory
        self.environment = environment
        self.calls = []

    async def exec_command(self, host, command, *, input_bytes, timeout):
        self.calls.append((host, command, input_bytes))
        args = command.split(" ")
        executable = shutil.which(args[0])
        if executable is None:
            pytest.skip(f"Receiver shell/interpreter unavailable: {args[0]}")
        args[0] = executable
        result = await asyncio.to_thread(
            subprocess.run, args, input=input_bytes, cwd=self.directory, env=self.environment,
            capture_output=True, timeout=timeout, creationflags=no_window_flags(),
        )
        return SimpleNamespace(
            stdout=result.stdout.decode("utf-8"), stderr=result.stderr.decode("utf-8"),
            exit_code=result.returncode, timed_out=False,
        )


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    controller, child, home, daemon = [tmp_path / name for name in ("controller", "child", "home", "daemon")]
    _checkout(controller, plugin=True)
    _checkout(child)
    home.mkdir()
    daemon.mkdir()
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    monkeypatch.setattr(
        "agent_bridge.related_plugins.related_plugins_for_repo",
        lambda *a, **kw: [PluginRef("sample@local")],
    )
    environment = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_HOME": str(home / ".copilot"),
    }
    target = SpawnTarget(
        type="ssh", host="synthetic-profile", cwd=str(child), project="app", worktree_id="allocated",
        ssh_shell="powershell" if os.name == "nt" else "bash",
        caller_worktree=str(controller),
        static_plugin_args={"base": ["--model", "selected"], "automatic": []},
        copilot_args=["--model", "selected", "--plugin-dir", "explicit-request"],
        env={"UNCHANGED": "request-value"},
    )
    return target, _Receiver(daemon, environment), controller, child, home


@pytest.mark.asyncio
@pytest.mark.parametrize("native", ["absent", "enabled", "disabled", "missing"])
async def test_streamed_receiver_preserves_native_authority_and_global_preferences(receiver, native):
    target, manager, controller, child, home = receiver
    # Incompressible fixture data proves the payload is not hidden in SSH argv.
    (controller / ".ai" / "sample" / "asset").write_bytes(random.Random(42).randbytes(90_000))
    preferences = home / ".copilot" / "settings.json"
    preferences.parent.mkdir()
    preferences.write_text('{"enabledPlugins":{"unrelated@elsewhere":true}}')
    before = preferences.read_bytes()
    if native != "absent":
        _checkout(child, plugin=True, enabled=native != "disabled")
    if native == "missing":
        (child / ".ai" / "sample" / "plugin.json").unlink()
        with pytest.raises(RuntimeError, match="Required SSH plugin preparation failed"):
            await ssh_plugin_preparation.prepare_ssh_plugins(target, manager, environment=target.env)
        assert target.prepared_plugins is None
    else:
        result = await ssh_plugin_preparation.prepare_ssh_plugins(target, manager, environment=target.env)
        context = target.prepared_plugins
        assert context["cwd"] == str(child)
        if native == "disabled":
            assert result == [] and context["plugins"] == [] and context["logical_payloads"] == {}
        else:
            payload = Path(context["logical_payloads"]["sample@local"])
            assert result == ["--plugin-dir", str(payload)]
            assert (payload / "marker").read_text() == ("child" if native == "enabled" else "controller")
            if native == "enabled":
                assert payload == child / ".ai" / "sample"
            else:
                assert payload != controller / ".ai" / "sample"
                assert (payload / "asset").read_bytes() == (controller / ".ai" / "sample" / "asset").read_bytes()
        assert (child / ".github" / "copilot" / "settings.local.json").is_file()
    assert preferences.read_bytes() == before
    assert len(manager.calls) == 2
    assert all(len(command) < 8192 for _host, command, _stdin in manager.calls)
    assert len(manager.calls[-1][2]) > 32_768
    assert target.env == {"UNCHANGED": "request-value"}


@pytest.mark.asyncio
@pytest.mark.parametrize("matching", [False, True])
async def test_ssh_planner_verifies_session_qualified_controller_owner(receiver, monkeypatch, matching):
    from agent_bridge.spawn_target import REQUEST_OVERRIDES_KEY

    target, manager, controller, _child, _home = receiver
    session = "11111111-2222-3333-4444-555555555555"
    target.caller_owner_ref = f"local/control/controller#{session}"
    target.venue = {REQUEST_OVERRIDES_KEY: {
        "controller_worktree_origin": "explicit",
        "controller_session_id": session if matching else "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    }}
    owner = AsyncMock(return_value={"worktrees": [{
        "id": "controller", "repo": "control", "machine": "local",
        "path": str(controller), "status": "active",
    }]})
    monkeypatch.setattr("agent_bridge.acquisition_authority.worktree_command", owner)
    if matching:
        assert await ssh_plugin_preparation.prepare_ssh_plugins(target, manager, environment={})
        assert target.prepared_plugins["plugins"][0]["projected_source"] == "sample@local"
        owner.assert_awaited_once()
    else:
        with pytest.raises(RuntimeError, match="controller context could not be verified"):
            await ssh_plugin_preparation.prepare_ssh_plugins(target, manager, environment={})
        assert target.prepared_plugins is None and len(manager.calls) == 1


@pytest.mark.asyncio
async def test_gitless_legacy_receiver_skips_projection_before_selecting_controller_plugins(receiver, monkeypatch):
    target, manager, _controller, child, _home = receiver
    target.cwd = str(child.parent)
    planner = AsyncMock(side_effect=AssertionError("Gitless launches have no registration surface"))
    monkeypatch.setattr(ssh_plugin_preparation, "venue_plugin_plan", planner)
    assert await ssh_plugin_preparation.prepare_ssh_plugins(target, manager, environment={}) is None
    planner.assert_not_awaited()
    assert len(manager.calls) == 1
    assert target.prepared_plugins["applicable"] is False


@pytest.mark.asyncio
async def test_relative_legacy_directory_resolves_in_receiver_not_controller(receiver):
    target, manager, _controller, _child, _home = receiver
    target.cwd = "."
    assert await ssh_plugin_preparation.prepare_ssh_plugins(target, manager, environment={}) is None
    assert target.cwd == str(manager.directory)


@pytest.mark.asyncio
async def test_receiver_wrapper_drains_large_stderr_without_blocking_stdout(receiver):
    target, manager, _controller, _child, _home = receiver
    output = await ssh_plugin_preparation._run_python(
        manager, target,
        "import sys,os,ctypes\n"
        "if os.name=='nt': assert ctypes.windll.kernel32.GetConsoleWindow()==0\n"
        "sys.stderr.write('diagnostic'*50000)\nprint('receiver-complete')\n",
    )
    assert output.strip() == "receiver-complete"


@pytest.mark.asyncio
async def test_receiver_directory_with_spaces_and_non_ascii_is_preserved(receiver):
    target, manager, _controller, child, _home = receiver
    renamed = child.with_name("child space " + chr(0x3C0))
    child.rename(renamed)
    target.cwd = str(renamed)
    await ssh_plugin_preparation.prepare_ssh_plugins(target, manager, environment={})
    assert target.prepared_plugins["cwd"] == str(renamed)
    assert (renamed / ".github" / "copilot" / "settings.local.json").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", [
    "",
    "__VENUE_CHECKOUT__{}",
    '__VENUE_CHECKOUT__{"requested_cwd":"/different","cwd":"/different","applicable":true}',
])
async def test_wrong_or_missing_receiver_checkout_frame_fails_closed(receiver, proof):
    target, manager, _controller, _child, _home = receiver
    manager.exec_command = AsyncMock(return_value=SimpleNamespace(
        exit_code=0, timed_out=False, stdout=proof, stderr="",
    ))
    with pytest.raises((RuntimeError, ValueError), match="checkout"):
        await ssh_plugin_preparation.prepare_ssh_plugins(target, manager, environment={})
    assert target.prepared_plugins is None


@pytest.mark.asyncio
@pytest.mark.parametrize("prebound", [False, True])
async def test_spawn_prepares_after_allocation_before_existing_productive_command(
    receiver, monkeypatch, prebound,
):
    target, manager, _controller, child, _home = receiver
    target.worktree_id = "existing-child" if prebound else None
    target.cwd = "/stale/configured-checkout"
    manager.ensure_connected = AsyncMock()
    resolved = AsyncMock(return_value={"launch": {
        "worktree_id": "actual-child", "work_dir": str(child),
        "env": {"FROM_PLAN": "selected-profile"},
    }})
    monkeypatch.setattr(transport, "get_default_manager", lambda: manager)
    monkeypatch.setattr(transport, "_resolve_worktree_remote", resolved)
    monkeypatch.setattr(transport, "_effective_auth_hooks", lambda hooks: hooks)
    preparation = AsyncMock(wraps=ssh_plugin_preparation.prepare_ssh_plugins)
    monkeypatch.setattr(ssh_plugin_preparation, "prepare_ssh_plugins", preparation)

    async def open_stdio(host, command):
        assert target.cwd == str(child) and target.worktree_id == "actual-child"
        assert target.prepared_plugins["plugins"][0]["projected_source"] == "sample@local"
        assert (child / ".github" / "copilot" / "settings.local.json").is_file()
        return SimpleNamespace(pid=999)

    manager.open_stdio_channel = AsyncMock(side_effect=open_stdio)
    original = list(target.copilot_args)
    process = await transport.spawn_ssh(target)
    assert process.target is target and target.copilot_args == original
    assert target.env == {"UNCHANGED": "request-value"}
    source = manager.ensure_connected.await_args.args[1]
    assert isinstance(source, transport.SSHProfileSource)
    assert manager.ensure_connected.await_args.args[0] == "synthetic-profile"
    # ACP still launches through the selected project owner, never a new launcher.
    command = manager.open_stdio_channel.await_args.args[1]
    if os.name == "nt":
        import base64
        command = base64.b64decode(command.rsplit(" ", 1)[1]).decode("utf-16-le")
    assert "app" in command and "--worktree-id actual-child" in command
    assert "--model selected" in command and "explicit-request" in command
    assert command.count("--plugin-dir") == 2
    assert preparation.await_args.kwargs["environment"] == {
        "UNCHANGED": "request-value", "FROM_PLAN": "selected-profile",
    }


@pytest.mark.asyncio
async def test_nested_execution_cwd_keeps_native_repository_root_and_productive_directory(
    receiver, monkeypatch,
):
    target, manager, _controller, child, _home = receiver
    _checkout(child, plugin=True)
    nested = child / "tools" / "nested execution"
    nested.mkdir(parents=True)
    target.cwd = "/stale/configured-checkout"
    target.worktree_id = None
    manager.ensure_connected = AsyncMock()
    monkeypatch.setattr(transport, "get_default_manager", lambda: manager)
    monkeypatch.setattr(transport, "_effective_auth_hooks", lambda hooks: hooks)
    monkeypatch.setattr(transport, "_resolve_worktree_remote", AsyncMock(return_value={"launch": {
        "worktree_id": "actual-child", "work_dir": str(nested),
    }}))

    async def open_stdio(host, command):
        assert target.cwd == str(nested)
        context = target.prepared_plugins
        assert context["cwd"] == str(nested) and context["repository_root"] == str(child)
        assert context["logical_payloads"] == {"sample@local": str(child / ".ai" / "sample")}
        assert (child / ".github" / "copilot" / "settings.local.json").is_file()
        assert not (nested / ".github").exists()
        return SimpleNamespace(pid=999)

    manager.open_stdio_channel = AsyncMock(side_effect=open_stdio)
    process = await transport.spawn_ssh(target)
    assert process.target.cwd == str(nested)
    manager.open_stdio_channel.assert_awaited_once()


@pytest.mark.asyncio
async def test_applicable_receiver_requires_a_verified_repository_root(receiver):
    target, manager, _controller, _child, _home = receiver
    manager.exec_command = AsyncMock(return_value=SimpleNamespace(
        exit_code=0, timed_out=False, stderr="", stdout="__VENUE_CHECKOUT__" + json.dumps({
            "requested_cwd": target.cwd, "cwd": target.cwd, "applicable": True,
        }),
    ))
    with pytest.raises(ValueError, match="verified repository root"):
        await ssh_plugin_preparation.prepare_ssh_plugins(target, manager, environment={})
    assert target.prepared_plugins is None


@pytest.mark.skipif(os.name != "nt", reason="Windows quoted execution PATH")
@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.asyncio
async def test_receiver_git_lookup_uses_the_shared_execution_path_contract(receiver, relative):
    target, manager, _controller, child, _home = receiver
    git = Path(shutil.which("git")).resolve()
    if relative and os.path.splitdrive(str(git))[0].casefold() != os.path.splitdrive(str(child))[0].casefold():
        pytest.skip("A relative Git PATH requires the fixture and Git on the same drive")
    directory = os.path.relpath(git.parent, child) if relative else str(git.parent)
    environment = {"PATH": '"' + directory + '"', "PATHEXT": ".EXE"}
    result = await ssh_plugin_preparation.prepare_ssh_plugins(target, manager, environment=environment)
    assert result and target.prepared_plugins["repository_root"] == str(child)
    assert environment == {"PATH": '"' + directory + '"', "PATHEXT": ".EXE"}


@pytest.mark.asyncio
async def test_receiver_repository_probe_ignores_foreign_git_selectors(receiver):
    target, manager, controller, child, _home = receiver
    environment = {
        "GIT_DIR": str(controller / ".git"), "GIT_WORK_TREE": str(controller),
    }
    assert await ssh_plugin_preparation.prepare_ssh_plugins(target, manager, environment=environment)
    assert target.prepared_plugins["repository_root"] == str(child)
    assert (child / ".github" / "copilot" / "settings.local.json").is_file()
    assert not (controller / ".github" / "copilot" / "settings.local.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exit", "timeout", "cancel"])
async def test_preparation_failure_never_opens_productive_stdio(receiver, monkeypatch, failure):
    target, manager, _controller, _child, _home = receiver
    target.explicit_cwd = True
    manager.ensure_connected = AsyncMock()
    manager.open_stdio_channel = AsyncMock()
    if failure == "cancel":
        manager.exec_command = AsyncMock(side_effect=asyncio.CancelledError)
    else:
        manager.exec_command = AsyncMock(return_value=SimpleNamespace(
            exit_code=3 if failure == "exit" else 0, timed_out=failure == "timeout",
            stderr="receiver preparation failed", stdout="",
        ))
    monkeypatch.setattr(transport, "get_default_manager", lambda: manager)
    monkeypatch.setattr(transport, "_effective_auth_hooks", lambda hooks: hooks)
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ConnectError) as error:
        await transport.spawn_ssh(target)
    if failure != "cancel":
        assert error.value.stage is ConnectStage.TARGET_BINSTUB
    manager.open_stdio_channel.assert_not_awaited()
    assert target.prepared_plugins is None


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "cmd"])
def test_windows_helper_uses_hidden_streamed_python_and_drains_both_pipes(shell):
    import base64
    command = ssh_plugin_preparation._python_command(shell)
    script = base64.b64decode(command.rsplit(" ", 1)[1]).decode("utf-16-le")
    assert "CreateNoWindow = $true" in script and "UseShellExecute = $false" in script
    assert script.count("ReadToEndAsync()") == 2
    assert script.index("ReadToEndAsync()") < script.index("WaitForExit()")
    assert "[Console]::In.ReadToEnd()" in script and "\\WindowsApps\\" in script


@pytest_asyncio.fixture
async def live_receiver(receiver):
    from ssh_manager import ConnectionManager, SSHProfileSource

    host = os.environ.get("AGENT_BRIDGE_TEST_SAME_FILESYSTEM_SSH")
    if not host:
        pytest.skip("Opt-in SSH profile with the test runner's filesystem required")
    target, _manager, _controller, _child, home = receiver
    target.host = host
    target.ssh_shell = "pwsh" if os.name == "nt" else "bash"
    target.env.update({
        "HOME": str(home), "USERPROFILE": str(home), "COPILOT_HOME": str(home / ".copilot"),
    })
    manager = ConnectionManager()
    try:
        await asyncio.wait_for(
            manager.ensure_connected(host, SSHProfileSource(host_alias=host)), timeout=40,
        )
        # Refuse a configured host that does not share this synthetic fixture.
        await ssh_plugin_preparation._run_python(
            manager, target,
            f"from pathlib import Path\nassert Path({str(home)!r}).is_dir()\n",
        )
        yield target, manager
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_live_ssh_profile_streams_shared_engine_without_an_installed_bridge(live_receiver):
    target, manager = live_receiver
    directories = await ssh_plugin_preparation.prepare_ssh_plugins(
        target, manager, environment=target.env,
    )
    assert len(directories) == 2 and directories[0] == "--plugin-dir"
    assert (Path(directories[1]) / "marker").read_text() == "controller"
    assert target.prepared_plugins["plugins"][0]["projected_source"] == "sample@local"


def _mutating_receiver(heartbeat, started, *, early_exit=None, exit_delay=0):
    child = (
        "import pathlib,time\n"
        f"path=pathlib.Path({str(heartbeat)!r})\n"
        "deadline=time.monotonic()+6\n"
        "while time.monotonic()<deadline:\n"
        "    path.write_text(str(time.monotonic()))\n"
        "    time.sleep(.05)\n"
    )
    return (
        "import pathlib,subprocess,sys,time\n"
        f"child=subprocess.Popen([sys.executable,'-u','-c',{child!r}],\n"
        "    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,\n"
        "    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
        f"pathlib.Path({str(started)!r}).write_text(str(child.pid))\n"
        + (
            "child.wait(timeout=10)\n" if early_exit is None else
            "deadline=time.monotonic()+3\n"
            f"while not pathlib.Path({str(heartbeat)!r}).is_file() and time.monotonic()<deadline:\n"
            "    time.sleep(.02)\n"
            f"time.sleep({exit_delay})\n"
            f"raise SystemExit({early_exit})\n"
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [0, 3])
async def test_receiver_early_exit_reaps_mutating_descendants(receiver, tmp_path, exit_code):
    target, manager, _controller, _child, _home = receiver
    heartbeat, started = tmp_path / "heartbeat", tmp_path / "started"
    before = time.monotonic()
    try:
        operation = ssh_plugin_preparation._run_python(
            manager, target, _mutating_receiver(heartbeat, started, early_exit=exit_code), timeout=3,
        )
        if exit_code:
            with pytest.raises(RuntimeError, match="exit 3"):
                await operation
        else:
            assert await operation == ""
        assert started.is_file() and heartbeat.is_file()
        settled = heartbeat.read_bytes()
        await asyncio.sleep(.3)
        assert heartbeat.read_bytes() == settled
    finally:
        await asyncio.sleep(max(0, 7 - (time.monotonic() - before)))


@pytest.mark.asyncio
@pytest.mark.parametrize("active_past_deadline", [False, True])
async def test_lost_supervisor_does_not_orphan_descendants(tmp_path, active_past_deadline):
    from plugin_resolve import receiver as ssh_plugin_receiver

    heartbeat, started = tmp_path / "heartbeat", tmp_path / "started"
    program = (
        _mutating_receiver(heartbeat, started) if active_past_deadline else
        _mutating_receiver(heartbeat, started, early_exit=0, exit_delay=.5)
    )
    timeout = 1 if active_past_deadline else 3
    encoded = base64.b64encode(program.encode()).decode()
    script = tmp_path / "supervisor.py"
    script.write_text(
        Path(ssh_plugin_receiver.__file__).read_text(encoding="utf-8")
        + f"\nsupervise({encoded!r},{timeout})\n",
        encoding="utf-8",
    )
    before = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-I", "-S", "-X", "utf8", str(script)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=no_window_flags(),
    )
    try:
        deadline = before + 5
        while not heartbeat.exists():
            assert process.poll() is None, "Receiver exited before its child started"
            assert time.monotonic() < deadline, "Receiver child did not start"
            await asyncio.sleep(.02)
        process.kill()
        await asyncio.to_thread(process.communicate, timeout=10)
        await asyncio.sleep(1.5 if active_past_deadline else 1)
        settled = heartbeat.read_bytes()
        await asyncio.sleep(.3)
        assert heartbeat.read_bytes() == settled
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.communicate, timeout=10)
        await asyncio.sleep(max(0, 7 - (time.monotonic() - before)))


@pytest.mark.asyncio
async def test_receiver_deadline_reaps_installer_descendants(receiver, tmp_path):
    target, manager, _controller, _child, _home = receiver
    heartbeat, started = tmp_path / "heartbeat", tmp_path / "started"
    before = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="receiver preparation timed out"):
            await ssh_plugin_preparation._run_python(
                manager, target, _mutating_receiver(heartbeat, started), timeout=3,
            )
        assert started.is_file() and heartbeat.is_file()
        settled = heartbeat.read_bytes()
        await asyncio.sleep(.3)
        assert heartbeat.read_bytes() == settled
    finally:
        await asyncio.sleep(max(0, 7 - (time.monotonic() - before)))


@pytest.mark.asyncio
async def test_live_ssh_cancellation_quiesces_receiver_and_its_child(live_receiver, tmp_path):
    target, manager = live_receiver
    heartbeat = tmp_path / "receiver-child-heartbeat"
    started = tmp_path / "receiver-started"
    before = time.monotonic()
    task = asyncio.create_task(ssh_plugin_preparation._run_python(
        manager, target, _mutating_receiver(heartbeat, started),
    ))
    try:
        deadline = before + 10
        while not heartbeat.exists():
            if task.done():
                await task
                pytest.fail("Receiver returned without starting its synthetic child")
            if time.monotonic() >= deadline:
                pytest.fail("Receiver did not start its synthetic child")
            await asyncio.sleep(.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(.5)
        settled = heartbeat.read_bytes()
        await asyncio.sleep(.5)
        assert heartbeat.read_bytes() == settled, "Cancelled SSH left a mutating receiver child"
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        # Even a failed cancellation control leaves no unbounded probe process.
        await asyncio.sleep(max(0, 7 - (time.monotonic() - before)))
