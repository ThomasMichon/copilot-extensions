"""Native role composition without a live Gateway, listener or host registration."""
import asyncio
import os
import subprocess
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING
from unittest.mock import Mock

import pytest

from agent_ssh_service import local_control, native_receiver, source_start
from agent_ssh_service.config import ServiceConfig
from fleet_manager.private_state import private_directory
from zdd.private_protocol import PeerIdentity

if TYPE_CHECKING:
    from services.ssh.tests.conftest import key
else:
    from conftest import key


def policy(tmp_path: Path) -> native_receiver.NativeReceiverConfig:
    value = ServiceConfig.from_dict({
        "schema": "agent-ssh.native-receiver", "version": 1,
        "data_dir": str(tmp_path / "state"), "ingress_file": str(tmp_path / "ingress.json"),
        "transport": {
            "ssh_config": str(tmp_path / "ssh_config"), "host_alias": "gateway-a",
            "gateway_command": ["/opt/native-gateway/launcher"],
        },
    })
    assert isinstance(value, native_receiver.NativeReceiverConfig)
    return value


def binding(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        root=tmp_path / "runtime", slot=tmp_path / "runtime" / "versions" / "1",
        identity=SimpleNamespace(document=lambda: {"fixture": "source"}),
    )


def test_native_policy_does_not_require_fleet_credentials_and_remains_strict(tmp_path: Path) -> None:
    selected = policy(tmp_path)
    assert isinstance(selected, native_receiver.NativeReceiverConfig)
    assert "proof_key_file" not in selected.to_dict()
    value = selected.to_dict()
    value["transport"]["gateway_command"] = ["../foreign"]
    with pytest.raises(ValueError, match="absolute-native"):
        ServiceConfig.from_dict(value)
    value = selected.to_dict()
    value["fleet"] = "ignored-by-an-old-reader"
    with pytest.raises(ValueError, match="invalid-native-receiver-policy"):
        ServiceConfig.from_dict(value)


def test_native_policy_requires_its_exact_ingress_state_and_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_ssh import gateway_sshd

    selected = policy(tmp_path)
    document: dict[str, Any] = {"version": 2, "state_dir": tmp_path / "state" / "native-ingress"}
    monkeypatch.setattr(gateway_sshd, "load", lambda _: document.copy())
    assert selected.ingress() == document
    document["version"] = 1
    with pytest.raises(ValueError, match="membership-contract-required"):
        selected.ingress()
    document.update(version=2, state_dir=tmp_path / "foreign")
    with pytest.raises(ValueError, match="state-mismatch"):
        selected.ingress()


def test_native_source_selection_validates_external_ingress_before_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_manager.private_state import create_private_file

    selected = policy(tmp_path)
    private_directory(tmp_path)
    path = tmp_path / "policy.json"
    with open(create_private_file(path), "wb", closefd=True) as stream:
        stream.write(selected.encoded)
    calls: list[str] = []
    def ingress(_: native_receiver.NativeReceiverConfig) -> dict[str, Any]:
        calls.append("ingress")
        return {}
    monkeypatch.setattr(native_receiver.NativeReceiverConfig, "ingress", ingress)
    assert isinstance(source_start.selected_policy(path, binding(tmp_path)),
                      native_receiver.NativeReceiverConfig)
    assert calls == ["ingress"]


def control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> local_control.LocalControl:
    monkeypatch.setattr(local_control, "current_peer", lambda: PeerIdentity(123, "456", "fixture"))
    return local_control.LocalControl(binding(tmp_path), policy(tmp_path), selected=lambda _: None)


def test_native_control_has_no_directory_writer_or_fleet_health_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = control(tmp_path, monkeypatch)
    monkeypatch.setattr(selected.writer, "observed_owner",
                        Mock(side_effect=AssertionError("directory")))
    ready = asyncio.Event()
    ready.set()
    receiver = SimpleNamespace(
        started=ready, forward=SimpleNamespace(is_alive=True),
        listener=SimpleNamespace(ready=Mock(return_value=True)),
    )

    async def exercise() -> None:
        selected.activation, selected.initialized = asyncio.Event(), asyncio.Event()
        selected.attach_receiver(receiver)
        assert (await selected._operation("prepare"))["prepared"]
        assert (await selected._operation("activate"))["ready"]
        receiver.forward.is_alive = False
        assert not (await selected._operation("health"))["ready"]
        receiver.forward.is_alive = True
        receiver.listener.ready.return_value = False
        assert not (await selected._operation("health"))["ready"]
        selected.stop.set()
        assert not (await selected._operation("prepare"))["prepared"]

    asyncio.run(exercise())


