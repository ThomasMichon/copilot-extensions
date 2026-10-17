"""Candidate-owned exact predecessor exit evidence; not adoption authority."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Protocol

from zdd import routing
from zdd.private_peer import PeerProcess
from zdd.private_protocol import Deadline, PeerIdentity, PrivateIPCError, Refusal
from zdd.routing import Endpoint


class RetainedChannel(Protocol):
    peer: PeerIdentity

    def check_peer(self, deadline: Deadline) -> None: ...

    def retain_peer(self, deadline: Deadline) -> PeerProcess: ...


def _matches_publication(raw: object, expected: Endpoint) -> bool:
    fields = expected.to_dict()
    return (
        type(raw) is dict and set(raw) == set(fields)
        and all(type(raw[key]) is type(value) and raw[key] == value for key, value in fields.items())
    )


def _native_channel_type() -> type:
    if os.name == "nt":
        from zdd.private_windows import WindowsChannel

        return WindowsChannel
    if os.name == "posix":
        from zdd.private_unix import UnixChannel

        return UnixChannel
    raise PrivateIPCError(Refusal.UNAVAILABLE)


class PredecessorBinding:
    """Retain one authenticated peer independently of its preparation channel."""

    def __init__(
        self, reference: PeerProcess, endpoint: Endpoint, identity: str,
        candidate: PeerIdentity,
    ) -> None:
        self._reference = reference
        self._endpoint = endpoint
        self._identity = identity
        self._candidate = candidate
        self._thread = threading.current_thread()
        self._closed = False

    @classmethod
    def acquire(
        cls, channel: RetainedChannel, parent: PeerIdentity, candidate: PeerIdentity, *,
        endpoint: Endpoint, identity: str, root: Path, deadline: Deadline,
    ) -> PredecessorBinding:
        """Bind an independently supplied active publication before passive ack.

        Only duplicate the native channel's original live reference. The route
        lock protects the full publication comparison and acquisition together;
        no listener probe, PID reopen, signal or process wait occurs here.
        """
        deadline.remaining()
        if (
            type(channel) is not _native_channel_type()
            or type(parent) is not PeerIdentity or type(candidate) is not PeerIdentity
            or candidate.pid != os.getpid() or candidate.pid == parent.pid
            or candidate.principal != parent.principal
            or type(endpoint) is not Endpoint
            or type(endpoint.pid) is not int or endpoint.pid != parent.pid
            or type(endpoint.process_start_time) is not str
            or endpoint.process_start_time != parent.start_time
            or not endpoint.process_start_time.isdigit()
            or type(endpoint.generation) is not int or endpoint.generation <= 0
            or type(endpoint.port) is not int or not 0 < endpoint.port <= 65535
            or type(endpoint.bind) is not str or not endpoint.bind
            or type(endpoint.version) is not str or not endpoint.version
            or type(identity) is not str or not identity
        ):
            raise PrivateIPCError(Refusal.IDENTITY)
        reference = None
        try:
            with routing.routing_lock(root, deadline=time.monotonic() + deadline.remaining()):
                channel.check_peer(deadline)
                table = routing.read_table(root, strict=True)
                if (
                    channel.peer != parent or type(table) is not dict
                    or not _matches_publication(table.get("active"), endpoint)
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
                reference = channel.retain_peer(deadline)
                if type(reference) is not PeerProcess or reference.peer != parent:
                    raise PrivateIPCError(Refusal.IDENTITY)
                channel.check_peer(deadline)
                if channel.peer != parent:
                    raise PrivateIPCError(Refusal.IDENTITY)
                deadline.remaining()
            deadline.remaining()
            return cls(reference, endpoint, identity, candidate)
        except BaseException as error:
            if reference is not None:
                reference.close()
            if isinstance(error, routing.RoutingLockTimeout):
                raise PrivateIPCError(Refusal.DEADLINE) from None
            if isinstance(error, (OSError, ValueError)):
                raise PrivateIPCError(Refusal.AUTHORITY) from None
            raise

    def _owner(self) -> None:
        if os.getpid() != self._candidate.pid or threading.current_thread() is not self._thread:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if self._closed:
            raise PrivateIPCError(Refusal.CLOSED)

    def require_exit(
        self, endpoint: Endpoint, identity: str, deadline: Deadline,
    ) -> None:
        """Require exact retained exit under a later receiver's original budget."""
        self._owner()
        deadline.remaining()
        if (
            type(endpoint) is not Endpoint
            or not _matches_publication(endpoint.to_dict(), self._endpoint)
            or type(identity) is not str or identity != self._identity
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        if not self._reference.exited(deadline):
            raise PrivateIPCError(Refusal.AUTHORITY)

    def verify_preparation(
        self, channel: RetainedChannel, identity: str, root: Path, deadline: Deadline,
    ) -> None:
        """Recheck the original live binding before its one-use passive receipt."""
        self._owner()
        if (
            type(channel) is not _native_channel_type()
            or type(identity) is not str or identity != self._identity
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        try:
            with routing.routing_lock(root, deadline=time.monotonic() + deadline.remaining()):
                channel.check_peer(deadline)
                table = routing.read_table(root, strict=True)
                if (
                    channel.peer != self._reference.peer
                    or type(table) is not dict
                    or not _matches_publication(table.get("active"), self._endpoint)
                    or self._reference.exited(deadline)
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
                deadline.remaining()
        except routing.RoutingLockTimeout:
            raise PrivateIPCError(Refusal.DEADLINE) from None
        except (OSError, ValueError):
            raise PrivateIPCError(Refusal.AUTHORITY) from None

    def close(self) -> None:
        """Release exit custody only, on its owning application thread."""
        if os.getpid() != self._candidate.pid or threading.current_thread() is not self._thread:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if not self._closed:
            self._closed = True
            self._reference.close()
