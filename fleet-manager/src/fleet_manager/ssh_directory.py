"""SSH onboarding and bounded directory notifications over the existing Gateway.

The machine's existing Fleet lifetime owner calls ``watch``. This module starts
no daemon, acquires no keys and does not grant a local profile/authorization
writer permission. The explicit local consumer retains that authority.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .gateway_ssh_policy import GatewaySSHPolicy
from .gateway_ssh_provider import directory_snapshot
from .planning import canonical, decode, record

MAX_RESPONSE = 32 * 1024

if TYPE_CHECKING:
    from .gateway_service_caller import ServiceCaller


@dataclass(frozen=True)
class DirectoryUpdate:
    encoded: bytes

    @classmethod
    def parse(cls, raw: bytes) -> DirectoryUpdate:
        value = decode(raw)
        record(value, "agent-ssh.service-directory", {"policy", "directory"})
        policy = GatewaySSHPolicy.from_dict(value["policy"])
        value["directory"] = directory_snapshot(value["directory"], policy)
        return cls(canonical(value))

    def to_dict(self) -> dict:
        return decode(self.encoded)

    @property
    def revision(self) -> int:
        return self.to_dict()["directory"]["revision"]


class SSHDirectoryClient:
    def __init__(self, caller: ServiceCaller, *, namespace: str = "/ssh"):
        from .gateway_service_channel import check, target

        path, query = target(namespace)
        check(not query and path not in ("", "/") and not path.endswith("/"),
              "invalid-ssh-namespace")
        self.caller, self.namespace = caller, path

    async def enroll(self, request: dict) -> DirectoryUpdate:
        """One original request ID; a lost response is not a new enrollment."""
        from .gateway_service_channel import check

        response = await self.caller.request(
            "POST", self.namespace + "/enroll", body=canonical(request),
            response_limit=MAX_RESPONSE,
        )
        check(response.status == 200, "ssh-enrollment-refused", response.status)
        return DirectoryUpdate.parse(response.body)

    async def wait(self, after: int, *, seconds: int = 25) -> DirectoryUpdate | None:
        from .gateway_service_channel import check

        check(type(after) is int and 0 <= after < 2**53
              and type(seconds) is int and 0 <= seconds <= 25,
              "invalid-ssh-notification-position")
        response = await asyncio.wait_for(
            self.caller.request(
                "GET", self.namespace + f"/directory?after={after}&wait={seconds}",
                response_limit=MAX_RESPONSE,
            ),
            seconds + 10,
        )
        if response.status == 204:
            check(not response.body, "invalid-ssh-notification")
            return None
        check(response.status == 200, "ssh-directory-unavailable", response.status)
        update = DirectoryUpdate.parse(response.body)
        check(update.revision > after, "ssh-directory-did-not-advance")
        return update

    async def watch(self, stop: asyncio.Event, accept: Callable[[DirectoryUpdate], None]) -> None:
        """Catch up first; advance only after the local owner's complete write.

        ``accept`` is the synchronous bounded local transaction, not an arbitrary
        remote operation. Its failures propagate and stop the watch. Reconnect
        or process restart begins from zero and revalidates the current snapshot;
        local revision custody/idempotence belongs to that existing writer.
        """
        from .gateway_service_channel import TransportError

        after, backoff = 0, 0
        while not stop.is_set():
            pending = asyncio.create_task(self.wait(after))
            stopped = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait(
                    {pending, stopped}, return_when=asyncio.FIRST_COMPLETED)
                if stopped in done:
                    return
                try:
                    update = await pending
                except (TransportError, asyncio.TimeoutError) as exc:
                    if (isinstance(exc, TransportError)
                            and exc.status not in (429, 502, 503, 504)):
                        raise
                    backoff = min(4, backoff + 1)
                    logging.getLogger(__name__).warning(
                        "ssh-directory-wait-retry code=service-unavailable")
                    try:
                        await asyncio.wait_for(stop.wait(), backoff)
                    except asyncio.TimeoutError:
                        pass
                    continue
                backoff = 0
                if update is not None:
                    accept(update)
                    after = update.revision
            finally:
                pending.cancel()
                stopped.cancel()
                await asyncio.gather(pending, stopped, return_exceptions=True)
