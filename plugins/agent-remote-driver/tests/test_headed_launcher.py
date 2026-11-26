"""Coordinator custody and parser controls, independent of native Copilot."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent_remote_driver.headed import launcher
from agent_remote_driver.headed import receipt
from agent_remote_driver.headed import caller
from agent_remote_driver.headed.staging import CONNECTOR_ENV, connector_selection
from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host import launcher as host_launcher
from agent_remote_driver.session_host.host import SessionHost
from agent_remote_driver.session_host.startup_admission import CONTEXT_ENV


@pytest.mark.parametrize("change", ["matching", "missing", "duplicate", "wrong-root", "positional", "value", "extra"])
def test_selected_connector_binds_facade_roots_to_the_original_native_command(
    tmp_path: Path, change: str,
) -> None:
    roots = {}
    for name in ("agent-bridge", "agent-remote-driver", "other"):
        root = tmp_path / name
        root.mkdir()
        (root / "plugin.json").write_text(json.dumps({
            "name": name if name != "other" else "agent-bridge",
        }), encoding="utf-8")
        roots[name] = str(root)
    selected = {
        "contract": "driver-native-v1", "version": 1, "sessionId": "chosen",
        "cwd": str(tmp_path), "scopeId": "scope", "attempt": "attempt",
        "bridgePluginRoot": roots["agent-bridge"], "driverPluginRoot": roots["agent-remote-driver"],
    }
    env = {
        CONNECTOR_ENV: json.dumps(selected), "AGENT_BRIDGE_NATIVE_SESSION_ID": "chosen",
        "AGENT_BRIDGE_SCOPE_ID": "scope", "AGENT_BRIDGE_PORTABLE_ATTEMPT": "attempt",
        "AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT": roots["agent-bridge"],
        "AGENT_BRIDGE_REMOTE_DRIVER_ROOT": roots["agent-remote-driver"],
    }
    driver = ["--plugin-dir=" + roots["agent-remote-driver"]]
    bridge = ["--plugin-dir", roots["agent-bridge"]]
    argv = ["copilot", "--session-id", "chosen", *driver, *bridge]
    if change == "missing":
        argv = argv[:-2]
    elif change == "duplicate":
        argv += bridge
    elif change == "wrong-root":
        argv[-1] = roots["other"]
    elif change == "positional":
        argv = ["copilot", "--session-id", "chosen", *driver, "--", *bridge]
    elif change == "value":
        argv = ["copilot", "--session-id", "chosen", *driver, "--interactive", "--plugin-dir", roots["agent-bridge"]]
    elif change == "extra":
        argv += ["--plugin-dir", roots["other"]]
    if change == "matching":
        assert connector_selection(env, session_id="chosen", cwd=str(tmp_path)) == selected
        launcher.selected_connector_roots(selected, argv, value_options=launcher.VALUE_OPTIONS)
    else:
        with pytest.raises(ValueError):
            launcher.selected_connector_roots(selected, argv, value_options=launcher.VALUE_OPTIONS)


@pytest.mark.asyncio
async def test_selected_root_mismatch_refuses_before_staging_or_native_birth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = {
        "contract": "driver-native-v1", "version": 1, "sessionId": "chosen",
        "cwd": str(tmp_path.resolve()), "scopeId": "scope", "attempt": "attempt",
        "bridgePluginRoot": str(tmp_path), "driverPluginRoot": str(tmp_path),
    }
    env = {
        CONNECTOR_ENV: json.dumps(selected), "AGENT_BRIDGE_NATIVE_SESSION_ID": "chosen",
        "AGENT_BRIDGE_SCOPE_ID": "scope", "AGENT_BRIDGE_PORTABLE_ATTEMPT": "attempt",
        "AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT": str(tmp_path),
        "AGENT_BRIDGE_REMOTE_DRIVER_ROOT": str(tmp_path),
    }
    stage, spawn = Mock(), Mock()
    monkeypatch.setattr(launcher, "_require_receiving_policy", Mock())
    monkeypatch.setattr(launcher, "stage_extension", stage)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    with pytest.raises(ValueError, match="missing from the original command"):
        await launcher.run_headed(
            ["copilot", "--session-id", "chosen"], session_id="chosen", env=env,
            cwd=str(tmp_path), attempt_directory=tmp_path / "attempt", state_file=tmp_path / "state.json",
        )
    stage.assert_not_called()
    spawn.assert_not_called()
    assert not (tmp_path / "attempt").exists()


@pytest.fixture
def headed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, Any], dict[int, str]]:
    births = {101: "host-birth", 202: "native-birth"}
    monkeypatch.setattr(receipt, "process_start_time", births.get)
    monkeypatch.setattr(receipt, "pid_alive", lambda pid: pid in births)
    state = {
        "version": 2, "mode": "headed", "process_receipt": receipt.PROCESS_RECEIPT,
        "state": "native_starting", "session_id": "chosen", "nonce": "a" * 64,
        "pid": 101, "host_pid": 101, "host_process_start_time": "host-birth",
        "child_pid": 202, "child_process_start_time": "native-birth",
    }
    return tmp_path / "host.json", state, births


def _read_receipt(path: Path) -> tuple[int, str]:
    return receipt.native_child(
        path, coordinator_pid=101, coordinator_birth="host-birth",
        session_id="chosen", nonce="a" * 64,
    )


@pytest.mark.parametrize("phase", ["native_starting", "running", "control_unavailable"])
def test_original_caller_receipt_uses_native_not_coordinator_identity(
    headed_receipt: tuple[Path, dict[str, Any], dict[int, str]], phase: str,
) -> None:
    path, state, _births = headed_receipt
    state["state"] = phase
    state["future_additive_field"] = True
    path.write_text(json.dumps(state), encoding="utf-8")
    assert _read_receipt(path) == (202, "native-birth")


@pytest.mark.parametrize("key,value", [
    ("version", True), ("mode", "headless"), ("process_receipt", None),
    ("state", "child_exited"), ("session_id", "other"), ("nonce", "b" * 64),
    ("pid", 303), ("host_pid", 303), ("host_process_start_time", "old-host"),
    ("child_pid", 101), ("child_pid", True), ("child_process_start_time", None),
])
def test_changed_or_unsupported_receipt_never_becomes_a_native_pid(
    headed_receipt: tuple[Path, dict[str, Any], dict[int, str]], key: str, value: object,
) -> None:
    path, state, _births = headed_receipt
    state[key] = value
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="unconfirmed"):
        _read_receipt(path)


@pytest.mark.parametrize("pid", [101, 202])
def test_reused_or_unobservable_process_keeps_receipt_uncertain(
    headed_receipt: tuple[Path, dict[str, Any], dict[int, str]], pid: int,
) -> None:
    path, state, births = headed_receipt
    path.write_text(json.dumps(state), encoding="utf-8")
    births[pid] = "replacement"
    with pytest.raises(ValueError, match="identity changed"):
        _read_receipt(path)


def test_duplicate_and_oversized_receipts_are_not_accepted(
    headed_receipt: tuple[Path, dict[str, Any], dict[int, str]],
) -> None:
    path, state, _births = headed_receipt
    path.write_text(json.dumps(state)[:-1] + ', "child_pid": 303}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        _read_receipt(path)
    path.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="exceeds its bound"):
        _read_receipt(path)


@pytest.mark.parametrize("outcome", ["bound", "admission-refused", "receipt-refused", "wait-lost"])
def test_original_owner_waits_after_coordinator_creation_without_replay(
    headed_receipt: tuple[Path, dict[str, Any], dict[int, str]],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    path, state, _births = headed_receipt
    actions: list[str] = []
    argv = ["copilot", "--session-id", "chosen", "-i", "one literal\nseed"]
    monkeypatch.setattr(caller, "prepare_headed_command", lambda *a, **k: ["retained-core"])
    monkeypatch.setattr(caller, "process_start_time", lambda _pid: "host-birth")

    class Process:
        pid = 101

        def poll(self) -> None:
            return None

        def wait(self) -> int:
            actions.append("wait")
            if outcome == "wait-lost":
                raise OSError("fixture lost wait handle")
            return 17

    def create(command: list[str], **options: Any) -> Process:
        assert command == ["retained-core"]
        actions.append("create")
        state["nonce"] = options["env"][receipt.NONCE_ENV]
        path.write_text(json.dumps(state), encoding="utf-8")
        return Process()

    class Admission:
        context = "original-owner-channel"

        def __init__(self, check: Any, **_options: Any) -> None:
            self.check = check

        def start(self, process: Process) -> None:
            assert process.pid == 101
            self.check({"argv": argv, "cwd": str(tmp_path), "session_id": "chosen"})

        def close(self) -> None:
            pass

    def admit() -> None:
        actions.append("admit")
        if outcome == "admission-refused":
            raise ValueError("fixture changed launch owner")

    def spawned(pid: int, birth: str) -> None:
        assert (pid, birth) == (202, "native-birth")
        actions.append("native-receipt")
        if outcome == "receipt-refused":
            raise ValueError("fixture receipt writer unavailable")

    monkeypatch.setattr(subprocess, "Popen", create)
    monkeypatch.setattr(caller, "StartupAdmissionServer", Admission)
    options: dict[str, Any] = dict(
        session_id="chosen", attempt_directory=tmp_path / "attempt", state_file=path,
        cwd=str(tmp_path), environment={}, admit=admit, spawned=spawned, interactive=False,
    )
    if outcome == "wait-lost":
        with pytest.raises(ValueError, match="retain the original handoff"):
            caller.execute(argv, **options)
    else:
        assert caller.execute(argv, **options) == (17, outcome == "bound")
    assert actions[:2] == ["create", "admit"]
    assert actions[-1] == "wait"
    assert actions.count("create") == 1
    assert ("native-receipt" in actions) == (outcome != "admission-refused")


@pytest.mark.parametrize("arguments", [
    ["native", "--session-id", "other"],
    ["native", "--session-id", "chosen", "--resume", "chosen"],
    ["native", "--session-id", "chosen", "-i", "one", "-i", "two"],
    ["native", "--session-id", "chosen", "--acp"],
    ["native", "--session-id", "chosen", "--ahp=http://127.0.0.1:3210"],
    ["native", "--session-id", "chosen", "--connect"],
    ["native", "--session-id", "chosen", "-i"],
    ["native", "--session-id", "chosen", "--log-dir"],
])
@pytest.mark.asyncio
async def test_bad_native_selection_is_refused_before_any_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str],
) -> None:
    policy = Mock(side_effect=AssertionError("must refuse before policy or staging"))
    monkeypatch.setattr(launcher, "_require_receiving_policy", policy)
    with pytest.raises(ValueError):
        await launcher.run_headed(
            arguments, session_id="chosen", attempt_directory=tmp_path / "attempt",
            state_file=tmp_path / "host.json",
        )
    policy.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("option,value", [
    ("--log-dir", "--resume=literal"), ("--model", "--session-id=literal"),
    ("--agent", "--ahp=literal"), ("-C", "--interactive=literal"),
])
def test_native_validator_preserves_selector_shaped_option_values(option: str, value: str) -> None:
    argv = ["native", "--session-id", "chosen", option, value]
    assert launcher._native_request(argv, "chosen") is None
    assert launcher._native_request([*argv, "-i", "real seed"], "chosen") == "real seed"
    with pytest.raises(ValueError, match="interactive native arguments"):
        launcher._native_request([*argv, "--acp"], "chosen")


def test_headless_parser_does_not_accept_cli_lifetime_or_headed_only_options() -> None:
    for options in (["--lifetime-policy", "cli"], ["--attempt-directory", "unused"]):
        with pytest.raises(SystemExit) as result:
            host_launcher.main([*options, "--", "must-not-start"])
        assert result.value.code == 2


@pytest.mark.parametrize("native_exit,system,expected", [
    (19, os.name, 19), (-2, "posix", 130),
])
def test_headed_terminal_interrupt_defers_to_native_without_cancelling_the_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    native_exit: int, system: str, expected: int,
) -> None:
    observed: list[str] = []
    previous = signal.getsignal(signal.SIGINT)

    async def native_owner(*_args: Any, **_kwargs: Any) -> int:
        assert signal.getsignal(signal.SIGINT) != signal.SIG_IGN
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(0)
        observed.append("native-owner-still-running")
        return native_exit

    monkeypatch.setattr(launcher, "run_headed", native_owner)
    monkeypatch.setattr(host_launcher, "os", SimpleNamespace(**{**vars(os), "name": system}))
    assert host_launcher.main([
        "--mode", "headed", "--session-id", "chosen",
        "--attempt-directory", str(tmp_path / "attempt"),
        "--state-file", str(tmp_path / "state.json"),
        "--", "native", "--session-id", "chosen",
    ]) == expected
    assert observed == ["native-owner-still-running"]
    assert signal.getsignal(signal.SIGINT) == previous


@pytest.mark.asyncio
async def test_original_startup_custody_is_rechecked_before_native_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    startup = Mock()
    startup.check.side_effect = ValueError("original launch closed")
    monkeypatch.setattr(launcher, "_require_receiving_policy", Mock())
    monkeypatch.setattr(launcher, "StartupAdmissionClient", lambda _context: startup)
    spawn = Mock(side_effect=AssertionError("must not create a native child"))
    monkeypatch.setattr(subprocess, "Popen", spawn)
    with pytest.raises(ValueError, match="original launch closed"):
        await launcher.run_headed(
            ["native", "--session-id", "chosen"], session_id="chosen",
            attempt_directory=tmp_path / "attempt", state_file=tmp_path / "state.json",
            env={CONTEXT_ENV: "original-private-channel"},
        )
    startup.check.assert_called_once()
    startup.close.assert_called_once()
    spawn.assert_not_called()
    assert not (tmp_path / "state.json").exists()


@pytest.mark.asyncio
async def test_broker_failure_after_creation_does_not_kill_or_relaunch_native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Child:
        pid = os.getpid()
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            pytest.fail("A lost broker is not authorized native retirement")

    child = Child()
    spawned = asyncio.Event()

    def spawn(*_args: Any, **_kwargs: Any) -> Child:
        spawned.set()
        return child

    monkeypatch.setattr(launcher, "_require_receiving_policy", Mock())
    monkeypatch.setattr(launcher, "contained_test_mode", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(launcher, "_write_host_state", Mock(side_effect=OSError("lost catalogue")))
    task = asyncio.create_task(launcher.run_headed(
        ["native", "--session-id", "chosen"], session_id="chosen",
        attempt_directory=tmp_path / "attempt", state_file=tmp_path / "state.json",
    ))
    try:
        await asyncio.wait_for(spawned.wait(), 2)
        await asyncio.sleep(0)
        assert not task.done()
        child.returncode = 23
        assert await asyncio.wait_for(task, 2) == 23
    finally:
        child.returncode = 23
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("publication", ["published", "locator-write-failed"])
async def test_coordinator_binds_real_child_identity_and_retains_it_after_peer_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, publication: str,
) -> None:
    from agent_procutil import daemon_observation

    from agent_procutil.daemon_instances import DaemonInstanceReceipt

    calls: list[list[str]] = []
    publications: list[DaemonInstanceReceipt] = []
    publish_observation = daemon_observation.publish_file_observation

    def observe_publication(receipt: DaemonInstanceReceipt, name: str, path: Path) -> None:
        if name == "session_host_state":
            publications.append(receipt)
        publish_observation(receipt, name, path)

    monkeypatch.setattr(daemon_observation, "publish_file_observation", observe_publication)

    class Child:
        pid = os.getpid()
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.returncode = 17

    child = Child()
    policy = Mock()
    monkeypatch.setattr(launcher, "_require_receiving_policy", policy)
    monkeypatch.setattr(launcher, "contained_test_mode", lambda: True)
    if publication == "locator-write-failed":
        locator = Mock(side_effect=OSError("original state locator unavailable"))
        monkeypatch.setattr(SessionHost, "publish_state_file", locator)
    environment: dict[str, str] | None = None

    def spawn(argv: list[str], **kwargs: Any) -> Child:
        nonlocal environment
        environment = kwargs["env"]
        calls.append(argv)
        return child

    monkeypatch.setattr(subprocess, "Popen", spawn)
    state_file = tmp_path / "private-state" / "caller-selected.data"
    seed = 'literal\n"quotes" & $value'
    argv = ["fixture-native", "--session-id", "chosen", "-i", seed]
    coordinator = asyncio.create_task(launcher.run_headed(
        argv, session_id="chosen", attempt_directory=tmp_path / "attempt",
        state_file=state_file, nonce="front-private",
    ))
    writer, client = None, None

    async def state_named(name: str) -> dict[str, Any]:
        for _ in range(300):
            if state_file.is_file():
                state = cast(dict[str, Any], json.loads(state_file.read_text()))
                if state["state"] == name:
                    return state
            if coordinator.done():
                await coordinator
                pytest.fail("Coordinator exited before " + name)
            await asyncio.sleep(0.01)
        pytest.fail("Coordinator did not publish " + name)

    try:
        starting = await state_named("native_starting")
        assert "port" not in starting
        assert calls[0][:len(argv)] == argv and calls[0].count(seed) == 1
        assert "--experimental" in calls[0]
        assert Path(calls[0][calls[0].index("--plugin-dir") + 1]).is_dir()
        assert policy.call_count == 2
        assert environment is not None
        binding = json.loads(environment[launcher.BINDING_ENV])
        assert binding["startupPrompt"] == seed
        reader, writer = await asyncio.open_connection("127.0.0.1", binding["port"])
        writer.write((json.dumps({
            "contract": "copilot-driver.headed-sdk", "version": 1,
            "sessionId": "chosen", "nonce": binding["nonce"],
            "nativePid": child.pid + 1, "extensionPid": 123,
        }) + "\n").encode())
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await writer.wait_closed()
        reader, writer = await asyncio.open_connection("127.0.0.1", binding["port"])
        writer.write((json.dumps({
            "contract": "copilot-driver.headed-sdk", "version": 1,
            "sessionId": "chosen", "nonce": binding["nonce"],
            "nativePid": child.pid, "extensionPid": 123,
        }) + "\n").encode())
        await writer.drain()
        if publication == "locator-write-failed":
            unavailable = await state_named("control_unavailable")
            assert unavailable["control_error"] == "coordinator-unavailable"
            locator.assert_called_once_with(state_file)
            assert not coordinator.done() and child.returncode is None and len(calls) == 1
            child.returncode = 17
            assert await asyncio.wait_for(coordinator, 3) == 17
            return
        running = await state_named("running")
        assert running["child_pid"] == child.pid and running["extension_pid"] == 123
        assert len(publications) == 1
        assert running["host_instance_id"] == publications[0].instance_id
        records = [
            record for record in daemon_observation.observe_instances(publications[0].registry_root)
            if record["instance_id"] == running["host_instance_id"]
        ]
        assert len(records) == 1
        assert Path(records[0]["observation_session_host_state_path"]).samefile(state_file)
        client = await SessionHostClient.connect(port=running["port"])
        hello = await client.attach(nonce=b"front-private")
        assert hello.child_pid == child.pid
        await client.detach(reapable=True)
        await client.close()
        client = None
        writer.close()
        await writer.wait_closed()
        degraded = await state_named("control_unavailable")
        assert degraded["child_pid"] == child.pid
        assert not coordinator.done() and child.returncode is None and len(calls) == 1
        client = await SessionHostClient.connect(port=running["port"])
        await client.attach(nonce=b"front-private")
        await client.terminate()
        assert await asyncio.wait_for(coordinator, 3) == 17
        assert json.loads(state_file.read_text())["state"] == "child_exited"
    finally:
        child.returncode = 17
        if client is not None:
            await client.close()
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        if not coordinator.done():
            coordinator.cancel()
        await asyncio.gather(coordinator, return_exceptions=True)
