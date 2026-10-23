"""Foreground lifetime and passive readiness; placement/supervision stay external."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import threading
import time
import uuid
from pathlib import Path

from agent_pull_requests.watch_service import WatchService
from fleet_manager.credential_references import load_service_credentials
from fleet_manager.gateway_service_handler import RequestDisconnected

from .config import HostConfig


class LaunchScope:
    """The explicitly launched local process, not a synthetic fleet permit.

    The supervisor owns launch/withdrawal. This guard bounds local transactions;
    the actual Gateway and credential worker separately authorize remote work.
    It never parses token claims or grants placement from a role declaration.
    """

    def __init__(self, operation_seconds: int) -> None:
        self.pid = os.getpid()
        self.stopping = threading.Event()
        self.operation_seconds = operation_seconds

    def check(self) -> None:
        if self.stopping.is_set() or os.getpid() != self.pid:
            raise RequestDisconnected()

    def deadline(self) -> int:
        self.check()
        return int(time.time()) + self.operation_seconds


def _publish(path: Path, status: dict) -> bool:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(status, stream, allow_nan=False)
            stream.flush()
        try:
            os.replace(temporary, path)
        except PermissionError as error:
            if os.name != "nt" or getattr(error, "winerror", None) not in {5, 32}:
                raise
            # A Windows health reader can deny rename/delete sharing. Leave the
            # prior observation to expire, rather than stopping the writer.
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def ready(path: Path, *, now: float | None = None) -> bool:
    """Read a bounded-age local observation, never an activation/ownership receipt."""
    try:
        if not path.is_absolute() or path.is_symlink():
            return False
        with path.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            return False
        from agent_pull_requests.watch_replay_store import json_object

        value = json_object(raw)
        instant = time.time() if now is None else now
        return bool(
            value.get("schema") == "agent-pull-requests.service-status"
            and type(value.get("version")) is int and value["version"] == 1
            and value.get("ready") is True
            and type(value.get("observed_at")) is int
            and type(value.get("expires_at")) is int
            and value["observed_at"] <= instant < value["expires_at"]
            and 0 < value["expires_at"] - value["observed_at"] <= 3
        )
    except (OSError, ValueError, UnicodeError, RecursionError):
        return False


async def run(config: HostConfig, *, stop: asyncio.Event | None = None) -> None:
    """Adopt existing state, then own exactly the existing WatchService lifetime."""
    stop = stop if stop is not None else asyncio.Event()
    if stop.is_set():
        return
    scope = LaunchScope(config.operation_seconds)
    credentials = load_service_credentials(
        proof_key_file=config.proof_key_file, access_token_file=config.access_token_file,
    )
    service = WatchService(
        config.store(), fleet=config.fleet, gateway=config.gateway, slot=config.slot,
        bindings=config.bindings, observations=config.observations,
        gateway_origin=config.gateway_origin, access_token_callback=credentials.access_token,
        proof_key=credentials.proof_key, authority=scope.deadline, checkpoint=scope.check,
        gateway_ca=str(config.gateway_ca) if config.gateway_ca else None,
        interval=config.interval,
    )
    loop = asyncio.get_running_loop()
    previous = {}

    def withdraw(_signal=None, _frame=None) -> None:
        scope.stopping.set()
        loop.call_soon_threadsafe(stop.set)

    try:
        for selected in (signal.SIGINT, signal.SIGTERM):
            previous[selected] = signal.signal(selected, withdraw)
    except ValueError:
        # Embedding off the main thread supplies its own stop event.
        for selected, handler in previous.items():
            signal.signal(selected, handler)
        previous.clear()

    async def observe() -> None:
        while True:
            if stop.is_set():
                scope.stopping.set()
            status = service.status()
            if status["writer_held"]:
                instant = int(time.time())
                _publish(config.status_path, {
                    "schema": "agent-pull-requests.service-status", "version": 1,
                    **status, "ready": status["ready"] and not stop.is_set(),
                    "observed_at": instant, "expires_at": instant + 3,
                })
            await asyncio.sleep(1)

    observer = asyncio.create_task(observe())
    owner = asyncio.create_task(service.run(stop, drain_timeout=config.drain_timeout))
    try:
        done, _ = await asyncio.wait({owner, observer}, return_when=asyncio.FIRST_COMPLETED)
        if observer in done:
            await observer
        await owner
    finally:
        scope.stopping.set()
        stop.set()
        # Never cancel the owner's bounded drain or release its custody early.
        try:
            await asyncio.shield(owner)
        finally:
            observer.cancel()
            await asyncio.gather(observer, return_exceptions=True)
            for selected, handler in previous.items():
                signal.signal(selected, handler)
