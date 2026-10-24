"""Foreground Gateway registration; the existing deployment owner supervises it."""

from __future__ import annotations

import asyncio
import os
import signal

from fleet_manager.credential_references import load_service_credentials
from fleet_manager.gateway_service_connector import ServiceConnector
from single_instance_lease import SingleInstance

from .config import ServiceConfig, absolute, check
from .gateway import SSHService
from .store import DirectoryStore


class UnsettledRelay(RuntimeError):
    def __init__(self, relay, lease):
        self.relay, self.lease = relay, lease
        super().__init__("native-relay-cleanup-unconfirmed")


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
                await asyncio.gather(readiness, stopping, return_exceptions=True)
        tasks.append(asyncio.create_task(connector.run(stop)))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
        check(stop.is_set(), "registered-ssh-lifetime-ended")
    finally:
        stop.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result


async def run(config: ServiceConfig, *, stop: asyncio.Event | None = None) -> None:
    stop = stop if stop is not None else asyncio.Event()
    if stop.is_set():
        return
    selected = config.to_dict()
    root = absolute(selected["data_dir"])
    root.mkdir(mode=0o700, exist_ok=True)
    check(root.is_dir() and (os.name == "nt" or (
        root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o077 == 0
    )), "service-data-directory-not-private")
    check((root / "directory.sqlite3").exists() or not any(root.iterdir()),
          "unowned-service-data-directory")
    credentials = load_service_credentials(
        proof_key_file=absolute(selected["proof_key_file"]),
        access_token_file=absolute(selected["access_token_file"]),
    )
    lease = SingleInstance(str(root), service="agent-ssh-service")
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
        if service is not None:
            await service.close()
        for selected_signal, handler in previous.items():
            signal.signal(selected_signal, handler)
        if relay is not None and relay.process is not None and relay.process.returncode is None:
            raise UnsettledRelay(relay, lease)
        lease.release()
