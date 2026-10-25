"""Opt-in TLS service profile with explicitly selected public proof authority."""

from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import HttpVersion11, WSMsgType, web

from .gateway_service_channel import (
    CONTROL_BURST, CONTROL_RATE, DEADLINE, FRAME_BYTES, GLOBAL_REQUESTS, REQUEST_HEADERS, RESPONSE_BYTES,
    BODY_BYTES,
    Channel, Pending, REQUEST_BURST, REQUEST_RATE, ServerSocket, TransportError, ValueFreeLogger,
    binary, check, headers, close_socket, encode_frame, no_store_response,
    send, send_encoded, shape, target, unpack,
)
from .gateway_service_context import ServiceContext, ServicePolicyError
from .gateway_identity_provider import IdentityProvider
from .gateway_service_listener import Listener, read_bounded
from .gateway_service_policy import ServiceAuthority, ServiceCandidate
from .gateway_manifest import RouteSpec, ServiceSlot
from .gateway_service_handler import PROFILE
from .gateway_service_handler import CallerContext, RequestLifetime, ServiceRequest, ServiceResponse
from .gateway_local_handler import LocalHandlerBinding, LocalServiceRequest
from .gateway_service_policy import CallerGrant

LOG = logging.getLogger(__name__)
STATE = web.AppKey("fleet-service-state", object)
DISPATCHED = web.RequestKey("fleet-service-dispatched", bool)


@dataclass(frozen=True)
class Ticket:
    candidate: ServiceCandidate
    deadline: float
    caller_context: bool = False


@dataclass(frozen=True)
class RoleStatus:
    name: str
    state: str
    generation: int | None


@dataclass(frozen=True)
class RuntimeStatus:
    revision: int | None
    generation: int
    roles: tuple[RoleStatus, ...]
    last_error: str | None


