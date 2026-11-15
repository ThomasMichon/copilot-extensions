"""Serving lifetime and typed adapters for legacy connection custody."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Literal, ParamSpec, Protocol, TypeVar

from agent_procutil.daemon_instances import daemon_entrypoint as _daemon_entrypoint

from .service_connections import Closable as PipeServer, VaultConnections, settle_owned

if TYPE_CHECKING:
    from .service import VaultService

P = ParamSpec("P")
R = TypeVar("R")


class EntrypointDecorator(Protocol):
    def __call__(self, function: Callable[P, R]) -> Callable[P, R]: ...


def daemon_entrypoint(
    plugin: str, *, source: str,
    purpose: Literal["production", "unattended-test", "interactive-test"],
) -> EntrypointDecorator:
    decorator: EntrypointDecorator = _daemon_entrypoint(plugin, source=source, purpose=purpose)
    return decorator


async def serve(
    service: VaultService, tcp_port: int | None, servers: list[asyncio.Server],
    pipes: list[PipeServer], connections: VaultConnections,
) -> None:
    from . import service as api
    from .federated_relay import selected_relay

    generation = service._generation = api.ServiceGeneration(service, connections, servers, pipes)
    relay = service._federated_relay = selected_relay()
    try:
        await generation.start(tcp_port)
        loop = asyncio.get_running_loop()
        warmer = api.CacheWarmRuntime(service, service._cache_warm, connections)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="vault-expiry") as expiry:
            try:
                while not service._shutdown and not service.is_expired:
                    await settle_owned(loop.run_in_executor(expiry, service._check_password_ttl))
                    await generation.tick()
                    warmer.tick()
                    if relay is not None:
                        await relay.tick(serving=(
                            generation.hooks_started and service._runtime_can_work
                            and not service._shutdown
                        ))
                    await asyncio.sleep(0.5)
            finally:
                try:
                    if relay is not None:
                        await settle_owned(relay.close())
                        relay = None
                finally:
                    await settle_owned(warmer.close())
    finally:
        if relay is not None:
            await settle_owned(relay.close())
