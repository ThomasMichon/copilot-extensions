"""Native completion custody inside an existing finite ServiceCaller request."""

import asyncio
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
import threading

_admission = ContextVar("service_caller_native_admission", default=None)
_request = ContextVar("service_caller_native_request", default=None)
_credential = ContextVar("service_caller_source_credential", default=None)


@contextmanager
def source_credential(caller, reader):
    token = _credential.set((caller, reader))
    try:
        yield
    finally:
        _credential.reset(token)


def source_access(caller, ordinary):
    binding = _credential.get()
    return binding[1] if binding is not None and binding[0] is caller else ordinary


@contextmanager
def native_admission(submit):
    token = _admission.set(submit)
    try:
        yield
    finally:
        _admission.reset(token)


class RequestNativeScope:
    def __init__(self):
        self.submit = _admission.get()
        self._futures = set()
        self._lock = threading.RLock()
        self._closed = False
        self.token = None

    def enter(self):
        self.token = _request.set(self)

    def register(self, future):
        if not isinstance(future, Future):
            raise TypeError("actual native completion future required")
        with self._lock:
            self._futures.add(future)
            if self._closed:
                future.cancel()
                raise RuntimeError("native request admission closed")
        return future

    async def join(self):
        with self._lock:
            self._closed = True
        cancellation = None
        observed = set()
        while True:
            with self._lock:
                futures = tuple(self._futures - observed)
                if not futures:
                    break
            joining = asyncio.gather(*(asyncio.wrap_future(future) for future in futures), return_exceptions=True)
            while not joining.done():
                try:
                    await asyncio.shield(joining)
                except asyncio.CancelledError as error:
                    cancellation = error
            joining.result()
            observed.update(futures)
        if observed:
            from agent_procutil.owned_process import OwnedCleanupError
            errors = [future.exception() for future in observed if not future.cancelled()]
            cleanup = {id(error): error for error in errors if isinstance(error, OwnedCleanupError)}
            if cleanup:
                primary, *others = cleanup.values()
                retained = list(getattr(primary, "cleanup_resources", ()))
                for error in others:
                    if not any(
                        type(resource) is tuple and len(resource) == 2
                        and resource[0] == "request-native-cleanup" and resource[1] is error
                        for resource in retained
                    ):
                        retained.append(("request-native-cleanup", error))
                primary.cleanup_resources = tuple(retained)
                raise primary
            denied = next((error for error in errors if isinstance(error, PermissionError)), None)
            if denied is not None:
                raise denied
        if cancellation is not None:
            raise cancellation

    def exit(self):
        _request.reset(self.token)


def register_native_future(future):
    """The actual producer registers its handle before awaiting a loop wrapper."""
    scope = _request.get()
    if scope is None:
        raise RuntimeError("native service request scope required")
    return scope.register(future)


async def run_native(function, *args):
    scope = _request.get()
    if scope is None or scope.submit is None:
        # Preserve the existing non-source credential-reference path.
        return await asyncio.to_thread(function, *args)
    with scope._lock:
        if scope._closed:
            raise RuntimeError("native request admission closed")
        future = scope.register(scope.submit(function, *args))
    return await asyncio.wrap_future(future)


def source_resolver():
    scope = _request.get()
    if scope is None or scope.submit is None:
        return None
    import socket
    from aiohttp.abc import AbstractResolver

    class Resolver(AbstractResolver):
        async def resolve(self, host, port=0, family=socket.AF_INET):
            addresses = await run_native(
                socket.getaddrinfo, host, port, family, socket.SOCK_STREAM, 0, socket.AI_ADDRCONFIG,
            )
            result = []
            for resolved_family, _, protocol, _, address in addresses:
                resolved_host, resolved_port = address[:2]
                if resolved_family == socket.AF_INET6 and len(address) == 4 and address[3]:
                    resolved_host, numeric_port = await run_native(
                        socket.getnameinfo, address, socket.NI_NUMERICHOST | socket.NI_NUMERICSERV,
                    )
                    resolved_port = int(numeric_port)
                result.append({"hostname": host, "host": resolved_host, "port": resolved_port,
                               "family": resolved_family, "proto": protocol,
                               "flags": socket.AI_NUMERICHOST | socket.AI_NUMERICSERV})
            return result

        async def close(self):
            pass

    return Resolver()
