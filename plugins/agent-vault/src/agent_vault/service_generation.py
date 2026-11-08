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
import socket
import sys
import time
from typing import Any

from single_instance_lease import AlreadyRunningError, SingleInstance
from single_instance_lease.supersession import pid_alive as _pid_exists
from agent_procutil._daemon_instance_identity import runtime_slot_paths

from . import __version__
from .config import home_dir, installation_id
from .rendezvous import Endpoint, read_endpoint
from .service_connections import settle_owned
from .service_handoff import AuthReceiver, send_auth_state
from .service_selection import SelectionCustodyUnavailable, selection_custody
from .service_unix_alias import UnixAlias, socket_identity

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


_PROCESS_LAYOUT = runtime_slot_paths(Path(sys.prefix).resolve())
PROCESS_SLOT = Path(_PROCESS_LAYOUT[1]) if _PROCESS_LAYOUT else Path(sys.prefix).resolve()
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
    request = {"action": "ping", "health_only": True}
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
        self.predecessor = None
        self.predecessor_deadline = 0.0
        self.predecessor_retirement = "not-requested"
        self.aliases_after_exit = False
        self.writer_candidate_pid: int | None = None
        self.writer_predecessor = None
        self.writer_predecessor_retirement = "not-requested"
        self.unix_alias = UnixAlias(self.root, api.SOCKET_PATH)
        self.owned_sockets: dict[str, tuple[int, int]] = {}
        self.legacy_pipe_bound = False
        self.legacy_tcp_bound = False

    def _take_writer(self) -> bool:
        if self.has_writer:
            return True
        waiting_for_exit = (
            self.legacy_predecessor and self.previous and pid_alive(self.previous.pid)
            and not (self.receiver and self.receiver.writer_released())
            and not (self.predecessor and self.predecessor.exited())
        )
        try:
            self.writer.acquire()
        except AlreadyRunningError as error:
            self.writer_candidate_pid = error.holder_pid
            return False
        if waiting_for_exit:
            self.writer.release()
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
            if (
                self.previous and self.previous.transport == "unix"
                and self.previous.address == api.SOCKET_PATH
                and self.previous.installation_id == installation_id()
            ):
                self.unix_alias.capture_previous(self.previous.pid)
            self.legacy_predecessor = bool(
                self.previous and pid_alive(self.previous.pid)
            )
            if self.legacy_predecessor:
                from .service_predecessor import Predecessor

                self.predecessor = await asyncio.to_thread(
                    Predecessor.capture, self.previous, home_dir(),
                    getattr(self.service, "_instance_registry_root", None),
                )
                try:
                    self.receiver = AuthReceiver(self.root, self.previous.pid, PROCESS_GENERATION)
                    self.receiver.start()
                except Exception:
                    self.handoff_result = "private-channel-unavailable"
            replacing = bool(self.previous and pid_alive(self.previous.pid))
            self.service._runtime_can_work = self._take_writer()
            await self._capture_writer()
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
                self._record_socket(self.socket)
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
            if self.legacy_predecessor or self.writer_predecessor is not None:
                from .service_predecessor import GRACE_SECONDS

                self.predecessor_deadline = time.monotonic() + GRACE_SECONDS
                self.predecessor_retirement = "grace"
        finally:
            self.publication.release()

    def _write_pid(self) -> None:
        path = Path(self.api.PID_FILE)
        pending = path.with_name(path.name + f".{os.getpid()}.pending")
        pending.write_text(str(os.getpid()), encoding="utf-8")
        pending.replace(path)

    def _publish_writer_pid(self) -> bool:
        try:
            self.publication.acquire()
        except AlreadyRunningError:
            return False
        try:
            endpoint = read_endpoint(self.root)
            if not (
                selected() and endpoint and endpoint.pid == os.getpid()
                and endpoint.installation_id == installation_id()
            ):
                return False
            self._write_pid()
            return True
        finally:
            self.publication.release()

    def _record_socket(self, path: str) -> None:
        identity = socket_identity(path)
        if identity is None:
            raise OSError("Bound Unix socket identity unavailable")
        self.owned_sockets[path] = identity
        if path == self.api.SOCKET_PATH:
            self.unix_alias.publish()

    async def _legacy_aliases(self) -> bool:
        try:
            self.publication.acquire()
        except AlreadyRunningError:
            return False
        try:
            endpoint = read_endpoint(self.root)
            if not (
                selected() and endpoint and endpoint.pid == os.getpid()
                and endpoint.installation_id == installation_id()
            ):
                return False
            await self._bind_legacy_aliases()
            return bool(
                (not self.socket or self.api.SOCKET_PATH in self.owned_sockets)
                and (not self.pipe or self.pipe == self.api.PIPE_PATH or self.legacy_pipe_bound)
                and (
                    not self.requested_port or self.port == self.requested_port
                    or self.legacy_tcp_bound
                )
            )
        finally:
            self.publication.release()

    async def _bind_legacy_aliases(self) -> None:
        api = self.api
        if self.socket and self.socket != api.SOCKET_PATH:
            identity = self.unix_alias.dead_identity()
            if (
                identity is not None
                and socket_identity(api.SOCKET_PATH) == identity
                and await asyncio.to_thread(self._unix_stale, api.SOCKET_PATH)
                and socket_identity(api.SOCKET_PATH) == identity
            ):
                try:
                    os.unlink(api.SOCKET_PATH)
                except OSError:
                    log.warning("Legacy Unix alias unavailable; generation discovery remains active")
        if self.socket and self.socket != api.SOCKET_PATH and not os.path.lexists(api.SOCKET_PATH):
            try:
                listener = await asyncio.start_unix_server(
                    self.connections.callback("unix"), path=api.SOCKET_PATH,
                )
                self.servers.append(listener)
                os.chmod(api.SOCKET_PATH, 0o600)
                self._record_socket(api.SOCKET_PATH)
            except OSError:
                log.warning("Legacy Unix alias unavailable; generation discovery remains active")
        if self.pipe and self.pipe != api.PIPE_PATH and not self.legacy_pipe_bound:
            if await asyncio.to_thread(api.pipe_absent, api.PIPE_PATH):
                try:
                    servers = await api.start_pipe_server(
                        api.PIPE_PATH, self.connections.callback("pipe"),
                    )
                    self.pipes.extend(servers)
                    self.legacy_pipe_bound = bool(servers)
                except Exception:
                    log.warning("Legacy pipe alias unavailable; generation discovery remains active")
        if (
            self.requested_port and self.port != self.requested_port
            and not self.legacy_tcp_bound
        ):
            try:
                await self._tcp(self.requested_port)
                self.legacy_tcp_bound = True
            except OSError:
                log.warning("Legacy TCP alias unavailable; generation discovery remains active")

    @staticmethod
    def _unix_stale(path: str) -> bool:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(.25)
            try:
                peer.connect(path)
            except OSError as error:
                return error.errno in {errno.ECONNREFUSED, errno.ENOENT}
        return False

    async def _capture_writer(self) -> None:
        pid = self.writer_candidate_pid
        if (
            pid is None or (self.previous and pid == self.previous.pid)
            or self.writer_predecessor_retirement != "not-requested"
        ):
            return
        from .service_predecessor import Predecessor

        self.writer_predecessor = await asyncio.to_thread(
            Predecessor.capture_pid, pid, home_dir(),
            getattr(self.service, "_instance_registry_root", None),
        )
        self.writer_predecessor_retirement = (
            "grace" if self.writer_predecessor else "refused-owner"
        )

    async def tick(self) -> None:
        recovery_settled = await self._recover_predecessors()
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
        if not recovery_settled or not selected() or endpoint is None:
            return
        previous_exited = self.predecessor_retirement in {"exited", "cold-retired"}
        if self.previous and not previous_exited:
            previous_exited = bool(
                (self.predecessor and self.predecessor.exited())
                or not pid_alive(self.previous.pid)
            )
            if previous_exited:
                self.predecessor_retirement = "exited"
        if self.predecessor_retirement == "grace":
            if previous_exited:
                self.predecessor_retirement = "exited"
            elif time.monotonic() >= self.predecessor_deadline:
                self.predecessor_retirement = await settle_owned(asyncio.to_thread(
                    self._retire_previous,
                ))
                previous_exited = bool(
                    self.predecessor and self.predecessor.exited()
                )
        if self.writer_predecessor_retirement == "grace":
            if self.writer_predecessor.exited():
                self.writer_predecessor_retirement = "exited"
            elif time.monotonic() >= self.predecessor_deadline:
                self.writer_predecessor_retirement = await settle_owned(asyncio.to_thread(
                    self._retire_previous, self.writer_predecessor, True,
                ))
        if not self._take_writer():
            await self._capture_writer()
            return
        if not self.hooks_started:
            await self.close_handoff()
            if not self._publish_writer_pid():
                return
            self.service._runtime_can_work = True
            context = self.api.resolve_context()
            self.api.get_registry().run_startup(
                self.service,
                self.api.StartupContext(
                    kpdb=context.kpdb, group=context.group, vault_name=context.vault_name or "",
                    port=self.port or self.requested_port, run_dir=str(self.root),
                ),
            )
            self.hooks_started = True
        if not self.aliases_after_exit:
            self.aliases_after_exit = await self._legacy_aliases()

    async def _recover_predecessors(self) -> bool:
        settled = True
        for target, status in (
            (self.predecessor, "predecessor_retirement"),
            (self.writer_predecessor, "writer_predecessor_retirement"),
        ):
            if target is not None and getattr(target, "custody", None) is not None:
                if await settle_owned(asyncio.to_thread(target.recover)):
                    setattr(self, status, "refused-native-retirement")
                else:
                    settled = False
        return settled

    async def settle_retirements(self) -> None:
        self.service._runtime_can_work = False
        while not await self._recover_predecessors():
            await asyncio.sleep(.5)

    async def _retire(self) -> None:
        if self.retiring is None:
            return
        self.service._runtime_can_work = False
        if self.connections.work:
            return
        expected = selected_generation()
        if expected is None or self.retiring.get("runtime_generation") == expected:
            from .cutover import build_auth_state

            payload = build_auth_state(self.service)
            released = self._release_writer()
            await settle_owned(asyncio.to_thread(
                send_auth_state, self.retiring, payload, writer_released=released,
            ))
        else:
            self._release_writer()
        self.service._shutdown = True

    def _release_writer(self) -> bool:
        if not getattr(self, "has_writer", False):
            return False
        self.writer.release()
        self.has_writer = False
        return True

    def _retire_previous(self, predecessor=None, writer: bool = False) -> str:
        predecessor = predecessor if predecessor is not None else self.predecessor
        if predecessor is None:
            return "refused-owner"
        try:
            self.publication.acquire()
        except AlreadyRunningError:
            return "refused-publication"
        try:
            def current() -> bool:
                endpoint = read_endpoint(self.root)
                selected_owner = bool(
                    selected() and endpoint and endpoint.pid == os.getpid()
                    and endpoint.installation_id == installation_id()
                )
                if not selected_owner or not writer:
                    return selected_owner
                if self.has_writer:
                    return False
                try:
                    self.writer.acquire()
                except AlreadyRunningError as error:
                    return error.holder_pid == predecessor.pid
                self.writer.release()
                return False

            try:
                with selection_custody(home_dir(), PROCESS_SLOT):
                    result = predecessor.retire(current)
            except SelectionCustodyUnavailable:
                return "refused-selection-custody"
            log.info("Vault predecessor retirement: %s", result)
            return result
        finally:
            self.publication.release()

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
        try:
            self.publication.acquire()
        except AlreadyRunningError:
            pass
        else:
            try:
                self.api.clear_endpoint(self.root)
                for path, identity in self.owned_sockets.items():
                    if path == self.api.SOCKET_PATH and not self.unix_alias.owned_by_current():
                        continue
                    if socket_identity(path) == identity:
                        with contextlib.suppress(FileNotFoundError):
                            os.unlink(path)
                pid = Path(self.api.PID_FILE)
                with contextlib.suppress(FileNotFoundError, ValueError):
                    if int(pid.read_text(encoding="utf-8").strip()) == os.getpid():
                        pid.unlink()
            finally:
                self.publication.release()
        self._release_writer()
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