class ServiceRuntime:
    def __init__(self, authority: ServiceAuthority, listener: Listener,
                 reload_interval: float,
                 local_handlers: tuple[LocalHandlerBinding, ...] = ()) -> None:
        check(type(reload_interval) in (int, float) and 0.05 <= reload_interval <= 60,
              "invalid-reload-interval")
        self.authority, self.listener = authority, listener
        check(type(local_handlers) is tuple and len(local_handlers) <= 32
              and all(type(item) is LocalHandlerBinding for item in local_handlers),
              "invalid-local-handler")
        self.local_handlers = {item.slot.name: item for item in local_handlers}
        check(len(self.local_handlers) == len(local_handlers), "duplicate-local-handler")
        self.local_busy: set[str] = set()
        self.local_active: dict[asyncio.Future[ServiceResponse], RequestLifetime] = {}
        self.origin = listener.external_origin or listener.origin
        self.interval = reload_interval
        self.context: ServiceContext | None = None
        self.channels: dict[str, Channel] = {}
        self.tickets: dict[str, Ticket] = {}
        self.unverified = 0
        self.active = 0
        self.pending = 0
        self.budget, self.sampled = float(CONTROL_BURST), time.monotonic()
        self.request_budget, self.request_sampled = float(REQUEST_BURST), time.monotonic()
        self.stopping = False
        self.last_error: str | None = None

    def status(self) -> RuntimeStatus:
        """Bounded embedding diagnostics, not an unauthenticated inventory API."""
        policy = self.authority.status()
        roles = []
        for slot in self.context.manifest.slots if self.context else ():
            channel = self.channels.get(slot.name)
            ready = channel is not None and not channel.closed and not self.stopping
            if ready:
                try:
                    self.authority.revalidate_service(channel.grant)
                except ServicePolicyError:
                    ready = False
            local = self.local_handlers.get(slot.name)
            registered = local is not None and local.slot == slot and not self.stopping
            roles.append(RoleStatus(slot.name, "ready" if ready else (
                "registered-local" if registered else "unavailable"),
                                    channel.grant.generation if ready else None))
        return RuntimeStatus(
            policy.revision, policy.generation, tuple(roles),
            self.last_error or policy.last_error)

    def control_budget(self) -> None:
        now = time.monotonic()
        self.budget = min(CONTROL_BURST, self.budget + (now - self.sampled) * CONTROL_RATE)
        self.sampled = now
        check(self.budget >= 1, "control-rate-limit", 429)
        self.budget -= 1

    def admit_request(self) -> None:
        now = time.monotonic()
        self.request_budget = min(
            REQUEST_BURST, self.request_budget + max(0, now - self.request_sampled) * REQUEST_RATE)
        self.request_sampled = now
        check(self.request_budget >= 1, "request-rate-limit", 429)
        self.request_budget -= 1

    def expire_tickets(self) -> None:
        for token, ticket in list(self.tickets.items()):
            if ticket.deadline <= time.monotonic():
                del self.tickets[token]
                self.authority.release_candidate(ticket.candidate)

    async def reload(self) -> None:
        publication = await asyncio.to_thread(self.authority.reload)
        self.context = publication.snapshot.context
        self.last_error = None
        await self.sweep()

    async def sweep(self) -> None:
        self.expire_tickets()
        actions = []
        for channel in list(self.channels.values()):
            try:
                self.authority.revalidate_service(channel.grant)
            except ServicePolicyError:
                actions.append(channel.close("authorization-withdrawn"))
                continue
            for pending in list(channel.pending.values()):
                try:
                    self.checkpoint(channel, pending)
                except (ServicePolicyError, TransportError):
                    pending.fail("authorization-withdrawn", 403)
                    actions.append(channel.cancel(pending))
        await asyncio.gather(*actions)

    async def reload_loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.reload()
            except (ServicePolicyError, TransportError) as exc:
                self.last_error = exc.code
                LOG.warning("service-reload-refused code=%s", exc.code)
                # An invalid candidate preserves LKG, but expiry still takes effect.
                await self.sweep()

    def route(self, path: str, method: str) -> tuple[ServiceSlot, RouteSpec]:
        check(self.context is not None, "not-configured", 503)
        assert self.context is not None
        for slot in self.context.manifest.slots:
            for route in slot.routes:
                if path == route.prefix or path.startswith(route.prefix + "/"):
                    check(method in route.methods, "method-denied", 405)
                    return slot, route
        raise TransportError("route-unavailable", 404)

    def checkpoint(self, channel: Channel, pending: Pending) -> None:
        self.authority.revalidate_service(channel.grant)
        scope = self.authority.revalidate_caller(pending.caller)
        slot, _ = self.route(pending.path, pending.method)
        check(self.channels.get(slot.name) is channel and scope.slot == slot
              and scope.method == pending.method and scope.target_uri == pending.target_uri,
              "authorization-withdrawn", 403)

    async def challenge(self, request: web.Request) -> web.Response:
        self.control_budget()
        check(request.method == "GET", "method-denied", 405)
        return web.json_response({"nonce": self.authority.challenge()},
                                 headers={"Cache-Control": "no-store"})

    async def admit(self, request: web.Request) -> web.Response:
        self.control_budget()
        check(request.method == "POST", "method-denied", 405)
        value = unpack((await body(request, 16384)).decode("utf-8"))
        caller_context = "profile" in value
        shape(value, {"slot", "access", "proof", "profile"} if caller_context
              else {"slot", "access", "proof"})
        check(not caller_context or value["profile"] == PROFILE, "unsupported-profile")
        check(all(isinstance(v, str) for v in value.values()), "invalid-admission")
        self.expire_tickets()
        check(len(self.tickets) < 64, "ticket-limit", 429)
        candidate = self.authority.prepare_service(
            value["access"], value["proof"], target_uri=self.origin + request.path,
            slot=value["slot"])
        if value["slot"] in self.local_handlers:
            self.authority.release_candidate(candidate)
            raise TransportError("slot-locally-owned", 409)
        ticket = secrets.token_hex(32)
        self.tickets[ticket] = Ticket(candidate, time.monotonic() + 30, caller_context)
        return web.json_response({"ticket": ticket}, headers={"Cache-Control": "no-store"})

    async def attach(self, request: web.Request) -> web.WebSocketResponse:
        self.control_budget()
        check(request.method == "GET", "method-denied", 405)
        check(self.unverified < 8, "handshake-limit", 429)
        self.unverified += 1
        ws = ServerSocket(max_msg_size=FRAME_BYTES, heartbeat=10,
                          compress=False, timeout=2, autoclose=False)
        channel = None
        grant = None
        candidate = None
        try:
            await ws.prepare(request)
            message = await asyncio.wait_for(ws.receive(), 5)
            check(message.type == WSMsgType.TEXT and len(message.data) <= 256,
                  "invalid-ticket")
            value = unpack(message.data)
            shape(value, {"ticket"})
            check(isinstance(value["ticket"], str) and len(value["ticket"]) == 64,
                  "invalid-ticket")
            self.expire_tickets()
            ticket = self.tickets.pop(value["ticket"], None)
            check(ticket is not None, "invalid-ticket", 401)
            assert ticket is not None
            candidate = ticket.candidate
            self.authority.revalidate_candidate(candidate)
            await send(ws, {"type": "attached", **(
                {"profile": PROFILE} if ticket.caller_context else {})})
            grant = self.authority.promote_service(candidate)
            candidate = None
            channel = Channel(ws, self.authority, grant, caller_context=ticket.caller_context)
            previous = self.channels.get(grant.slot)
            self.channels[grant.slot] = channel
            if previous:
                await previous.close("channel-replaced")
            self.authority.revalidate_service(grant)
            check(self.channels.get(grant.slot) is channel, "channel-replaced", 403)
        except (TransportError, ServicePolicyError, OSError, asyncio.TimeoutError,
                asyncio.CancelledError) as exc:
            LOG.warning("service-channel-refused code=attachment-refused")
            if channel:
                await channel.close("attachment-refused")
                if self.channels.get(grant.slot) is channel:
                    del self.channels[grant.slot]
            elif grant:
                self.authority.release_service(grant)
            if candidate is not None:
                self.authority.release_candidate(candidate)
            await close_socket(ws, code=1008, message=b"attachment-refused")
            if isinstance(exc, asyncio.CancelledError):
                raise
            return ws
        finally:
            self.unverified -= 1
        try:
            await channel.run()
        except (TransportError, ServicePolicyError, OSError, asyncio.TimeoutError):
            LOG.warning("service-channel-closed code=channel-refused")
        finally:
            await channel.close()
            if self.channels.get(grant.slot) is channel:
                del self.channels[grant.slot]
        return ws

    async def public(self, request: web.Request) -> web.StreamResponse:
        path, query = target(request.raw_path)
        check("Upgrade" not in request.headers and request.method != "CONNECT",
              "unsupported-upgrade", 400)
        check(request.headers.getall("Content-Encoding", ["identity"]) == ["identity"],
              "unsupported-content-encoding", 400)
        slot, route = self.route(path, request.method)
        check("response-stream" not in route.capabilities or request.version >= HttpVersion11,
              "stream-http-version-required", 505)
        access, proof = credentials(request)
        uri = self.origin + path
        grant = self.authority.authenticate_caller(
            access, proof, method=request.method, request_method=request.method,
            target_uri=uri, slot=slot.name)
        channel = None
        pending = None
        response = None
        counted = False
        try:
            check(self.pending < GLOBAL_REQUESTS, "request-limit", 429)
            if slot.name in self.local_handlers:
                return await self.local(request, grant, slot, route, path, query)
            channel = self.channels.get(slot.name)
            check(channel is not None and not channel.closed, "service-unavailable", 503)
            assert channel is not None
            pending = Pending(grant, path, request.method, uri)
            self.checkpoint(channel, pending)
            channel.admit(pending)
            self.pending += 1
            counted = True
            payload = await body(request, BODY_BYTES)
            selected = headers(request.headers.items(), REQUEST_HEADERS)
            self.checkpoint(channel, pending)
            stripped = path[len(route.prefix):] or "/"
            caller = self.authority.revalidate_caller(grant)
            context = {
                "fleet": self.listener.fleet, "gateway": self.listener.gateway,
                "slot": slot.name, "principal": caller.principal,
                "expires_at": caller.expires_at,
            } if channel.caller_context else None
            envelope = encode_frame({
                "type": "request", "id": pending.id, "method": request.method,
                "target": stripped + query, "headers": selected,
                "body": base64.b64encode(payload).decode("ascii"), "timeout": DEADLINE,
                **({"context": context} if context is not None else {}),
            })
            self.checkpoint(channel, pending)
            pending.dispatched = True
            request[DISPATCHED] = True
            await send_encoded(channel.ws, envelope)
            first = await pending.queue.get()
            if isinstance(first, TransportError):
                raise first
            self.checkpoint(channel, pending)
            response = web.StreamResponse(status=first["status"], headers=first["headers"])
            streaming = "response-stream" in route.capabilities
            buffered = bytearray()
            if streaming:
                await response.prepare(request)
            while True:
                frame = await pending.queue.get()
                if isinstance(frame, TransportError):
                    raise frame
                self.checkpoint(channel, pending)
                if frame["type"] == "end":
                    if not streaming:
                        if request.method != "HEAD":
                            response.content_length = len(buffered)
                        await response.prepare(request)
                        self.checkpoint(channel, pending)
                        await response.write(bytes(buffered))
                        self.checkpoint(channel, pending)
                    await response.write_eof()
                    return response
                data = binary(frame["data"], 16384)
                if streaming:
                    await response.write(data)
                else:
                    check(len(buffered) + len(data) <= RESPONSE_BYTES, "response-limit", 502)
                    buffered.extend(data)
                self.checkpoint(channel, pending)
                pending.unacknowledged = False
                await send(channel.ws, {"type": "ack", "id": pending.id, "seq": frame["seq"]})
        except (TransportError, ServicePolicyError, OSError,
                asyncio.TimeoutError, asyncio.CancelledError):
            if response is not None and response.prepared and request.transport is not None:
                request.transport.close()
            raise
        finally:
            if counted:
                self.pending -= 1
            self.authority.release_caller(grant)
            if channel is not None and pending is not None:
                await channel.cancel(pending)

    async def local(
        self, request: web.Request, grant: CallerGrant, slot: ServiceSlot,
        route: RouteSpec, path: str, query: str,
    ) -> web.Response:
        binding = self.local_handlers[slot.name]
        check(slot.name not in self.local_busy, "local-owner-busy", 429)
        lifetime = None
        task = None

        def checkpoint() -> None:
            check(not self.stopping, "gateway-unavailable", 503)
            current = self.authority.revalidate_caller(grant)
            selected, current_route = self.route(path, request.method)
            check(current.slot == selected == binding.slot and current_route == route
                  and current.method == request.method
                  and current.target_uri == self.origin + path,
                  "local-owner-changed", 403)
            check(request.transport is not None and not request.transport.is_closing(),
                  "request-disconnected", 503)

        self.local_busy.add(slot.name)
        self.pending += 1
        try:
            checkpoint()
            payload = await body(request, BODY_BYTES)
            checkpoint()
            caller = self.authority.revalidate_caller(grant)
            local_request = ServiceRequest(
                CallerContext(self.listener.fleet, self.listener.gateway, slot.name,
                              caller.principal, caller.expires_at),
                request.method, (path[len(route.prefix):] or "/") + query,
                tuple(headers(request.headers.items(), REQUEST_HEADERS).items()), payload)
            lifetime = local_request.lifetime
            local = LocalServiceRequest(local_request, self.authority, grant, checkpoint)
            request[DISPATCHED] = True
            task = asyncio.ensure_future(binding.handle(local))
            self.local_active[task] = lifetime
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                lifetime.close()
                task.cancel()
                settled = asyncio.gather(task, return_exceptions=True)
                while not settled.done():
                    try:
                        await asyncio.shield(settled)
                    except asyncio.CancelledError:
                        continue
                settled.result()
                raise
            local.checkpoint()
            check(type(result) is ServiceResponse, "invalid-backend-response")
            return web.Response(status=result.status, headers=result.headers, body=result.body)
        except (TransportError, ServicePolicyError, OSError,
                asyncio.TimeoutError, asyncio.CancelledError):
            raise
        except Exception:
            LOG.error("local-handler-refused code=handler-failed")
            raise TransportError("handler-failed") from None
        finally:
            if lifetime is not None:
                lifetime.close()
            if task is not None:
                self.local_active.pop(task, None)
            self.pending -= 1
            self.local_busy.remove(slot.name)


