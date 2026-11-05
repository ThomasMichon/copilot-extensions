"""Service-local native control using the existing ZDD authenticated channels."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from concurrent.futures import Future
from contextlib import contextmanager
import hashlib
import logging
import os
from pathlib import Path
import secrets
import threading
import time
from tempfile import mkdtemp
from collections.abc import Callable, Iterator
from typing import Any, TYPE_CHECKING, cast

from fleet_manager.private_state import create_private_file, private_directory, read_private_file
from zdd.conditional import RuntimeAttestation, RuntimePreparation
from zdd.diagnostics import lock_data_is_live, process_start_time
from zdd.private_protocol import Channel, Deadline, PeerIdentity, read_frame as read_frame, write_frame

from .config import Document, ServiceConfig, absolute, canonical, check, decode

if TYPE_CHECKING:
    from fleet_manager.gateway_service_connector import ServiceConnector
    from single_instance_lease import SingleInstance
    from zdd.private_unix import UnixListener
    from zdd.private_windows import WindowsListener
    from .gateway import SSHService
    from .relay import NativeRelay, PosixOperations
    from .store import DirectoryStore

MAX_CONTROL = 16384
CONTROL_CADENCE = 0.05


def current_peer() -> PeerIdentity:
    if os.name == "nt":
        from zdd.private_windows_native import native

        principal = native().current_sid()
    else:
        principal = str(cast("PosixOperations", os).geteuid())
    return PeerIdentity(os.getpid(), cast(str, process_start_time(os.getpid())), principal)


def config_identity(binding: Any, config: ServiceConfig) -> str:
    return hashlib.sha256(canonical({
        "root": str(binding.root), "slot": binding.slot.name,
        "source": binding.identity.document(), "config": config.to_dict(),
    })).hexdigest()


def writer_identity(peer: Document, identity: str) -> str:
    return hashlib.sha256(canonical({"peer": peer, "identity": identity})).hexdigest()


def cell_directory(config: ServiceConfig) -> Path:
    return absolute(config.to_dict()["data_dir"]).resolve() / ".local-service"


def publish_new(path: Path, value: Document) -> None:
    """Publish a complete new owner/request without replacing an existing entry."""
    data = canonical(value)
    check(len(data) <= MAX_CONTROL, "local-control-limit")
    temporary = path.with_name("." + secrets.token_hex(16) + ".tmp")
    try:
        with os.fdopen(create_private_file(temporary), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def records(config: ServiceConfig) -> list[Document]:
    cell = cell_directory(config)
    if not cell.exists():
        return []
    private_directory(cell)
    paths = sorted(cell.glob("owner-*.json"))
    result = []
    for path in paths:
        try:
            identity = path.lstat()
            content = read_private_file(path, MAX_CONTROL)
        except FileNotFoundError:
            continue
        value = decode(content)
        check(isinstance(value, dict) and set(value) == {
            "peer", "root", "slot", "source", "config_identity",
        }, "invalid-local-owner-record")
        PeerIdentity(**value["peer"])
        if not lock_data_is_live(value["peer"]):
            try:
                observed = path.lstat()
                check((observed.st_dev, observed.st_ino, observed.st_size, observed.st_mtime_ns)
                      == (identity.st_dev, identity.st_ino, identity.st_size, identity.st_mtime_ns)
                      and read_private_file(path, MAX_CONTROL) == content,
                      "local-owner-record-changed")
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        result.append(value)
        check(len(result) <= 32, "local-owner-inventory-limit")
    return result


def attestation(record: Document, result: Document, operation: str) -> Document:
    expected = PeerIdentity(**record["peer"])
    evidence = "prepared" if operation == "prepare" else "ready"
    check(isinstance(result, dict) and set(result) == {
        "pid", "process_start_time", "version", "config_identity", evidence,
    }, "invalid-local-attestation")
    check(result["pid"] == expected.pid
          and result["process_start_time"] == expected.start_time
          and result["version"] == record["slot"]
          and result["config_identity"] == record["config_identity"]
          and type(result[evidence]) is bool, "local-attestation-mismatch")
    return result


def remove_request(path: Path, deadline: Deadline) -> None:
    while True:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError as error:
            if getattr(error, "winerror", None) not in (32, 33):
                raise
            time.sleep(min(CONTROL_CADENCE, deadline.remaining()))


@contextmanager
def _request_listener(nonce: str) -> Iterator[tuple[WindowsListener | UnixListener, str]]:
    listener: WindowsListener | UnixListener | None = None
    directory = None
    identity = None
    try:
        if os.name == "nt":
            from zdd.private_windows import WindowsListener

            address = "\\\\.\\pipe\\agent-ssh-service-" + nonce
            listener = WindowsListener(address)
        else:
            from zdd.private_unix import UnixListener

            directory = Path(mkdtemp(prefix="ssh-"))
            identity = directory.lstat()
            private_directory(directory)
            address = str(directory / "c")
            check(len(os.fsencode(address)) <= 107, "local-control-socket-path-limit")
            listener = UnixListener(Path(address))
        yield listener, address
    finally:
        try:
            if listener is not None:
                listener.close()
        finally:
            if directory is not None and identity is not None:
                current = directory.lstat()
                check((current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)
                      and not directory.is_symlink()
                      and not getattr(current, "st_file_attributes", 0) & 0x400,
                      "local-control-directory-changed")
                directory.rmdir()  # Never recursively erase an unexpected replacement.


def request(config: ServiceConfig, record: Document, operation: str, *,
            wait_exit: bool = False, timeout: float | None = None) -> Document:
    """Discovery is not health: authenticate and retain the actual service peer."""
    check(operation in ("prepare", "activate", "health", "stop"), "invalid-local-control-operation")
    cell = cell_directory(config)
    private_directory(cell)
    peer, expected = current_peer(), PeerIdentity(**record["peer"])
    nonce = secrets.token_hex(16)
    path = cell / ("request-" + nonce + ".json")
    channel: Channel | None = None
    held = None
    limit = 15 if operation == "activate" else 10 if wait_exit else 3
    deadline = Deadline(limit if timeout is None else min(limit, max(0, timeout)))
    with _request_listener(nonce) as (listener, address):
        try:
            publish_new(path, {
                "peer": asdict(peer), "address": address, "operation": operation,
                "target": record["peer"], "config_identity": record["config_identity"],
            })
            channel = listener.accept(deadline, expected=expected)
            held = channel.retain_peer(deadline)
            write_frame(channel, {"operation": operation}, deadline)
            result = attestation(record, read_frame(channel, deadline), operation)
            channel.close()
            channel = None
            if wait_exit:
                held.wait_exit(deadline)
            return result
        finally:
            if held is not None:
                held.close()
            if channel is not None:
                channel.close()
            remove_request(path, deadline)


class LocalControl:
    """One listener-registration worker inside the existing foreground process."""

    def __init__(self, binding: Any, config: ServiceConfig, *,
                 selected: Callable[[Any], None] | None = None) -> None:
        from .source_start import ensure_selected
        from .writer import FencedWriter

        self.binding, self.config = binding, config
        self.peer = current_peer()
        self.identity = config_identity(binding, config)
        self.closed = threading.Event()
        self.stop = asyncio.Event()
        self.thread: threading.Thread | None = None
        self.record: Path | None = None
        self.handled: set[str] = set()
        self.work_lock = threading.Lock()
        self.pending: Future[Document] | None = None
        self.operations: set[asyncio.Task[Document]] = set()
        owner = writer_identity(asdict(self.peer), self.identity)
        def selected_writer() -> None:
            check(not self.closed.is_set() and not self.stop.is_set(), "local-service-stopping")
            (selected or ensure_selected)(binding)
            check(not self.closed.is_set() and not self.stop.is_set(), "local-service-stopping")
        self.writer = FencedWriter(config, owner, selected=selected_writer)
        self.service: SSHService | None = None
        self.connector: ServiceConnector | None = None
        self.relay: NativeRelay | None = None
        self.read_selection: Callable[[], str | None] | None = None

    async def prepare(self, *, stop: asyncio.Event, lease: SingleInstance) -> None:
        check("relay" not in self.config.to_dict(), "native-relay-transfer-unsupported")
        self.stop, self.lease = stop, lease
        self.loop = asyncio.get_running_loop()
        self.activation = asyncio.Event()
        self.initialized = asyncio.Event()
        self.cell = cell_directory(self.config)
        private_directory(self.cell)
        self.record = self.cell / ("owner-" + str(self.peer.pid) + "-" + self.peer.start_time + ".json")
        self.record_data = {
            "peer": asdict(self.peer), "root": str(self.binding.root),
            "slot": self.binding.slot.name, "source": self.binding.identity.document(),
            "config_identity": self.identity,
        }
        publish_new(self.record, self.record_data)
        self.thread = threading.Thread(target=self._serve, name="ssh-local-control", daemon=False)
        self.thread.start()

    async def wait_activation(self) -> bool:
        tasks = [asyncio.create_task(event.wait()) for event in (self.activation, self.stop)]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            return self.activation.is_set() and not self.stop.is_set()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def attach(self, *, store: DirectoryStore, service: SSHService,
               connector: ServiceConnector, relay: NativeRelay | None,
               stop: asyncio.Event, lease: SingleInstance) -> None:
        self.store, self.service, self.connector, self.relay = store, service, connector, relay
        self.initialized.set()

    async def _attest(self) -> Document:
        ready = (
            self.service is not None and self.writer.current()
            and not self.stop.is_set() and not self.service.closed
            and cast("ServiceConnector", self.connector).connected.is_set()
            and (self.relay is None or (
                self.relay.process is not None and self.relay.process.returncode is None
            ))
        )
        if ready:
            from .config import SSHServiceError

            try:
                from .gateway import _drain

                result, interrupted = await _drain(asyncio.create_task(asyncio.to_thread(self.store.snapshot)))
                if interrupted:
                    raise asyncio.CancelledError
                if isinstance(result[0], BaseException):
                    raise result[0]
            except SSHServiceError:
                ready = False
        return asdict(RuntimeAttestation(
            self.peer.pid, self.peer.start_time, self.binding.slot.name, self.identity, ready,
        ))

    async def _operate(self, operation: str) -> Document:
        if self.closed.is_set():
            raise asyncio.CancelledError
        task = cast(asyncio.Task[Document], asyncio.current_task())
        self.operations.add(task)
        try:
            return await self._operation(operation)
        finally:
            self.operations.discard(task)

    async def _operation(self, operation: str) -> Document:
        if operation == "prepare":
            return asdict(RuntimePreparation(
                self.peer.pid, self.peer.start_time, self.binding.slot.name,
                self.identity, not self.stop.is_set() and (
                    not self.initialized.is_set() or self.writer.current()
                ),
            ))
        if operation == "activate":
            self.activation.set()
            await self.initialized.wait()
            await cast("ServiceConnector", self.connector).connected.wait()
        return await self._attest()

    def _supersession_unchanged(self, owner: str, selection: str) -> bool:
        return (
            not self.closed.is_set() and not self.stop.is_set()
            and owner != self.writer.owner
            and self.writer.observed_owner() == owner
            and cast(Callable[[], str | None], self.read_selection)() == selection
        )

    def _stop_if_superseded(self, owner: str, selection: str) -> None:
        try:
            if self._supersession_unchanged(owner, selection):
                self.stop.set()
        except (OSError, ValueError, RuntimeError):
            return

    def _observe_supersession(self) -> None:
        """Only this outgoing owner retires, and only from current ready evidence."""
        try:
            if not self.initialized.is_set() or self.closed.is_set() or self.stop.is_set():
                return
            owner = self.writer.observed_owner()
            if owner is None or owner == self.writer.owner:
                return
            if self.read_selection is None:
                from .source_start import selection_reader

                self.read_selection = selection_reader(self.binding)
            selection = self.read_selection()
            if not selection:
                return
            for record in records(self.config):
                if record["slot"] != selection:
                    continue
                peer = PeerIdentity(**record["peer"])
                if peer == self.peer or peer.principal != self.peer.principal:
                    continue
                try:
                    same_root = absolute(record["root"]).samefile(self.binding.root)
                except OSError:
                    continue
                if not same_root:
                    continue
                if writer_identity(record["peer"], record["config_identity"]) != owner:
                    continue
                result = attestation(
                    record, request(self.config, record, "health"), "health",
                )
                if result["ready"] and self._supersession_unchanged(owner, selection):
                    self.loop.call_soon_threadsafe(
                        lambda: self._stop_if_superseded(owner, selection),
                    )
                return
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            # Missing/changed native readiness is not retirement authority.
            return

    def _serve(self) -> None:
        while not self.closed.wait(CONTROL_CADENCE):
            self._observe_supersession()
            paths = sorted(self.cell.glob("request-*.json"))
            self.handled.intersection_update(path.name for path in paths)
            remaining = 32
            for path in paths:
                if self.closed.is_set() or not remaining:
                    break
                if path.name in self.handled:
                    continue
                remaining -= 1
                self.handled.add(path.name)
                channel: Channel | None = None
                try:
                    value = decode(read_private_file(path, MAX_CONTROL))
                    check(set(value) == {"peer", "address", "operation", "target", "config_identity"},
                          "invalid-local-registration")
                    if value["target"] != asdict(self.peer):
                        continue
                    check(value["config_identity"] == self.identity, "local-config-mismatch")
                    check(value["operation"] in ("prepare", "activate", "health", "stop"),
                          "invalid-local-operation")
                    expected = PeerIdentity(**value["peer"])
                    check(expected.principal == self.peer.principal, "local-principal-mismatch")
                    deadline = Deadline(14 if value["operation"] == "activate" else 2)
                    transport_deadline = Deadline(min(2, deadline.remaining()))
                    if os.name == "nt":
                        from zdd.private_windows import connect as windows_connect
                        channel = windows_connect(value["address"], expected, transport_deadline)
                    else:
                        from zdd.private_unix import connect as unix_connect
                        channel = unix_connect(Path(value["address"]), expected, transport_deadline)
                    frame = read_frame(channel, transport_deadline)
                    check(frame == {"operation": value["operation"]}, "local-operation-mismatch")
                    with self.work_lock:
                        if self.closed.is_set():
                            break
                        pending = asyncio.run_coroutine_threadsafe(
                            self._operate(value["operation"]), self.loop,
                        )
                        self.pending = pending
                    try:
                        result = pending.result(timeout=deadline.remaining())
                    finally:
                        if not pending.done():
                            pending.cancel()
                        with self.work_lock:
                            self.pending = None
                    if self.closed.is_set():
                        break
                    write_frame(channel, result, Deadline(min(2, deadline.remaining())))
                    if value["operation"] == "stop":
                        self.loop.call_soon_threadsafe(self.stop.set)
                except Exception as error:
                    logging.getLogger(__name__).warning(
                        "local-control-request-refused type=%s", type(error).__name__,
                    )
                    continue
                finally:
                    if channel is not None:
                        channel.close()

    async def close(self) -> None:
        from .gateway import _drain

        with self.work_lock:
            self.closed.set()
            if self.pending is not None:
                self.pending.cancel()
        operations = tuple(self.operations)
        for task in operations:
            task.cancel()
        _, interrupted = await _drain(*operations)
        if self.thread is not None:
            _, joining_interrupted = await _drain(asyncio.create_task(
                asyncio.to_thread(self.thread.join, 5),
            ))
            interrupted |= joining_interrupted
            check(not self.thread.is_alive(), "local-control-cleanup-unconfirmed")
        if self.record is not None:
            if decode(read_private_file(self.record, MAX_CONTROL)) != self.record_data:
                raise RuntimeError("local-owner-record-changed")
            self.record.unlink()
        if interrupted:
            raise asyncio.CancelledError
