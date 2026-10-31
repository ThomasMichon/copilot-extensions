"""Foreground Gateway registration; the existing deployment owner supervises it."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from fleet_manager.credential_references import load_service_credentials
from fleet_manager.gateway_service_connector import ServiceConnector
from fleet_manager.private_state import create_private_file, private_directory, windows_private
from single_instance_lease import SingleInstance

from .config import ServiceConfig, absolute, check
from .gateway import SSHService, _drain
from .store import DirectoryStore

_LEASE = "agent-ssh-service.lock"


class UnsettledRelay(RuntimeError):
    def __init__(self, relay, lease, previous):
        self.relay, self.lease = relay, lease
        self.previous = previous
        super().__init__("native-relay-cleanup-unconfirmed")

    async def settle(self) -> None:
        from agent_procutil.native_child import NativeChildCleanupError

        while True:
            if self.relay.custody is not None:
                try:
                    self.relay.process.returncode = self.relay.custody.retire()
                except NativeChildCleanupError:
                    pass
                else:
                    self.lease.release()
                    for selected_signal, handler in self.previous.items():
                        signal.signal(selected_signal, handler)
                    self.previous.clear()
                    return
            logging.getLogger(__name__).error(
                "ssh-native-relay-cleanup-unconfirmed code=foreground-custody-retained")
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                continue


async def registered_lifetime(connector, relay, stop) -> None:
    tasks = []
    try:
        if relay is not None:
            ready = asyncio.Event()
            child = asyncio.create_task(relay.run(stop, ready))
            tasks.append(child)
            readiness = asyncio.create_task(ready.wait())
            stopping = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait(
                    {readiness, stopping, child}, return_when=asyncio.FIRST_COMPLETED)
                if child in done:
                    await child
                    check(stop.is_set(), "native-relay-stopped-before-registration")
                if stop.is_set():
                    return
            finally:
                readiness.cancel()
                stopping.cancel()
                await _drain(readiness, stopping)
        tasks.append(asyncio.create_task(connector.run(stop)))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
        check(stop.is_set(), "registered-ssh-lifetime-ended")
    finally:
        stop.set()
        results, interrupted = await _drain(*tasks)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        if interrupted:
            raise asyncio.CancelledError


async def run(config: ServiceConfig, *, stop: asyncio.Event | None = None) -> None:
    stop = stop if stop is not None else asyncio.Event()
    if stop.is_set():
        return
    selected = config.to_dict()
    root = absolute(selected["data_dir"])
    private_directory(root)
    lease_path = absolute(str(root / _LEASE))
    if os.name == "nt":
        for name in ("directory.sqlite3", "directory.sqlite3-journal",
                     "directory.sqlite3-wal", "directory.sqlite3-shm", _LEASE):
            path = absolute(str(root / name))
            if path.exists():
                check(path.is_file() and path.stat().st_nlink == 1, "invalid-directory-store")
                # SQLite creates recovery files with the token's default owner.
                # Privileged ownership does not widen the same closed access grants.
                windows_private(path, privileged_owner=name.startswith("directory.sqlite3-"))
    if lease_path.exists():
        info = lease_path.stat()
        check(lease_path.is_file() and info.st_nlink == 1 and (
            os.name == "nt" or (info.st_uid == os.getuid() and info.st_mode & 0o022 == 0)
        ), "invalid-service-lease")
    check((root / "directory.sqlite3").exists()
          or {entry.name for entry in root.iterdir()} <= {_LEASE},
          "unowned-service-data-directory")
    credentials = load_service_credentials(
        proof_key_file=absolute(selected["proof_key_file"]),
        access_token_file=absolute(selected["access_token_file"]),
    )
    lease = SingleInstance(str(root), service="agent-ssh-service")
    if os.name == "nt":
        try:
            descriptor = create_private_file(lease.lock_path)
        except FileExistsError:
            windows_private(lease.lock_path)
        else:
            os.close(descriptor)
    lease.acquire()
    service = None
    relay = None
    previous = {}
    loop = asyncio.get_running_loop()

    def withdraw(_signal=None, _frame=None):
        loop.call_soon_threadsafe(stop.set)

    try:
        for selected_signal in (signal.SIGINT, signal.SIGTERM):
            previous[selected_signal] = signal.signal(selected_signal, withdraw)
        store = DirectoryStore(config)
        if "relay" in selected:
            from .relay import NativeRelay

            relay = NativeRelay(store)
        service = SSHService(store, reconcile_relay=relay.reconcile if relay else None)
        connector = ServiceConnector(
            slot=selected["slot"], gateway_origin=selected["gateway_origin"],
            access_token_callback=credentials.access_token, proof_key=credentials.proof_key,
            gateway_ca=selected["gateway_ca"], handler=service.binding,
            methods=frozenset({"GET", "POST"}),
        )
        await registered_lifetime(connector, relay, stop)
    finally:
        result, interrupted = [], False
        if service is not None:
            result, interrupted = await _drain(asyncio.create_task(service.close()))
        if relay is not None and relay.process is not None and relay.process.returncode is None:
            raise UnsettledRelay(relay, lease, previous)
        for selected_signal, handler in previous.items():
            signal.signal(selected_signal, handler)
        lease.release()
        for failure in result:
            if isinstance(failure, BaseException):
                raise failure
        if interrupted:
            raise asyncio.CancelledError