def transport_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[native_receiver.NativeReceiverConfig, dict[str, Any], list[tuple[str, str]], Mock]:
    from agent_ssh import gateway_authorization, gateway_profile, gateway_sshd
    from ssh_manager.config_sources import SSHConfig, SSHProfileSource

    selected = policy(tmp_path)
    ingress = {
        "public_key_file": tmp_path / "client.pub", "ssh_keygen": tmp_path / "keygen",
        "native_gateway": {"gateway_user": "owner", "host_key": key(7)},
    }
    known_hosts = tmp_path / "known_hosts"
    options = [
        ("stricthostkeychecking", "yes"), ("identitiesonly", "yes"), ("batchmode", "yes"),
        ("identityagent", "none"), ("user", "owner"), ("identityfile", str(tmp_path / "identity")),
        ("userknownhostsfile", str(known_hosts)), ("hostkeyalias", "gateway-pin"),
        ("hostname", "127.0.0.1"), ("port", "18444"),
        ("proxycommand", "provider-proxy --target selected"),
    ]
    monkeypatch.setattr(SSHProfileSource, "get_ssh_config", lambda _: SSHConfig(
        host_alias="gateway-a", effective_config=tuple(options),
    ))
    monkeypatch.setattr(gateway_authorization, "_private", lambda *a, **k: None)
    monkeypatch.setattr(gateway_profile, "_read_owned_file", lambda path: (
        "gateway-pin " + key(7) + "\n" if path == known_hosts else key(1)
    ))
    matches = Mock()
    monkeypatch.setattr(gateway_sshd, "_host_key", matches)
    return selected, ingress, options, matches


def test_receiver_freezes_strict_profile_and_matches_local_client_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected, ingress, options, matches = transport_fixture(tmp_path, monkeypatch)
    transport = native_receiver._transport(selected, ingress)
    assert transport.config_file == "none"
    assert transport.port == 18444 and transport.user == "owner"
    assert transport.extra_options["ProxyCommand"] == "provider-proxy --target selected"
    assert transport.extra_options["StrictHostKeyChecking"] == "yes"
    options[:] = []
    assert transport.extra_options["HostKeyAlias"] == "gateway-pin"
    matches.assert_called_once_with({
        "host_key_file": tmp_path / "identity", "ssh_keygen": tmp_path / "keygen",
    }, key(1))


@pytest.mark.parametrize("operation", ["observe", "forward"])
def test_frozen_known_hosts_space_survives_actual_openssh_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    from ssh_manager.forward import build_forward_ssh_args, build_remote_exec_args
    from agent_procutil import no_window_kwargs

    ssh = shutil.which("ssh")
    if ssh is None:
        pytest.skip("OpenSSH is unavailable")
    home = tmp_path / ("u " * 64 + "user")
    selected, ingress, options, _ = transport_fixture(home, monkeypatch)
    transport = native_receiver._transport(selected, ingress)
    arguments = (
        build_remote_exec_args(transport, "true") if operation == "observe"
        else build_forward_ssh_args(transport, None, None, reverse_forwards=["24001:127.0.0.1:22"])
    )
    result = subprocess.run(
        [ssh, "-G", *arguments[1:]], capture_output=True, text=True, timeout=10,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    pins = [line.partition(" ")[2] for line in result.stdout.splitlines()
            if line.startswith("userknownhostsfile ")]
    assert len(pins) == 1
    assert pins[0].strip('"') == str(home / "known_hosts")


@pytest.mark.parametrize("name,value", [
    ("stricthostkeychecking", "accept-new"), ("identityagent", "ambient"),
    ("user", "foreign"), ("identityfile", "relative"), ("port", "0"),
    ("proxyjump", "foreign"),
])
def test_receiver_rejects_weakened_or_mismatched_route_before_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str,
) -> None:
    selected, ingress, options, matches = transport_fixture(tmp_path, monkeypatch)
    options[:] = [(key, entry) for key, entry in options if key != name]
    options.append((name, value))
    with pytest.raises(ValueError):
        native_receiver._transport(selected, ingress)


