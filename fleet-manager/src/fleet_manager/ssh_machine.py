"""Configured Fleet lifetime for existing SSH notifications and local writers."""

from __future__ import annotations

import asyncio
from functools import partial
import logging
import os
import signal
import stat
import sys

from .planning import PlanError
from .private_state import create_private_file, private_directory, windows_private
from .ssh_machine_config import SSHMachineConfig, absolute

LEASE = "fleet-ssh-client.lock"
_LOG = logging.getLogger(__name__)


def _consumer(config):
    from agent_ssh.gateway_directory import apply_update, validate_consumer_state

    value = config.to_dict()
    validate_consumer_state(
        absolute(value["state_dir"]), config_d=absolute(value["config_d"]),
        ssh_config=absolute(value["ssh_config"]),
        authorization_dir=absolute(value["authorization_dir"]) if value["authorization_dir"] else None,
    )
    return partial(apply_update, **config.local_scope())


def _lease(root):
    from single_instance_lease import SingleInstance

    private_directory(root)
    if {path.name for path in root.iterdir()} - {LEASE}:
        raise PlanError("SSH client state directory contains unowned files")
    path = absolute(str(root / LEASE))
    try:
        descriptor = create_private_file(path)
    except FileExistsError:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PlanError("SSH client lease is not an ordinary owned file")
        if os.name == "nt":
            windows_private(path)
        elif info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PlanError("SSH client lease is not private to its owner")
    else:
        os.close(descriptor)
    return SingleInstance(str(root), service="fleet-ssh-client")


async def _settle(task):
    joined = asyncio.gather(task, return_exceptions=True)
    interrupted = False
    while True:
        try:
            return (await asyncio.shield(joined)), interrupted
        except asyncio.CancelledError:
            interrupted = True


async def run(
    config: SSHMachineConfig, *, stop: asyncio.Event | None = None,
    install_signals: bool = False,
) -> None:
    """No enrollment or service action; the embedding owns this foreground task."""
    stop = stop if stop is not None else asyncio.Event()
    if stop.is_set():
        return
    config = SSHMachineConfig.from_dict(config.to_dict())
    accept = _consumer(config)
    from .credential_references import load_service_credentials
    from .gateway_service_caller import ServiceCaller
    from .ssh_directory import SSHDirectoryClient

    value = config.to_dict()
    credentials = load_service_credentials(
        proof_key_file=absolute(value["proof_key_file"]),
        access_token_file=absolute(value["access_token_file"]),
    )
    caller = ServiceCaller(
        gateway_origin=value["gateway_origin"], gateway_ca=value["gateway_ca"],
        access_token_callback=credentials.access_token, proof_key=credentials.proof_key,
        timeout=35,
    )
    client = SSHDirectoryClient(caller, namespace=value["namespace"])
    lease = _lease(absolute(value["state_dir"]))
    lease.acquire()
    previous, task = {}, None
    loop = asyncio.get_running_loop()

    def withdraw(_signal, _frame):
        loop.call_soon_threadsafe(stop.set)

    def apply(update):
        # The existing writers are synchronous: cancellation cannot orphan a write.
        result = accept(update)
        _LOG.info("ssh-client-directory-applied revision=%s", result["directory_revision"])

    try:
        if install_signals:
            for selected in (signal.SIGINT, signal.SIGTERM):
                previous[selected] = signal.signal(selected, withdraw)
        task = asyncio.create_task(client.watch(stop, apply))
        await asyncio.shield(task)
        if not stop.is_set():
            raise PlanError("SSH client lifetime ended without a stop request")
    finally:
        stop.set()
        results, interrupted = [], False
        try:
            if task is not None:
                task.cancel()
                results, interrupted = await _settle(task)
        finally:
            for selected, handler in previous.items():
                signal.signal(selected, handler)
            lease.release()
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                raise result
        if interrupted:
            raise asyncio.CancelledError


def serve(path) -> int:
    try:
        from .gateway_contract import GatewayError
        from .gateway_service_channel import TransportError

        asyncio.run(run(SSHMachineConfig.load(path), install_signals=True))
    except ImportError:
        print("fleet-manager: SSH hosting requires the ssh-client extra", file=sys.stderr)
        return 1
    except (GatewayError, TransportError, OSError, ValueError, RuntimeError) as exc:
        print(f"fleet-manager: {exc}", file=sys.stderr)
        return 1
    return 0
