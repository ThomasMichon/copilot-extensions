"""Independent exit custody duplicated from an authenticated native channel."""

from __future__ import annotations

import os
import math
import select
import sys
import threading

from .private_protocol import Deadline, PeerIdentity, PrivateIPCError, Refusal
from .private_windows_native import WAIT_TIMEOUT, Native


def _pidfd_exited(reference: int, timeout: float) -> bool:
    poll_factory = getattr(select, "poll", None)
    if not callable(poll_factory):
        raise PrivateIPCError(Refusal.UNAVAILABLE)
    poller = poll_factory()
    poller.register(reference, select.POLLIN)
    events = poller.poll(0 if timeout == 0 else max(1, math.ceil(timeout * 1000)))
    for descriptor, flags in events:
        if descriptor != reference or flags & (select.POLLERR | select.POLLNVAL):
            raise PrivateIPCError(Refusal.IDENTITY)
        if not flags & select.POLLIN:
            raise PrivateIPCError(Refusal.IDENTITY)
    return bool(events)


class PeerProcess:
    """An owned original peer reference, not signalling or adoption authority.

    Native channels create these while their authenticated peer is still alive.
    The duplicate survives channel closure and process reaping. Exit is observed
    through that same reference, never a later numeric PID lookup. Use and close
    on the receiving owner thread; waiting belongs outside a routing fence.
    """

    def __init__(
        self, reference: int, peer: PeerIdentity, *, api: Native | None = None,
    ) -> None:
        if api is None and sys.platform != "linux":
            raise PrivateIPCError(Refusal.UNAVAILABLE)
        self._reference = reference
        self._peer = peer
        self._api = api
        self._pid = os.getpid()
        self._thread = threading.current_thread()
        self._closed = False

    @property
    def peer(self) -> PeerIdentity:
        """Identity authenticated before the native reference was duplicated."""
        return self._peer

    def _check_owner(self) -> None:
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if self._closed:
            raise PrivateIPCError(Refusal.CLOSED)

    def _observe(self, timeout: float) -> bool:
        try:
            if self._api is None:
                return _pidfd_exited(self._reference, timeout)
            result = self._api.k.WaitForSingleObject(
                self._reference, 0 if timeout == 0 else max(1, int(timeout * 1000)),
            )
            if result == 0:
                return True
            if result == WAIT_TIMEOUT:
                return False
            raise PrivateIPCError(Refusal.IDENTITY)
        except (OSError, ValueError, OverflowError):
            raise PrivateIPCError(Refusal.IDENTITY) from None

    def exited(self, deadline: Deadline) -> bool:
        """Nonblocking exact-reference observation suitable for a receiver fence."""
        self._check_owner()
        deadline.remaining()
        result = self._observe(0)
        deadline.remaining()
        return result

    def wait_exit(self, deadline: Deadline) -> None:
        """Wait outside routing authority using one original bounded budget."""
        self._check_owner()
        while not self._observe(deadline.wait_slice()):
            deadline.remaining()
        deadline.remaining()

    def close(self) -> None:
        """Release only this duplicate; the channel and peer remain untouched."""
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if self._closed:
            return
        self._closed = True
        if self._api is None:
            try:
                os.close(self._reference)
            except OSError:
                raise PrivateIPCError(Refusal.IO) from None
        else:
            self._api.close(self._reference)