async def body(request: web.Request, maximum: int) -> bytes:
    result = bytearray()
    async for chunk in request.content.iter_chunked(min(maximum + 1, 16384)):
        check(len(result) + len(chunk) <= maximum, "body-limit", 413)
        result.extend(chunk)
    return bytes(result)


def credentials(request: web.Request) -> tuple[str, str]:
    values, proofs = request.headers.getall("Authorization", []), request.headers.getall("DPoP", [])
    check(len(values) == len(proofs) == 1, "authentication-required", 401)
    scheme = "DP" + "oP "
    check(values[0].startswith(scheme) and 0 < len(values[0]) - len(scheme) <= 8192
          and 0 < len(proofs[0]) <= 4096, "authentication-required", 401)
    return values[0][len(scheme):], proofs[0]


@web.middleware
async def guarded(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    state = request.app[STATE]
    if state.stopping:
        return web.json_response({"error": "gateway-unavailable"}, status=503)
    if state.active >= 128:
        return web.json_response({"error": "request-limit"}, status=429)
    state.active += 1
    try:
        state.admit_request()
        check(request.secure, "tls-required", 400)
        state.listener.check_host(request.headers.getall("Host", []))
        target(request.raw_path)
        check(not request.path.startswith("/_fleet/") or not request.query_string,
              "invalid-control-query")
        check(sum(len(k) + len(v) for k, v in request.headers.items()) <= 16384,
              "header-limit", 413)
        if request.path == "/_fleet/service-channel":
            return await handler(request)
        return await asyncio.wait_for(handler(request), DEADLINE)
    except TransportError as exc:
        LOG.warning("service-request-refused code=%s", exc.code)
        return web.json_response({"error": outcome(request, exc.code)}, status=exc.status)
    except ServicePolicyError as exc:
        LOG.warning("service-request-refused code=%s", exc.code)
        status = 403 if exc.code in (
            "caller-denied", "service-denied", "revoked-grant", "invalid-grant",
            "slot-unavailable", "method-denied",
        ) else 401
        return web.json_response(
            {"error": outcome(request, "authorization-denied")}, status=status)
    except asyncio.TimeoutError:
        LOG.warning("service-request-refused code=request-timeout")
        return web.json_response({"error": outcome(request, "request-timeout")}, status=504)
    except (UnicodeError, web.HTTPBadRequest):
        return web.json_response({"error": "invalid-request"}, status=400)
    except OSError:
        LOG.warning("service-request-refused code=channel-disconnected")
        return web.json_response({"error": outcome(request, "channel-disconnected")}, status=502)
    finally:
        state.active -= 1


def outcome(request: web.Request, code: str) -> str:
    return "outcome-unknown" if request.get(DISPATCHED) and request.method not in (
        "GET", "HEAD", "OPTIONS") else code


async def build_app(listener: Listener, policy_path: Path, *, reload_interval: float = 1,
                    clock: Callable[[], int] = lambda: int(time.time()),
                    identity_provider: IdentityProvider | None = None,
                    local_handlers: tuple[LocalHandlerBinding, ...] = ()) -> web.Application:
    """Build without binding; the policy file is the sole explicit runtime source."""
    listener.tls()
    authority = ServiceAuthority(
        fleet=listener.fleet, gateway=listener.gateway,
        source=lambda: read_bounded(policy_path), clock=clock,
        identity_provider=identity_provider)
    state = ServiceRuntime(authority, listener, reload_interval, local_handlers)
    await state.reload()
    app = web.Application(middlewares=[guarded], client_max_size=BODY_BYTES, handler_args={
        "logger": ValueFreeLogger(LOG, {}), "max_line_size": 8190,
        "max_field_size": 12288, "auto_decompress": False,
    })
    app[STATE] = state
    app.on_response_prepare.append(no_store_response)
    app.router.add_route("*", "/_fleet/challenge", state.challenge)
    app.router.add_route("*", "/_fleet/service-admit", state.admit)
    app.router.add_route("*", "/_fleet/service-channel", state.attach)
    app.router.add_route("*", "/{tail:.*}", state.public)

    async def lifetime(application: web.Application) -> AsyncIterator[None]:
        task = asyncio.create_task(state.reload_loop())

        def failed(finished: asyncio.Task[None]) -> None:
            if not finished.cancelled() and finished.exception() is not None:
                state.stopping = True
                state.last_error = "reload-owner-failed"
                LOG.error("service-reload-stopped code=reload-owner-failed")

        task.add_done_callback(failed)
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(application: web.Application) -> None:
        state.stopping = True
        local = tuple(state.local_active.items())
        for task, lifetime in local:
            lifetime.close()
            task.cancel()
        try:
            await asyncio.gather(*(channel.close("gateway-shutdown")
                                   for channel in list(state.channels.values())))
        finally:
            await asyncio.gather(*(task for task, _ in local), return_exceptions=True)
        for ticket in state.tickets.values():
            authority.release_candidate(ticket.candidate)
        state.tickets.clear()

    app.cleanup_ctx.append(lifetime)
    app.on_shutdown.append(shutdown)
    return app


@dataclass
class Serving:
    runner: web.AppRunner
    app: web.Application
    origin: str

    async def close(self) -> None:
        await self.runner.cleanup()


async def start(listener: Listener, policy_path: Path, **kwargs: Any) -> Serving:
    """Start a real TLS listener; port zero resolves one immutable test origin."""
    context = listener.tls()
    app = await build_app(listener, policy_path, **kwargs)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=5,
                           handler_cancellation=True)
    try:
        await runner.setup()
        site = web.TCPSite(runner, listener.host, listener.port, ssl_context=context)
        await site.start()
        addresses = runner.addresses
        check(len(addresses) == 1 or listener.port != 0, "ambiguous-test-listener")
        actual = listener.bound_origin(addresses[0][1])
        app[STATE].origin = listener.external_origin or actual
        return Serving(runner, app, actual)
    except BaseException:
        await runner.cleanup()
        raise


def serve(policy_path: Path, tls_path: Path) -> int:
    """Foreground runner; the external service manager owns process lifetime."""
    try:
        listener = Listener.load(tls_path)
        check(listener.port != 0, "ephemeral-listener-sdk-only")
        context = listener.tls()
        web.run_app(build_app(listener, policy_path), host=listener.host,
                    port=listener.port, ssl_context=context, access_log=None,
                    print=None, shutdown_timeout=5, handler_cancellation=True)
        return 0
    except (TransportError, ServicePolicyError, OSError):
        LOG.error("service-start-refused code=invalid-bootstrap-or-policy")
        return 1
