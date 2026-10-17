"""Opt-in receiver authority for an owner-registered private runtime request.

Registration is an in-process owner action, never a wire operation. Adopters
must supply native peer birth evidence independently of a client's frames.
Execute controls on the application thread; callbacks are short synchronous
mutations, not drain waits, async task scheduling or routing publication.
"""

from __future__ import annotations

import inspect
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol

from . import diagnostics, routing
from .private_protocol import (
    Channel, Deadline, PeerIdentity, PrivateIPCError, Refusal,
)
from .routing import Endpoint


class Operation(str, Enum):
    """Explicit, independently attenuated request capabilities."""

    ATTEST = "attest"
    ATTEST_PREPARED = "attest_prepared"
    CAPTURE = "capture"
    DRAIN = "drain"
    UNDRAIN = "undrain"
    ACTIVATE_RESOURCES = "activate_resources"
    ACTIVATE_SERVING = "activate_serving"
    RESERVE_SINGLETON = "reserve_singleton"
    ACTIVATE_SINGLETON = "activate_singleton"
    ADOPT = "adopt"
    RETIRE = "retire"


class GuardedChannel(Channel, Protocol):
    """A native channel that can recheck its original peer process reference."""

    def check_peer(self, deadline: Deadline) -> None: ...


@dataclass(frozen=True)
class _Grant:
    peer: PeerIdentity
    operation: Operation
    receiver: Endpoint
    active: Endpoint | None
    config_identity: str
    deadline: Deadline = field(repr=False)


