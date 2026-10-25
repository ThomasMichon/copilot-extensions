"""Real Gateway caller-context handler and bounded onboarding notifications."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

from fleet_manager.gateway_service_handler import HandlerBinding, ServiceRequest, ServiceResponse
from fleet_manager.gateway_service_channel import CHANNEL_REQUESTS
from fleet_manager.planning import PlanError, canonical, decode

from .config import SSHServiceError, check
from .store import DirectoryStore


async def _drain(*tasks):
    """Settle owned work even if the caller is cancelled more than once."""
    settlement = asyncio.gather(*tasks, return_exceptions=True)
    interrupted = False
    while True:
        try:
            return await asyncio.shield(settlement), interrupted
        except asyncio.CancelledError:
            interrupted = True


class SSHService:
    def __init__(self, store: DirectoryStore, *,
                 reconcile_relay: Callable[[], None] | None = None):
        self.store = store
        self.reconcile_relay = reconcile_relay
        self.changed = asyncio.Condition()
        self.waiters = 0
        self.closed = False
        self.workers = set()
        self.closing = None
        config = store.config.to_dict()
        self.binding = HandlerBinding(config["fleet"], config["gateway"], self.handle)
        self.owner = (config["fleet"], config["gateway"], config["slot"])

    async def _disk(self, operation, *args, **kwargs):
        check(not self.closed, "service-draining")
        worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        self.workers.add(worker)
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            result, _ = await _drain(worker)
            if isinstance(result[0], Exception):
                logging.getLogger(__name__).warning(
                    "ssh-directory-worker-withdrawn code=transaction-refused")
            raise
        finally:
            self.workers.discard(worker)

    def _checkpoint(self, request: ServiceRequest) -> None:
        request.lifetime.check()
        check(not self.closed, "service-draining")
        check(request.context.expires_at > time.time(), "caller-context-expired")
        check((request.context.fleet, request.context.gateway, request.context.slot)
              == self.owner, "caller-owner-mismatch")
        self.store.config.binding(request.context.principal)

    async def _directory(self, request: ServiceRequest, query: str) -> ServiceResponse:
        arguments = parse_qs(query, strict_parsing=True)
        check(set(arguments) <= {"after", "wait"}
              and all(len(values) == 1 for values in arguments.values()), "invalid-directory-query")
        after_text = arguments.get("after", ["0"])[0]
        wait_text = arguments.get("wait", ["0"])[0]
        check(after_text.isascii() and after_text.isdecimal()
              and wait_text.isascii() and wait_text.isdecimal(), "invalid-directory-query")
        after, wait = int(after_text), int(wait_text)
        check(0 <= after < 2**53 and 0 <= wait <= 25, "invalid-directory-query")
        deadline = min(time.monotonic() + wait,
                       time.monotonic() + request.context.expires_at - time.time())
        async with self.changed:
            while True:
                self._checkpoint(request)
                result = await self._disk(self.store.snapshot)
                revision = result["directory"]["revision"]
                check(after <= revision, "directory-revision-ahead")
                if after < revision:
                    return ServiceResponse(200, (), canonical(result))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ServiceResponse(204)
                check(self.waiters < CHANNEL_REQUESTS - 2, "notification-waiter-limit")
                self.waiters += 1
                try:
                    await asyncio.wait_for(self.changed.wait(), remaining)
                except asyncio.TimeoutError:
                    self._checkpoint(request)
                    return ServiceResponse(204)
                finally:
                    self.waiters -= 1

    async def handle(self, request: ServiceRequest) -> ServiceResponse:
        try:
            self._checkpoint(request)
            target = urlsplit(request.target)
            check(not target.scheme and not target.netloc and not target.fragment, "invalid-target")
            if request.method == "GET" and target.path == "/directory":
                return await self._directory(request, target.query)
            if request.method == "POST" and target.path == "/enroll" and not target.query:
                value = decode(request.body)
                result, changed = await self._disk(
                    self.store.enroll, request.context.principal, value,
                    checkpoint=lambda: self._checkpoint(request),
                )
                if self.reconcile_relay is not None:
                    # Even an original-ID retry reconciles an already committed join.
                    try:
                        await self._disk(self.reconcile_relay)
                    except (OSError, SSHServiceError) as exc:
                        logging.getLogger(__name__).error(
                            "ssh-native-relay-reconcile-failed code=local-authorization-unavailable")
                        raise SSHServiceError("native-relay-unavailable") from exc
                if changed:
                    async with self.changed:
                        self.changed.notify_all()
                self._checkpoint(request)
                return ServiceResponse(200, (), canonical(result))
            return ServiceResponse(404)
        except SSHServiceError as exc:
            code = str(exc)
            status = 503 if code in {
                "service-draining", "notification-waiter-limit", "native-relay-unavailable",
            } else 403
            return ServiceResponse(status, (), canonical({"error": code}))
        except (PlanError, UnicodeError, ValueError, RecursionError):
            return ServiceResponse(400, (), canonical({"error": "invalid-request"}))

    async def close(self) -> None:
        self.closed = True

        async def settle():
            async with self.changed:
                self.changed.notify_all()
            await _drain(*tuple(self.workers))

        if self.closing is None:
            self.closing = asyncio.create_task(settle())
        result, interrupted = await _drain(self.closing)
        if isinstance(result[0], BaseException):
            raise result[0]
        if interrupted:
            raise asyncio.CancelledError