def test_receiver_refuses_foreign_gateway_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_ssh import gateway_profile

    selected, ingress, options, matches = transport_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(gateway_profile, "_read_owned_file",
                        lambda _: "gateway-pin " + key(8))
    with pytest.raises(ValueError, match="exact-gateway-pin"):
        native_receiver._transport(selected, ingress)
    matches.assert_not_called()


@pytest.mark.parametrize("failure", ["sshd", "host-key", "login"])
def test_invalid_incoming_native_binding_preserves_addressed_incumbent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from agent_ssh import gateway_authorization, gateway_sshd
    from agent_ssh_service import local_service

    selected, source = policy(tmp_path), binding(tmp_path)
    monkeypatch.setattr(gateway_sshd, "require_foreground_support", lambda: None)
    source.root.mkdir(parents=True)
    executable = tmp_path / "executable"
    executable.write_bytes(b"fixture")
    executable.chmod(0o700)
    ingress = {
        "native_gateway": {}, "machine": "receiver", "environment": "windows",
        "public_key_file": tmp_path / "identity.pub",
        "host_key_file": tmp_path / "host", "ssh_keygen": executable,
        "sshd": tmp_path / "missing" if failure == "sshd" else executable,
    }
    projection = {"user": "foreign" if failure == "login" else "receiver", "host_key": key(7)}
    monkeypatch.setattr(type(selected), "ingress", lambda _: ingress)
    monkeypatch.setattr(native_receiver, "_transport", lambda *a: object())

    async def observe(*args: Any) -> object:
        return object()

    monkeypatch.setattr(native_receiver.PublicObserver, "__call__", observe)
    monkeypatch.setattr(gateway_authorization, "projection_from_native", lambda *a, **k: projection)
    monkeypatch.setattr(gateway_authorization, "_private", lambda *a, **k: None)
    monkeypatch.setattr(gateway_sshd, "_account", lambda: "receiver")
    monkeypatch.setattr(gateway_sshd, "_principal", lambda _: "receiver")
    monkeypatch.setattr(subprocess, "run",
                        Mock(return_value=SimpleNamespace(returncode=0, stdout=key(8).encode("ascii"))))
    monkeypatch.setattr(source_start, "ensure_selected", lambda _: None)
    monkeypatch.setattr(local_service, "_matching", lambda *a: [])
    incumbent = {"root": str(source.root), "peer": {"pid": os.getpid() + 1}}
    monkeypatch.setattr(local_service, "records", lambda _: [incumbent])
    stop, spawn = Mock(), Mock()
    monkeypatch.setattr(local_service, "request", stop)
    monkeypatch.setattr(local_service, "spawn_daemon", spawn)
    with pytest.raises((OSError, ValueError)):
        local_service.start(source, tmp_path / "policy.json", selected)
    stop.assert_not_called()
    spawn.assert_not_called()


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_unsupported_foreground_preserves_addressed_incumbent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    from agent_ssh import gateway_sshd
    from agent_ssh_service import local_service

    selected, source = policy(tmp_path), binding(tmp_path)
    source.root.mkdir(parents=True)
    monkeypatch.setattr(gateway_sshd, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(source_start, "ensure_selected", lambda _: None)
    monkeypatch.setattr(local_service, "_matching", lambda *a: [])
    incumbent = {"root": str(source.root), "peer": {"pid": os.getpid() + 1}}
    monkeypatch.setattr(local_service, "records", lambda _: [incumbent])
    stop, spawn, ingress = Mock(), Mock(), Mock()
    monkeypatch.setattr(local_service, "request", stop)
    monkeypatch.setattr(local_service, "spawn_daemon", spawn)
    monkeypatch.setattr(type(selected), "ingress", ingress)
    with pytest.raises(OSError, match="requires Windows"):
        local_service.start(source, tmp_path / "policy.json", selected)
    stop.assert_not_called()
    spawn.assert_not_called()
    ingress.assert_not_called()


def test_changed_retained_listener_config_preserves_incumbent_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_ssh import gateway_authorization, gateway_profile, gateway_sshd
    from agent_ssh_service import local_service
    from dropin_registry import atomic_write_text

    selected, source = policy(tmp_path), binding(tmp_path)
    source.root.mkdir(parents=True)
    root = tmp_path / "state" / "native-ingress"
    private_directory(root.parent)
    if os.name == "nt":
        gateway_authorization._windows_create_directory(root)
    else:
        root.mkdir(mode=0o700)
    projection = {
        "target": {"machine": "receiver", "environment": "windows"},
        "controller": "gateway-a", "user": "receiver", "host_key": key(7),
    }
    ingress: dict[str, Any] = {
        "state_dir": root, "port": 24107, "host_key_file": tmp_path / "host",
        "sshd": tmp_path / "sshd", "ssh_keygen": tmp_path / "keygen",
        "native_gateway": {}, "machine": "receiver", "environment": "windows",
        "public_key_file": tmp_path / "identity.pub",
    }
    owner = {
        "schema": "agent-ssh.gateway-native-ingress", "version": 1,
        "target": projection["target"], "controller": projection["controller"],
        "user": "receiver", "host_key": projection["host_key"], "principal": "receiver",
    }
    for path, content in (
        (root / "owner.json", gateway_profile._json(owner)),
        (root / "sshd_config", gateway_sshd._native_config(
            ingress, "receiver", root / "authorization" / "authorized_keys.gateway",
        )),
    ):
        atomic_write_text(path, content)
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()}
    ingress["port"] += 1
    monkeypatch.setattr(type(selected), "ingress", lambda _: ingress)
    monkeypatch.setattr(native_receiver, "_transport", lambda *a: object())

    async def observe(*args: Any) -> object:
        return object()

    monkeypatch.setattr(native_receiver.PublicObserver, "__call__", observe)
    monkeypatch.setattr(gateway_authorization, "projection_from_native", lambda *a, **k: projection)
    monkeypatch.setattr(gateway_sshd, "require_foreground_support", lambda: None)
    monkeypatch.setattr(gateway_sshd, "_account", lambda: "receiver")
    monkeypatch.setattr(gateway_sshd, "_principal", lambda _: "receiver")
    monkeypatch.setattr(gateway_sshd, "_executable", lambda _: None)
    monkeypatch.setattr(gateway_sshd, "_host_key", lambda *a: None)
    monkeypatch.setattr(source_start, "ensure_selected", lambda _: None)
    monkeypatch.setattr(local_service, "_matching", lambda *a: [])
    incumbent = {"root": str(source.root), "peer": {"pid": os.getpid() + 1}}
    monkeypatch.setattr(local_service, "records", lambda _: [incumbent])
    stop, spawn = Mock(), Mock()
    monkeypatch.setattr(local_service, "request", stop)
    monkeypatch.setattr(local_service, "spawn_daemon", spawn)
    with pytest.raises(ValueError, match="configuration differs"):
        local_service.start(source, tmp_path / "policy.json", selected)
    stop.assert_not_called()
    spawn.assert_not_called()
    assert {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()} == before