class PrivateReceiver:
    """Bounded, single-use capabilities, checked at the receiver's mutation.

    The owning application thread also owns registration and revocation; IPC
    workers may frame requests but must marshal execution to that thread.
    No listener, automatic registration, bootstrap or lifecycle activation is
    provided here. A timeout consumes its grant even if the response is lost.
    """

    def __init__(
        self, root: Path, *, config_identity: Callable[[], str],
        endpoint: Callable[[], Endpoint],
        callbacks: dict[Operation, Callable[[], dict[str, object]]],
        capacity: int = 64,
    ) -> None:
        if type(capacity) is not int or capacity <= 0 or capacity > 64:
            raise ValueError("private receiver capacity must be between 1 and 64")
        if any(not isinstance(op, Operation) or not callable(fn)
               or inspect.iscoroutinefunction(fn)
               or inspect.iscoroutinefunction(getattr(fn, "__call__", None))
               for op, fn in callbacks.items()):
            raise ValueError("private receiver callbacks require explicit operations")
        self._root = Path(root)
        self._identity = config_identity
        self._endpoint = endpoint
        self._callbacks = dict(callbacks)
        self._capacity = capacity
        self._grants: dict[str, _Grant] = {}
        self._executing_deadline: Deadline | None = None
        self._thread = threading.current_thread()
        self._pid = os.getpid()
        self._start = diagnostics.process_start_time(self._pid)
        if not self._start:
            raise PrivateIPCError(Refusal.IDENTITY)

    def _owner_thread(self) -> None:
        if threading.current_thread() is not self._thread or os.getpid() != self._pid:
            raise PrivateIPCError(Refusal.AUTHORITY)

    def _reap(self) -> None:
        for key, grant in tuple(self._grants.items()):
            try:
                grant.deadline.remaining()
            except PrivateIPCError:
                del self._grants[key]

    def register(
        self, peer: PeerIdentity, operation: Operation, *, receiver: Endpoint,
        active: Endpoint | None, config_identity: str, deadline: Deadline,
    ) -> str:
        """Register an independently verified native peer for exactly one call.

        ``receiver`` is this process's endpoint. ``active`` is the exact route
        required at execution, including generation, PID/start and version.
        Only route-lock-free attestations may omit that active-route condition.
        """
        self._owner_thread()
        deadline.remaining()
        if (
            not isinstance(peer, PeerIdentity)
            or not isinstance(operation, Operation) or operation not in self._callbacks
            or not isinstance(receiver, Endpoint)
            or receiver.pid != self._pid or receiver.process_start_time != self._start
            or not isinstance(config_identity, str) or not config_identity
            or (
                operation not in (Operation.ATTEST, Operation.ATTEST_PREPARED)
                and not self._valid_publication(active)
            )
            or (active is not None and not self._valid_publication(active))
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._reap()
        if len(self._grants) >= self._capacity:
            raise PrivateIPCError(Refusal.CAPACITY)
        token = secrets.token_hex(32)
        self._grants[token] = _Grant(
            peer, operation, receiver, active, config_identity, deadline,
        )
        return token

    @staticmethod
    def _valid_publication(endpoint: Endpoint | None) -> bool:
        return (
            isinstance(endpoint, Endpoint)
            and type(endpoint.pid) is int and endpoint.pid > 0
            and isinstance(endpoint.process_start_time, str)
            and endpoint.process_start_time.isdigit()
            and type(endpoint.generation) is int and endpoint.generation > 0
            and type(endpoint.port) is int and 0 < endpoint.port <= 65535
            and isinstance(endpoint.bind, str) and bool(endpoint.bind)
            and isinstance(endpoint.version, str) and bool(endpoint.version)
        )

    def revoke(self, token: str) -> None:
        """Revoke a local registration; this does not signal or close its peer."""
        self._owner_thread()
        self._grants.pop(token, None)

    @staticmethod
    def _matches_publication(raw: object, expected: dict[str, object]) -> bool:
        return (
            type(raw) is dict and set(raw) == set(expected)
            and all(type(raw[key]) is type(value) and raw[key] == value
                    for key, value in expected.items())
        )

    def _check(self, grant: _Grant, channel: GuardedChannel) -> None:
        grant.deadline.remaining()
        channel.check_peer(grant.deadline)
        actual = self._endpoint()
        # Publication generation is checked against the native routing owner,
        # not an app's startup value (passives publish later; rollback advances
        # the same process's generation). The bound listener remains app-owned.
        fields = ("pid", "process_start_time", "bind", "port", "version")
        if (
            channel.peer != grant.peer
            or diagnostics.process_start_time(self._pid) != self._start
            or not isinstance(actual, Endpoint)
            or any(getattr(actual, key) != getattr(grant.receiver, key) for key in fields)
            or self._identity() != grant.config_identity
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)

    def execution_deadline(self) -> Deadline:
        """Return the original deadline only during an owner-thread callback."""
        self._owner_thread()
        if self._executing_deadline is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._executing_deadline.remaining()
        return self._executing_deadline

    def _call(self, grant: _Grant) -> dict[str, object]:
        if self._executing_deadline is not None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._executing_deadline = grant.deadline
        try:
            return self._call_result(grant)
        finally:
            self._executing_deadline = None

    def _call_result(self, grant: _Grant) -> dict[str, object]:
        result = self._callbacks[grant.operation]()
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise PrivateIPCError(Refusal.AUTHORITY)
        if type(result) is not dict:
            raise PrivateIPCError(Refusal.PROTOCOL)
        attestation_flag = {
            Operation.ATTEST: "ready", Operation.ATTEST_PREPARED: "prepared",
        }.get(grant.operation)
        if attestation_flag is not None and (
            set(result) != {
                "pid", "process_start_time", "version", "config_identity", attestation_flag,
            }
            or type(result["pid"]) is not int or result["pid"] != self._pid
            or result["process_start_time"] != self._start
            or result["version"] != grant.receiver.version
            or result["config_identity"] != grant.config_identity
            or type(result[attestation_flag]) is not bool
        ):
            raise PrivateIPCError(Refusal.PROTOCOL)
        grant.deadline.remaining()
        return result

    def execute(
        self, channel: GuardedChannel, request: dict[str, object],
    ) -> dict[str, object]:
        """Consume a grant and perform its checked synchronous application action.

        The wire supplies only an opaque grant and operation, not peer evidence,
        endpoint authority, configuration or a new timeout. Attestations
        deliberately do not acquire routing authority: final verification may
        already hold it. Capture and controls check strict publication state
        and native identity under the same fence as the callback.
        """
        self._owner_thread()
        if (
            type(request) is not dict or set(request) != {"grant", "operation"}
            or not isinstance(request["grant"], str)
            or not isinstance(request["operation"], str)
        ):
            raise PrivateIPCError(Refusal.PROTOCOL)
        grant = self._grants.pop(request["grant"], None)
        if grant is None or request["operation"] != grant.operation.value:
            raise PrivateIPCError(Refusal.AUTHORITY)
        try:
            self._check(grant, channel)
            if grant.operation in (Operation.ATTEST, Operation.ATTEST_PREPARED):
                return self._call(grant)
            lock_deadline = time.monotonic() + grant.deadline.remaining()
            with routing.routing_lock(self._root, deadline=lock_deadline):
                self._check(grant, channel)
                table = routing.read_table(self._root, strict=True)
                if (
                    type(table) is not dict
                    or not self._matches_publication(table.get("active"), grant.active.to_dict())
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
                receiver = grant.receiver.to_dict()
                active_receiver = self._matches_publication(table.get("active"), receiver)
                previous_receiver = self._matches_publication(table.get("previous"), receiver)
                if not active_receiver and not previous_receiver:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                if grant.operation in (
                    Operation.CAPTURE, Operation.ADOPT, Operation.UNDRAIN,
                    Operation.ACTIVATE_RESOURCES,
                    Operation.ACTIVATE_SERVING,
                    Operation.RESERVE_SINGLETON,
                    Operation.ACTIVATE_SINGLETON,
                ) \
                        and not active_receiver:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                if grant.operation in (Operation.DRAIN, Operation.RETIRE):
                    if (
                        active_receiver
                        or diagnostics.process_start_time(grant.active.pid)
                        != grant.active.process_start_time
                    ):
                        raise PrivateIPCError(Refusal.AUTHORITY)
                return self._call(grant)
        except PrivateIPCError:
            raise
        except routing.RoutingLockTimeout:
            raise PrivateIPCError(Refusal.DEADLINE) from None
        except Exception:
            # Config/callback/storage failures may contain credentials. They
            # are failures, not fallback permission or successful responses.
            raise PrivateIPCError(Refusal.AUTHORITY) from None
