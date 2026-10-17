"""Opt-in synchronous app controls; no listener or automatic activation."""

from __future__ import annotations

import asyncio
import os
import socket

from fastapi import FastAPI
from zdd import diagnostics
from zdd.private_protocol import Deadline, PeerIdentity, PrivateIPCError, Refusal
from zdd.private_receiver import GuardedChannel, Operation, PrivateReceiver
from zdd.routing import Endpoint

from . import __version__
from .effective_runtime import RuntimeOrigins, ServingRuntimeCapture
from .install_paths import effective_config_dir, install_dir
from .runtime_transfer import PendingRuntime


class RuntimeControls:
    """Bind owner-registered capabilities to actual app, listener and state.

    Construct and execute on the application event loop. Native peer evidence
    and exact routing endpoints come from the owning coordinator, never frames.
    Callbacks do not await, spawn tasks, publish routes, attach hosts or stop
    sessions. A passive candidate attests ready=False and preparation separately.
    Writable acquisition remains passive. Foreground activation requires an
    independently prepared graph. Singleton reservation/admission is separately
    granted after exact predecessor exit. Full host/maintenance ADOPT refuses.
    """

    def __init__(
        self, app: FastAPI, listener: socket.socket, origins: RuntimeOrigins, *,
        pending: PendingRuntime | None = None,
        include_roster: bool = False,
        predecessor: Endpoint | None = None,
    ) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            raise PrivateIPCError(Refusal.AUTHORITY) from None
        if (
            not isinstance(app, FastAPI) or type(listener) is not socket.socket
            or type(origins) is not RuntimeOrigins
            or (pending is not None and type(pending) is not PendingRuntime)
            or type(include_roster) is not bool
            or (predecessor is not None and (
                type(predecessor) is not Endpoint or pending is None
            ))
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._app, self._listener, self._origins = app, listener, origins
        self._pending = pending
        self._predecessor = predecessor
        self._server = getattr(app.state, "uvicorn_server", None)
        self._pid = os.getpid()
        self._start = diagnostics.process_start_time(self._pid)
        if not self._start:
            raise PrivateIPCError(Refusal.IDENTITY)
        self._capture = None if pending is not None else ServingRuntimeCapture(
            app, origins, include_resolver=True, include_relay=True, include_roster=include_roster,
        )
        callbacks = {Operation.ATTEST: self._attest}
        if pending is not None:
            callbacks[Operation.ATTEST_PREPARED] = self._attest_prepared
            callbacks[Operation.ACTIVATE_RESOURCES] = self._activate_resources
            if pending.runtime.roster is not None:
                callbacks[Operation.ACTIVATE_SERVING] = self._activate_serving
            if predecessor is not None and pending.runtime.relay is not None:
                callbacks[Operation.RESERVE_SINGLETON] = self._reserve_relay
                callbacks[Operation.ACTIVATE_SINGLETON] = self._activate_relay
        else:
            callbacks.update({
                Operation.CAPTURE: self._capture_state,
                Operation.DRAIN: self._drain,
                Operation.UNDRAIN: self._undrain,
                Operation.RETIRE: self._retire,
            })
        self._receiver = PrivateReceiver(
            origins.config_root, config_identity=self.identity,
            endpoint=self.endpoint, callbacks=callbacks,
        )
        self.identity()
        self.endpoint()

    def _check(self) -> None:
        try:
            state = self._app.state
            if (
                asyncio.get_running_loop() is not self._loop
                or os.getpid() != self._pid
                or diagnostics.process_start_time(self._pid) != self._start
                or state.runtime_origins is not self._origins
                or effective_config_dir().resolve() != self._origins.config_root
                or install_dir().resolve() != self._origins.install_root
                or state.session_manager._db is not state.db
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            if self._pending is not None:
                if not self._pending.prepared:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                self._pending.verify_resources(self._app)
                if self._pending.serving:
                    self._check_server()
            elif (
                state.ready is not True or state.db.read_only
                or state.uvicorn_server is not self._server
                or self._server is None or self._server.started is not True
                or self._server.should_exit is not False
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
        except (AttributeError, OSError, RuntimeError, ValueError, TypeError):
            raise PrivateIPCError(Refusal.AUTHORITY) from None

    def identity(self) -> str:
        """Return an opaque binding only while actual effective state matches."""
        self._check()
        if self._pending is not None:
            return self._pending.runtime.identity
        if self._capture is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        return self._capture.capture().identity

    def _check_server(self) -> None:
        if (
            self._server is None
            or self._app.state.uvicorn_server is not self._server
            or self._server.started is not True or self._server.should_exit is not False
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)

    def endpoint(self) -> Endpoint:
        """Use the actual retained listening socket, not a claimed port."""
        self._check()
        try:
            if (
                self._listener.family not in (socket.AF_INET, socket.AF_INET6)
                or self._listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 1
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            address = self._listener.getsockname()
            return Endpoint(
                address[0], address[1], self._pid, __version__, 0, self._start,
            )
        except (OSError, IndexError, TypeError):
            raise PrivateIPCError(Refusal.AUTHORITY) from None

    def register(
        self, peer: PeerIdentity, operation: Operation, *, receiver: Endpoint,
        active: Endpoint | None, deadline: Deadline,
    ) -> str:
        """Register one native peer and exact publication on the owner loop."""
        deadline.remaining()
        return self._receiver.register(
            peer, operation, receiver=receiver, active=active,
            config_identity=self.identity(), deadline=deadline,
        )

    def execute(
        self, channel: GuardedChannel, request: dict[str, object],
    ) -> dict[str, object]:
        """Consume and execute under the shared receiver's native routing fence."""
        return self._receiver.execute(channel, request)

    def revoke(self, token: str) -> None:
        self._receiver.revoke(token)

    def _attest(self) -> dict[str, object]:
        endpoint = self.endpoint()
        return {
            "pid": endpoint.pid, "process_start_time": endpoint.process_start_time,
            "version": endpoint.version, "config_identity": self.identity(),
            "ready": (self._pending is None or self._pending.serving)
            and not self._app.state.session_manager.is_draining,
        }

    def _capture_state(self) -> dict[str, object]:
        if self._capture is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        return self._capture.capture().private_payload()

    def _attest_prepared(self) -> dict[str, object]:
        endpoint = self.endpoint()
        return {
            "pid": endpoint.pid, "process_start_time": endpoint.process_start_time,
            "version": endpoint.version, "config_identity": self.identity(),
            "prepared": True,
        }

    def _drain(self) -> dict[str, object]:
        self._app.state.session_manager.set_private_draining(True)
        return {"draining": True}

    def _activate_resources(self) -> dict[str, object]:
        if self._pending is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._pending.activate_resources(self._app, self._receiver.execution_deadline())
        return {"resources_active": True, "ready": False}

    def _activate_serving(self) -> dict[str, object]:
        if self._pending is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._check_server()
        self._pending.activate_serving(self._app, self._receiver.execution_deadline())
        return {"serving": True, "ready": True, "adopted": False}

    def _reserve_relay(self) -> dict[str, object]:
        if self._pending is None or self._predecessor is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._pending.reserve_relay(
            self._app, self._predecessor, self._receiver.execution_deadline(),
        )
        return {"relay_reserved": True, "relay_ready": False, "adopted": False}

    def _activate_relay(self) -> dict[str, object]:
        if self._pending is None or self._predecessor is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._pending.activate_relay(
            self._app, self._predecessor, self._receiver.execution_deadline(),
        )
        return {"relay_ready": True, "adopted": False}

    def _undrain(self) -> dict[str, object]:
        self._app.state.session_manager.set_private_draining(False)
        return {"draining": False}

    def _retire(self) -> dict[str, object]:
        if (
            not self._app.state.session_manager.is_draining
            or not self._app.state.session_manager._drain_private
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._server.should_exit = True
        return {"shutting_down": True}