def test_service_owns_receiver_lease_through_cleanup_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_ssh import gateway_receiver

    selected = policy(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(native_receiver, "private_directory", lambda _: None)
    monkeypatch.setattr(native_receiver, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(native_receiver, "SingleInstance", lambda *a, **k: SimpleNamespace(
        acquire=lambda: events.append("lease-acquire"),
        release=lambda: events.append("lease-release"),
    ))
    monkeypatch.setattr(type(selected), "ingress", lambda _: {"native_gateway": {}})
    async def transport(*_: Any) -> object:
        events.append("transport")
        return object()
    monkeypatch.setattr(native_receiver, "_resolve_transport", transport)

    class Receiver:
        running = False
        attempts = 0

        def __init__(self, *a: Any, **k: Any) -> None:
            events.append("receiver")

        async def run(self, stop: asyncio.Event) -> None:
            self.running = True
            events.append("serve")
            raise RuntimeError("cleanup-refused")

        async def close(self) -> None:
            self.attempts += 1
            events.append("cleanup")
            if self.attempts == 1:
                raise RuntimeError("cleanup-refused")
            self.running = False

    monkeypatch.setattr(gateway_receiver, "NativeReceiver", Receiver)
    original_sleep = asyncio.sleep

    async def sleep(_: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", sleep)

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="cleanup-refused"):
            await native_receiver.run(selected)

    asyncio.run(exercise())
    assert events == [
        "lease-acquire", "transport", "receiver", "serve", "cleanup", "cleanup", "lease-release",
    ]


@pytest.mark.parametrize("blocked", ["profile", "key"])
def test_stop_joins_blocked_transport_probe_before_releasing_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked: str,
) -> None:
    import agent_procutil
    from agent_ssh import gateway_sshd

    selected, ingress, options, _ = transport_fixture(tmp_path, monkeypatch)
    ingress["ssh_keygen"] = Path(sys.executable)
    monkeypatch.setattr(gateway_sshd, "_executable", lambda _: None)
    monkeypatch.setattr(shutil, "which", lambda _: sys.executable)
    monkeypatch.setattr(type(selected), "ingress", lambda _: ingress)
    monkeypatch.setattr(native_receiver, "os", SimpleNamespace(name="posix"))
    events: list[str] = []
    monkeypatch.setattr(native_receiver, "private_directory", lambda _: None)
    monkeypatch.setattr(native_receiver, "SingleInstance", lambda *a, **k: SimpleNamespace(
        acquire=lambda: events.append("lease-acquire"),
        release=lambda: events.append("lease-release"),
    ))
    original_probe = native_receiver._transport_probe
    original_spawn = agent_procutil.spawn_in_kill_on_close_job
    processes: list[asyncio.subprocess.Process] = []

    async def exercise() -> None:
        started = asyncio.Event()
        stop = asyncio.Event()

        async def spawn(*args: Any, **kwargs: Any) -> Any:
            process, job = await original_spawn(*args, **kwargs)
            processes.append(process)
            started.set()
            return process, job

        async def probe(arguments: list[str], cwd: str) -> bytes:
            profile = arguments[1] == "-G"
            if (blocked == "profile") == profile:
                return await original_probe(
                    [sys.executable, "-c", "import time;time.sleep(60)"], str(tmp_path),
                )
            return (
                "\n".join(name + " " + value for name, value in options).encode()
                if profile else key(1).encode()
            )

        monkeypatch.setattr(agent_procutil, "spawn_in_kill_on_close_job", spawn)
        monkeypatch.setattr(native_receiver, "_transport_probe", probe)
        running = asyncio.create_task(native_receiver.run(selected, stop=stop))
        try:
            await asyncio.wait_for(started.wait(), 10)
            stop.set()
            await asyncio.wait_for(running, 10)
            assert processes and all(process.returncode is not None for process in processes)
        finally:
            stop.set()
            await running

    asyncio.run(exercise())
    assert events == ["lease-acquire", "lease-release"]


def test_public_observer_uses_only_read_commands_and_existing_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_ssh import gateway_native

    calls: list[tuple[str, ...]] = []
    descriptor = {
        "schema": 1, "backend": "openssh-reverse", "machine": "receiver",
        "gateway_user": "owner", "host_key": key(7), "server_port": 22, "relay_port": 24001,
        "peers": [{"machine": "receiver", "relay_port": 24001}],
    }
    responses: dict[tuple[str, ...], dict[str, Any]] = {
        ("export-client", "--machine", "receiver"): descriptor,
        ("export-directory",): {"peers": [{
            "machine": "receiver", "user": "owner", "public_key": key(1), "host_key": key(2),
        }]},
        ("status",): {"installed": True, "enabled": True, "enrollment_enabled": True},
    }
    observer = native_receiver.PublicObserver(object(), ["/selected/owner"], descriptor)

    async def call(command: tuple[str, ...]) -> bytes:
        calls.append(command)
        return gateway_native._encode(responses[command])

    monkeypatch.setattr(observer, "_call", call)
    observation = asyncio.run(observer())
    assert observation.descriptor == descriptor
    assert observation.directory == responses[("export-directory",)]
    assert calls == [
        ("export-client", "--machine", "receiver"), ("export-directory",), ("status",),
    ]
