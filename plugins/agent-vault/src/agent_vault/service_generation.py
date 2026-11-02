"""Incoming-first Vault endpoints and exclusive credential-work custody."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

from single_instance_lease import AlreadyRunningError, SingleInstance
from single_instance_lease.supersession import pid_alive as _pid_exists

from . import __version__
from .config import home_dir, installation_id
from .rendezvous import Endpoint, read_endpoint
from .service_connections import settle_owned
from .service_handoff import AuthReceiver, send_auth_state

log = logging.getLogger(__name__)
PROTOCOL = 1


def pid_alive(pid: int | None) -> bool:
    if os.name != "nt":
        return _pid_exists(pid)
    if type(pid) is not int or pid <= 0:
        return False
    from zdd.private_windows_native import native

    api = native()
    handle = api.k.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        return api.error() != 87
    try:
        return api.k.WaitForSingleObject(handle, 0) != 0
    finally:
        api.close(handle)


def slot_generation(slot: Path) -> str:
    try:
        value = (slot / ".runtime-bootstrap" / "runtime-generation").read_text(
            encoding="utf-8",
        ).strip()
    except OSError:
        value = ""
    return value if re.fullmatch(r"[0-9a-f]{32}", value) else slot.name


PROCESS_SLOT = Path(sys.prefix).resolve()
PROCESS_GENERATION = (
    slot_generation(PROCESS_SLOT) if PROCESS_SLOT.parent.name == "versions" else __version__
)


def valid_generation_health(response: object) -> bool:
    return bool(
        isinstance(response, dict) and response.get("ok") is True
        and type(response.get("pid")) is int and response["pid"] > 0
        and type(response.get("generation_protocol")) is int
        and response["generation_protocol"] == PROTOCOL
        and isinstance(response.get("runtime_generation"), str)
        and bool(response["runtime_generation"])
    )


def same_generation(response: dict | None) -> bool:
    return bool(
        response and valid_generation_health(response)
        and response["runtime_generation"] == PROCESS_GENERATION
    )


def selected_generation() -> str | None:
    root = home_dir()
    try:
        name = (root / "current-version").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_+.-]{1,256}", name) or name in {".", ".."}:
        return None
    return slot_generation(root / "versions" / name)


def selected() -> bool:
    if PROCESS_SLOT.parent.name != "versions":
        return True  # Explicit source/development invocations have no installed marker.
    return selected_generation() == PROCESS_GENERATION


def generation_lease(root: Path) -> SingleInstance:
    key = hashlib.sha256(PROCESS_GENERATION.encode()).hexdigest()[:16]
    return SingleInstance(root, service=f"agent-vault-generation-{key}")


def generation_running() -> bool:
    lease = generation_lease(home_dir())
    try:
        lease.acquire()
    except AlreadyRunningError:
        return True
    lease.release()
    return False


def endpoint_health(endpoint: Endpoint | None) -> dict | None:
    """Probe the captured endpoint, never re-resolve it to a different process."""
    from . import service

    if endpoint is None or endpoint.installation_id != installation_id():
        return None
    expected_pid = endpoint.pid
    request = {"action": "ping"}
    if endpoint.transport == "pipe":
        endpoint = next((item for item in endpoint.alt if item.transport == "tcp"), None)
        if endpoint is None:
            return None
    try:
        if endpoint.transport == "unix" and not service.IS_WINDOWS:
            response = service._dial_unix(request, endpoint.address, timeout=0.25)
        elif endpoint.transport == "tcp":
            host, port = endpoint.tcp_host_port
            response = service._dial_tcp(request, host, port, timeout=0.25)
        else:
            return None
    except (OSError, ValueError, TypeError):
        return None
    return response if (
        isinstance(response, dict) and type(response.get("pid")) is int
        and response["pid"] == expected_pid
    ) else None


class ServiceGeneration:
    def __init__(self, service: Any, connections: Any, servers: list, pipes: list) -> None:
        from . import service as api

        self.api = api
        self.service = service
        self.connections = connections
        self.servers = servers
        self.pipes = pipes
        self.root = api.run_dir()
        self.writer = SingleInstance(home_dir(), service="agent-vault-credential-work")
        self.publication = SingleInstance(home_dir(), service="agent-vault-publication")
        self.generation_lease = generation_lease(home_dir())
        self.has_generation = False
        self.has_writer = False
        self.socket: str | None = None
        self.pipe: str | None = None
        self.port: int | None = None
        self.requested_port = 0
        self.previous: Endpoint | None = None
        self.legacy_predecessor = False
        self.superseded = 0
        self.retiring: dict | None = None
        self.hooks_started = False
        self.receiver: AuthReceiver | None = None
        self.handoff_result = "not-requested"

    def _take_writer(self) -> bool:
        if self.has_writer:
            return True
        if self.legacy_predecessor and self.previous and pid_alive(self.previous.pid):
            return False
        try:
            self.writer.acquire()
        except AlreadyRunningError:
            return False
        self.has_writer = True
        return True

    async def _tcp(self, port: int) -> None:
        listener = await asyncio.start_server(
            self.connections.callback("tcp"), host="127.0.0.1", port=port,
        )
        self.servers.append(listener)
        if self.port is None:
            self.port = listener.sockets[0].getsockname()[1]

    async def start(self, tcp_port: int | None) -> None:
        api = self.api
        self.service._runtime_can_work = False
        try:
            self.generation_lease.acquire()
        except AlreadyRunningError:
            self.service._shutdown = True
            return
        self.has_generation = True
        deadline = time.monotonic() + 60
        while True:
            try:
                self.publication.acquire()
                break
            except AlreadyRunningError:
                if not selected() or time.monotonic() >= deadline:
                    self.service._shutdown = True
                    return
                await asyncio.sleep(0.05)
        try:
            if not selected():
                self.service._shutdown = True
                return
            self.previous = read_endpoint(self.root)
            self.legacy_predecessor = bool(
                self.previous and pid_alive(self.previous.pid)
            )
            if self.legacy_predecessor:
                try:
                    self.receiver = AuthReceiver(self.root, self.previous.pid, PROCESS_GENERATION)
                    self.receiver.start()
                except Exception:
                    self.handoff_result = "private-channel-unavailable"
            replacing = bool(self.previous and pid_alive(self.previous.pid))
            self.service._runtime_can_work = self._take_writer()
            suffix = f".{os.getpid()}"
            if not api.IS_WINDOWS:
                self.socket = api.SOCKET_PATH + suffix if replacing else api.SOCKET_PATH
                if os.path.exists(self.socket):
                    self.socket = api.SOCKET_PATH + suffix
                listener = await asyncio.start_unix_server(
                    self.connections.callback("unix"), path=self.socket,
                )
                self.servers.append(listener)
                os.chmod(self.socket, 0o600)
            self.requested_port = api.configured_tcp_port() if tcp_port is None else tcp_port
            try:
                await self._tcp(self.requested_port)
            except OSError as error:
                if error.errno not in {errno.EADDRINUSE, errno.EACCES} or not self.requested_port:
                    raise
                await self._tcp(0)
            if api.IS_WINDOWS:
                self.pipe = api.PIPE_PATH + suffix if replacing else api.PIPE_PATH
                try:
                    self.pipes.extend(await api.start_pipe_server(
                        self.pipe, self.connections.callback("pipe"),
                    ))
                except Exception:
                    log.warning("Named pipe unavailable; TCP discovery remains available")
                    self.pipe = None
            if not selected():
                self.service._shutdown = True
                return
            address = f"127.0.0.1:{self.port}"
            advertised = api.advertised_endpoint(
                is_windows=api.IS_WINDOWS, unix_bound=self.socket is not None,
                socket_path=self.socket or "", pipe_bound=bool(self.pipes),
                pipe_address=self.pipe, tcp_bound=True, tcp_address=address,
            )
            api.write_endpoint(
                self.root, advertised[0], advertised[1],
                installation_id=installation_id(), alt=advertised[2] or None,
            )
            self._write_pid()
        finally:
            self.publication.release()

    def _write_pid(self) -> None:
        path = Path(self.api.PID_FILE)
        pending = path.with_name(path.name + f".{os.getpid()}.pending")
        pending.write_text(str(os.getpid()), encoding="utf-8")
        pending.replace(path)

    async def _legacy_aliases(self) -> None:
        api = self.api
        if self.socket and self.socket != api.SOCKET_PATH and not os.path.exists(api.SOCKET_PATH):
            try:
                listener = await asyncio.start_unix_server(
                    self.connections.callback("unix"), path=api.SOCKET_PATH,
                )
                self.servers.append(listener)
                os.chmod(api.SOCKET_PATH, 0o600)
                self.legacy_socket = api.SOCKET_PATH
            except OSError:
                log.warning("Legacy Unix alias unavailable; generation discovery remains active")
        if self.pipe and self.pipe != api.PIPE_PATH:
            legacy = Endpoint("pipe", api.PIPE_PATH)
            if not await asyncio.to_thread(api.connect_probe, legacy):
                try:
                    self.pipes.extend(await api.start_pipe_server(
                        api.PIPE_PATH, self.connections.callback("pipe"),
                    ))
                except Exception:
                    log.warning("Legacy pipe alias unavailable; generation discovery remains active")
        if self.requested_port and self.port != self.requested_port:
            try:
                await self._tcp(self.requested_port)
            except OSError:
                log.warning("Legacy TCP alias unavailable; generation discovery remains active")

    async def tick(self) -> None:
        if self.retiring is not None:
            await self._retire()
            return
        endpoint = read_endpoint(self.root)
        if endpoint and endpoint.pid != os.getpid():
            status = await asyncio.to_thread(endpoint_health, endpoint)
            expected = selected_generation()
            if (
                status and valid_generation_health(status)
                and status.get("runtime_generation") != PROCESS_GENERATION
                and (expected is None or status.get("runtime_generation") == expected)
            ):
                self.superseded += 1
                if self.superseded >= 2:
                    self.retiring = status
                    await self._retire()
            else:
                self.superseded = 0
            return
        if not selected() or endpoint is None:
            return
        if not self._take_writer():
            return
        if not self.hooks_started:
            await self.close_handoff()
            self.service._runtime_can_work = True
            self._write_pid()
            await self._legacy_aliases()
            context = self.api.resolve_context()
            self.api.get_registry().run_startup(
                self.service,
                self.api.StartupContext(
                    kpdb=context.kpdb, group=context.group, vault_name=context.vault_name or "",
                    port=self.port or self.requested_port, run_dir=str(self.root),
                ),
            )
            self.hooks_started = True

    async def _retire(self) -> None:
        if self.retiring is None:
            return
        self.service._runtime_can_work = False
        if self.connections.work:
            return
        expected = selected_generation()
        if expected is None or self.retiring.get("runtime_generation") == expected:
            from .cutover import build_auth_state

            await settle_owned(asyncio.to_thread(
                send_auth_state, self.retiring, build_auth_state(self.service),
            ))
        self.service._shutdown = True

    def handoff_advertisement(self) -> dict | None:
        return self.receiver.advertisement() if self.receiver is not None else None

    async def close_handoff(self) -> None:
        if self.receiver is not None:
            from .cutover import apply_auth_state

            receiver, self.receiver = self.receiver, None
            await receiver.close()
            payload = receiver.take()
            if payload is not None and selected():
                warmed = apply_auth_state(self.service, payload)
                self.handoff_result = "warmed" if warmed else "no-unexpired-state"
            else:
                self.handoff_result = "no-compatible-state"

    def cleanup(self) -> None:
        self.service._runtime_can_work = False
        self.api.clear_endpoint(self.root)
        for path in (self.socket, getattr(self, "legacy_socket", None)):
            if path:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(path)
        pid = Path(self.api.PID_FILE)
        with contextlib.suppress(FileNotFoundError, ValueError):
            if int(pid.read_text(encoding="utf-8").strip()) == os.getpid():
                pid.unlink()
        if self.has_writer:
            self.writer.release()
            self.has_writer = False
        if self.has_generation:
            self.generation_lease.release()
            self.has_generation = False


def ensure_selected(timeout: float = 60.0) -> bool:
    """Ensure this installed generation without stopping or relying on its peer."""
    from . import service
    from .launch import spawn_service

    status = endpoint_health(read_endpoint(service.run_dir()))
    if same_generation(status) and status.get("accepting_work") is True:
        return True
    if not selected():
        return False
    spawn_service(["--foreground", "--persistent"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = endpoint_health(read_endpoint(service.run_dir()))
        if same_generation(status) and status.get("accepting_work") is True:
            return True
        time.sleep(0.1)
    return False
