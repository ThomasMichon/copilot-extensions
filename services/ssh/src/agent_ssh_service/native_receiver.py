"""Native receiving role inside the existing installed SSH application."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import locale
import os
from pathlib import PurePosixPath
import shlex
import shutil
import signal
from typing import Any, cast

from fleet_manager.private_state import private_directory
from single_instance_lease import SingleInstance

from .config import Document, ServiceConfig, absolute, canonical, check, decode

CAPABILITY = "gateway-native-receiver-v1"


@dataclass(frozen=True)
class NativeReceiverConfig(ServiceConfig):
    """Local private policy, not a synchronized executable handoff."""

    encoded: bytes

    @classmethod
    def from_dict(cls, value: Document) -> NativeReceiverConfig:
        value = decode(canonical(value))
        check(isinstance(value, dict) and set(value) == {
            "schema", "version", "data_dir", "ingress_file", "transport",
        } and value["schema"] == "agent-ssh.native-receiver"
              and type(value["version"]) is int and value["version"] == 1,
              "invalid-native-receiver-policy")
        for name in ("data_dir", "ingress_file"):
            absolute(value[name])
        root = absolute(value["data_dir"])
        check(root != root.parent, "dedicated-service-data-directory-required")
        transport = value["transport"]
        check(isinstance(transport, dict) and set(transport) == {
            "ssh_config", "host_alias", "gateway_command",
        }, "invalid-native-receiver-transport")
        absolute(transport["ssh_config"])
        try:
            from agent_ssh.gateway_profile import _alias
        except ImportError as error:
            raise RuntimeError("native-receiver-extra-required") from error

        _alias(transport["host_alias"])
        command = transport["gateway_command"]
        check(isinstance(command, list) and 1 <= len(command) <= 8 and all(
            isinstance(part, str) and 0 < len(part) <= 1024
            and not any(ord(char) < 32 for char in part) for part in command
        ), "invalid-native-gateway-command")
        check(PurePosixPath(command[0]).is_absolute()
              and ".." not in PurePosixPath(command[0]).parts,
              "absolute-native-gateway-command-required")
        return cls(canonical(value))

    def to_dict(self) -> Document:
        return cast(Document, decode(self.encoded))

    def ingress(self) -> Document:
        from agent_ssh.gateway_sshd import load

        value = self.to_dict()
        ingress = load(absolute(value["ingress_file"]))
        check(ingress["version"] == 2, "native-receiver-membership-contract-required")
        expected = absolute(value["data_dir"]).resolve() / "native-ingress"
        check(ingress["state_dir"].resolve() == expected, "native-ingress-state-mismatch")
        return ingress


def _transport(config: NativeReceiverConfig, ingress: Document) -> Any:
    from agent_ssh import gateway_authorization, gateway_sshd
    from ssh_manager.config_sources import SSHProfileSource

    selected = config.to_dict()["transport"]
    profile = absolute(selected["ssh_config"])
    gateway_authorization._private(profile, directory=False)
    source = SSHProfileSource(
        selected["host_alias"], config_file=str(profile), resolve_timeout=10,
        resolve_cwd=config.to_dict()["data_dir"],
    ).get_ssh_config()
    transport, public = _frozen_transport(config, ingress, source)
    gateway_sshd._host_key({
        "host_key_file": absolute(transport.identity_file), "ssh_keygen": ingress["ssh_keygen"],
    }, public)
    return transport


def _frozen_transport(
    config: NativeReceiverConfig, ingress: Document, source: Any,
) -> tuple[Any, str]:
    from agent_ssh import gateway_authorization, gateway_profile
    from ssh_manager.config_sources import SSHConfig

    selected = config.to_dict()["transport"]
    options: dict[str, list[str]] = {}
    for key, value in source.effective_config:
        options.setdefault(key.casefold(), []).append(value)

    def one(name: str) -> str:
        values = options.get(name, [])
        check(len(values) == 1, "native-receiver-exact-ssh-option-required")
        return values[0]

    for name in ("stricthostkeychecking", "identitiesonly", "batchmode"):
        check(one(name) in ("yes", "true"), "native-receiver-strict-ssh-policy-required")
    check(one("identityagent") == "none", "native-receiver-agent-refused")
    descriptor = ingress["native_gateway"]
    check(one("user") == descriptor["gateway_user"], "native-receiver-gateway-user-mismatch")
    identity = absolute(one("identityfile").strip('"'))
    known_hosts = absolute(one("userknownhostsfile").strip('"'))
    alias = one("hostkeyalias")
    gateway_profile._alias(alias)
    gateway_authorization._private(known_hosts, directory=False)
    expected_pin = alias + " " + descriptor["host_key"]
    check(gateway_profile._read_owned_file(known_hosts).strip() == expected_pin,
          "native-receiver-exact-gateway-pin-required")
    public = gateway_profile._public_key(
        gateway_profile._read_owned_file(ingress["public_key_file"]).strip(),
    )
    # Freeze the selected routing/authentication inputs instead of rereading a
    # mutable profile on every reconnect. The provider owns any ProxyCommand.
    frozen = {
        "Hostname": one("hostname"), "UserKnownHostsFile": gateway_profile._quote(known_hosts),
        "GlobalKnownHostsFile": "none", "HostKeyAlias": alias,
        "StrictHostKeyChecking": "yes", "IdentitiesOnly": "yes",
        "IdentityAgent": "none", "BatchMode": "yes", "PasswordAuthentication": "no",
        "KbdInteractiveAuthentication": "no", "PreferredAuthentications": "publickey",
        "ControlMaster": "no", "ControlPath": "none", "ControlPersist": "no",
        "ClearAllForwardings": "no",
    }
    proxy = options.get("proxycommand", [])
    check(len(proxy) <= 1 and options.get("proxyjump", []) in ([], ["none"]),
          "native-receiver-ambiguous-proxy")
    if proxy and proxy != ["none"]:
        frozen["ProxyCommand"] = proxy[0]
    port = one("port")
    check(port.isascii() and port.isdigit() and 1 <= int(port) <= 65535,
          "native-receiver-invalid-gateway-port")
    return SSHConfig(
        host_alias=selected["host_alias"], user=descriptor["gateway_user"],
        port=int(port), identity_file=str(identity), config_file="none", extra_options=frozen,
    ), public


async def _transport_probe(arguments: list[str], cwd: str) -> bytes:
    from agent_procutil import spawn_in_kill_on_close_job
    from agent_ssh import gateway_sshd
    from agent_ssh.gateway_receiver import _settle
    from ssh_manager.process import (
        redirected_ssh_env, ssh_subprocess_kwargs, terminate_ssh_process_tree,
    )
    from ssh_manager.proxy import _track_owner_job

    spawning = asyncio.create_task(spawn_in_kill_on_close_job(
        *arguments, require_job=True, cwd=cwd,
        env=redirected_ssh_env(gateway_sshd._process_environment(), windows=os.name == "nt"),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **ssh_subprocess_kwargs(),
    ))
    process = None
    tasks: list[asyncio.Task[Any]] = []

    async def bounded(stream: asyncio.StreamReader) -> bytes:
        result = bytearray()
        while True:
            part = await stream.read(min(65536, 65537 - len(result)))
            if not part:
                return bytes(result)
            result.extend(part)
            check(len(result) <= 65536, "native-transport-probe-output-limit")

    try:
        try:
            spawned, job = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            spawned, job = await _settle(spawning)
            process = spawned
            _track_owner_job(process, job)
            raise
        process = spawned
        _track_owner_job(process, job)
        assert process.stdout is not None and process.stderr is not None
        tasks = [asyncio.create_task(bounded(process.stdout)),
                 asyncio.create_task(bounded(process.stderr)),
                 asyncio.create_task(process.wait())]
        output, _, code = await asyncio.wait_for(asyncio.gather(*tasks), 10)
        check(code == 0, "native-transport-probe-unavailable")
        return bytes(output)
    finally:
        if process is not None:
            await _settle(asyncio.create_task(terminate_ssh_process_tree(process, grace=0)))
            await _settle(asyncio.create_task(process.wait()))
        for task in tasks:
            task.cancel()
        await _settle(asyncio.ensure_future(asyncio.gather(*tasks, return_exceptions=True)))


async def _resolve_transport(config: NativeReceiverConfig, ingress: Document) -> Any:
    from agent_ssh import gateway_authorization, gateway_sshd
    from ssh_manager.config_sources import SSHConfig

    selected = config.to_dict()["transport"]
    profile = absolute(selected["ssh_config"])
    gateway_authorization._private(profile, directory=False)
    ssh = shutil.which("ssh")
    if ssh is None:
        raise ValueError("native-receiver-ssh-unavailable")
    output = await _transport_probe(
        [ssh, "-G", "-F", str(profile), selected["host_alias"]], config.to_dict()["data_dir"],
    )
    effective = []
    for line in output.decode(locale.getpreferredencoding(False)).splitlines():
        name, separator, value = line.partition(" ")
        if separator and name.casefold() != "host":
            effective.append((name.casefold(), value.strip()))
    source = SSHConfig(host_alias=selected["host_alias"], effective_config=tuple(effective))
    transport, public = _frozen_transport(config, ingress, source)
    identity = absolute(transport.identity_file)
    gateway_authorization._private(identity, directory=False)
    gateway_sshd._executable(ingress["ssh_keygen"])
    actual = await _transport_probe(
        [str(ingress["ssh_keygen"]), "-y", "-P", "", "-f", str(identity)],
        config.to_dict()["data_dir"],
    )
    check(len(actual) <= 4096 and " ".join(actual.decode("ascii").split()[:2]) == public,
          "native-receiver-client-key-mismatch")
    return transport


async def _activation_transport(
    config: NativeReceiverConfig, ingress: Document, stop: asyncio.Event,
) -> Any:
    from agent_ssh.gateway_receiver import _settle

    resolving = asyncio.create_task(_resolve_transport(config, ingress))
    stopping = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({resolving, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if stop.is_set():
            return None
        return await resolving
    finally:
        resolving.cancel()
        stopping.cancel()
        results = await _settle(asyncio.ensure_future(asyncio.gather(
            resolving, stopping, return_exceptions=True,
        )))
        for result in results:
            if isinstance(result, Exception):
                raise result


class PublicObserver:
    """Read fixed native public commands; never issue enrollment or arbitrary exec."""

    def __init__(self, transport: Any, command: list[str], descriptor: Document) -> None:
        self.transport, self.command, self.descriptor = transport, command, descriptor

    async def _call(self, command: tuple[str, ...]) -> bytes:
        from agent_ssh.gateway_native import MAX_BYTES
        from ssh_manager.forward import build_remote_exec_args
        from ssh_manager.process import terminate_ssh_process_tree
        from ssh_manager.proxy import create_ssh_subprocess, ssh_process_env
        from agent_ssh.gateway_receiver import _settle

        arguments = build_remote_exec_args(
            self.transport, shlex.join([*self.command, *command]),
        )
        process = await create_ssh_subprocess(
            *arguments, config=self.transport, env=ssh_process_env(self.transport),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        async def bounded(stream: asyncio.StreamReader) -> bytes:
            result = bytearray()
            while True:
                part = await stream.read(min(65536, MAX_BYTES + 1 - len(result)))
                if not part:
                    return bytes(result)
                result.extend(part)
                check(len(result) <= MAX_BYTES, "native-public-output-limit")

        assert process.stdout is not None and process.stderr is not None
        output = asyncio.create_task(bounded(process.stdout))
        errors = asyncio.create_task(bounded(process.stderr))
        waiting = asyncio.create_task(process.wait())
        try:
            stdout, _, code = await asyncio.wait_for(asyncio.gather(output, errors, waiting), 20)
            check(code == 0, "native-public-command-unavailable")
            return bytes(stdout)
        finally:
            await _settle(asyncio.create_task(terminate_ssh_process_tree(process)))
            for task in (output, errors, waiting):
                task.cancel()
            await _settle(asyncio.ensure_future(asyncio.gather(
                output, errors, waiting, return_exceptions=True,
            )))

    async def __call__(self) -> Any:
        from agent_ssh.gateway_native import NativeGateway

        responses: dict[tuple[str, ...], bytes] = {}
        for command in (
            ("export-client", "--machine", self.descriptor["machine"]),
            ("export-directory",), ("status",),
        ):
            responses[command] = await self._call(command)
        return NativeGateway(
            lambda command, payload: responses[command], self.descriptor,
        ).inspect()


def preflight(config: NativeReceiverConfig) -> None:
    """Prove local bindings and read access before retiring an addressed receiver."""
    from agent_ssh import gateway_sshd
    from agent_ssh.gateway_authorization import projection_from_native

    gateway_sshd.require_foreground_support()
    ingress = config.ingress()
    transport = _transport(config, ingress)
    observation = asyncio.run(PublicObserver(
        transport, config.to_dict()["transport"]["gateway_command"], ingress["native_gateway"],
    )())
    projection = projection_from_native(
        observation, descriptor=ingress["native_gateway"], machine=ingress["machine"],
        environment=ingress["environment"],
        public_key_file=ingress["public_key_file"],
    )
    gateway_sshd.validate_bindings(ingress, projection)


async def run(config: NativeReceiverConfig, *, lifecycle: Any = None,
              stop: asyncio.Event | None = None) -> None:
    """Keep the original service lease/control alive until native cleanup settles."""
    from agent_ssh.gateway_receiver import NativeReceiver
    from fleet_manager.private_state import create_private_file, windows_private

    stop = stop if stop is not None else asyncio.Event()
    if stop.is_set():
        return
    root = absolute(config.to_dict()["data_dir"])
    private_directory(root)
    lease = SingleInstance(str(root), service="agent-ssh-service")
    if os.name == "nt":
        try:
            descriptor = create_private_file(lease.lock_path)
        except FileExistsError:
            windows_private(lease.lock_path)
        else:
            os.close(descriptor)
    previous = {}
    receiver = None
    acquired = False
    loop = asyncio.get_running_loop()
    try:
        for selected in (signal.SIGINT, signal.SIGTERM):
            previous[selected] = signal.signal(
                selected, lambda *_: loop.call_soon_threadsafe(stop.set),
            )
        if lifecycle is not None:
            await lifecycle.prepare(stop=stop, lease=lease)
            if not await lifecycle.wait_activation():
                return
        lease.acquire()
        acquired = True
        ingress = config.ingress()
        transport = await _activation_transport(config, ingress, stop)
        if stop.is_set():
            return
        receiver = NativeReceiver(
            ingress, transport, observe=PublicObserver(
                transport, config.to_dict()["transport"]["gateway_command"],
                ingress["native_gateway"],
            ), observation_timeout=65,
        )
        if lifecycle is not None:
            lifecycle.attach_receiver(receiver)
        await receiver.run(stop)
    finally:
        if receiver is not None and receiver.running:
            # Cleanup refusal retains this application's lease and local owner.
            # The same owner retries; neither a successor nor an installer guesses.
            while receiver.running:
                try:
                    await receiver.close()
                except (OSError, ValueError, RuntimeError):
                    try:
                        await asyncio.sleep(1)
                    except asyncio.CancelledError:
                        continue
        if acquired:
            lease.release()
        for selected, handler in previous.items():
            signal.signal(selected, handler)
