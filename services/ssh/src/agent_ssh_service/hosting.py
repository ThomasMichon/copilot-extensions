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
    previous = {}
    loop = asyncio.get_running_loop()

    def withdraw(_signal=None, _frame=None):
        loop.call_soon_threadsafe(stop.set)

    try:
        for selected_signal in (signal.SIGINT, signal.SIGTERM):
            previous[selected_signal] = signal.signal(selected_signal, withdraw)
        service = SSHService(DirectoryStore(config))
        connector = ServiceConnector(
            slot=selected["slot"], gateway_origin=selected["gateway_origin"],
            access_token_callback=credentials.access_token, proof_key=credentials.proof_key,
            gateway_ca=selected["gateway_ca"], handler=service.binding,
            methods=frozenset({"GET", "POST"}),
        )
        await connector.run(stop)
    finally:
        if service is not None:
            await service.close()
        for selected_signal, handler in previous.items():
            signal.signal(selected_signal, handler)
        lease.release()
