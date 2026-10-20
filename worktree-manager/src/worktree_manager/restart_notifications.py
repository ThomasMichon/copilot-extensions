"""Native Linux logind transport, owned by the active daemon's I/O thread."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from uuid import uuid4

from dbus_next import Message, MessageFlag, MessageType, Variant
from dbus_next.aio import MessageBus
from dbus_next.auth import AuthExternal
from dbus_next.errors import DBusError

from .restart_evidence import RecoveryEvidenceError, RecoveryJournal
from .restart_observer import HostEpoch


log = logging.getLogger(__name__)
_SERVICE = "org.freedesktop.login1"
_PATH = "/org/freedesktop/login1"
_INTERFACE = "org.freedesktop.login1.Manager"
_DBUS = "org.freedesktop.DBus"


def _system_bus() -> MessageBus:
    # Never let inherited session-bus/address environment redirect native evidence.
    return MessageBus(
        bus_address="unix:path=/run/dbus/system_bus_socket",
        auth=AuthExternal(), negotiate_unix_fd=True,
    )


class NotificationError(RuntimeError):
    """The native notification contract could not be established or maintained."""


class LogindShutdownAdapter:
    """Own one connection and inhibitor; never spawn a worker, timer or daemon.

    All methods, callbacks and journal writes run on one owning I/O thread.
    This transport alone does not authorize automatic restoration or establish
    platform startup qualification.
    """

    def __init__(
        self, journal: RecoveryJournal, epoch: HostEpoch, *,
        bus_factory: Callable[[], MessageBus] = _system_bus,
        loop_factory: Callable[[], asyncio.AbstractEventLoop] = asyncio.new_event_loop,
    ) -> None:
        self.journal = journal
        self.epoch = epoch
        self._bus_factory = bus_factory
        self._loop_factory = loop_factory
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bus: MessageBus | None = None
        self._task: asyncio.Task | None = None
        self._owner: str | None = None
        self._fd: int | None = None
        self._received_fds: set[int] = set()
        self._handler_added = False
        self._subscribed = False
        self._notification_id: str | None = None
        self._notification_ids: set[str] = set()
        self._signal_sequence = 0
        self._delay_seconds = 0.0
        self.cleanup_error: str | None = None
        self.status: dict = {"status": "inactive", "qualified": False}

    def _state(self, status: str, **detail) -> None:
        self.status = {"status": status, "qualified": False, **detail}
        if self.cleanup_error is not None:
            self.status["cleanup_error"] = self.cleanup_error

    def _close_fd(self, fd: int) -> None:
        try:
            os.close(fd)
        except OSError as exc:
            if self.cleanup_error is None:
                self.cleanup_error = f"Native notification descriptor release failed: {exc}"
            raise

    def _release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            self._received_fds.discard(fd)
            self._close_fd(fd)

    def _release_unclaimed(self) -> None:
        while self._received_fds:
            self._close_fd(self._received_fds.pop())

    def _block(self, error: object) -> None:
        error = str(error) or type(error).__name__
        try:
            self._release()
            self._release_unclaimed()
        except OSError as exc:
            error = f"{error}; inhibitor release failed: {exc}"
        self._state("blocked", blocker=str(error))
        log.warning("Native shutdown notification blocked: %s", error)
        if self._bus is not None:
            self._bus.disconnect()

    async def _call(
        self, destination: str, path: str, interface: str, member: str,
        signature: str = "", body: list | None = None, *, expected: str = "",
    ) -> Message:
        if self._bus is None:
            raise NotificationError("Native notification bus is not connected.")
        reply = await asyncio.wait_for(self._bus.call(Message(
            destination=destination, path=path, interface=interface, member=member,
            signature=signature, body=body or [], flags=MessageFlag.NO_AUTOSTART,
        )), timeout=3.0)
        if self.status["status"] == "blocked":
            raise NotificationError(self.status["blocker"])
        if (
            reply is None or reply.message_type != MessageType.METHOD_RETURN
            or reply.signature != expected
            or reply.sender != destination
            or expected != "h" and reply.unix_fds
        ):
            if reply is not None:
                for fd in reply.unix_fds:
                    self._received_fds.discard(fd)
                    self._close_fd(fd)
            name = getattr(reply, "error_name", None) or "incompatible response"
            raise NotificationError(f"logind {member}: {name}")
        return reply

    async def _property(self, name: str, signature: str):
        reply = await self._call(
            self._owner, _PATH, "org.freedesktop.DBus.Properties", "Get",
            "ss", [_INTERFACE, name], expected="v",
        )
        if (
            len(reply.body) != 1 or not isinstance(reply.body[0], Variant)
            or reply.body[0].signature != signature
        ):
            raise NotificationError(f"Incompatible logind property {name}.")
        return reply.body[0].value

    async def _inhibit(self) -> None:
        reply = await self._call(
            self._owner, _PATH, _INTERFACE, "Inhibit",
            "ssss", ["shutdown", "Worktree Manager", "Persist restart evidence", "delay"],
            expected="h",
        )
        if (
            len(reply.body) != 1 or type(reply.body[0]) is not int or reply.body[0] != 0
            or len(reply.unix_fds) != 1
        ):
            for fd in reply.unix_fds:
                self._received_fds.discard(fd)
                self._close_fd(fd)
            raise NotificationError("logind did not return one negotiated inhibitor FD.")
        fd = reply.unix_fds[0]
        self._received_fds.add(fd)
        try:
            os.set_inheritable(fd, False)
        except OSError:
            self._received_fds.discard(fd)
            self._close_fd(fd)
            raise
        self._fd = fd

    async def _setup(self) -> None:
        try:
            self._notification_ids = {
                row["notification_id"] for row in self.journal.snapshot()
                if row["notification_id"]
                and row["disposition"] in {"shutdown-pending", "shutdown-interrupted"}
                and all(row["last_positive"][field] == getattr(self.epoch, field) for field in (
                    "platform", "environment_id", "boot_id",
                ))
            }
            self._bus = self._bus_factory()
            await asyncio.wait_for(self._bus.connect(), timeout=3.0)
            self._bus.add_message_handler(self._message)
            self._handler_added = True
            owner = await self._call(
                _DBUS, "/org/freedesktop/DBus", _DBUS,
                "GetNameOwner", "s", [_SERVICE], expected="s",
            )
            if (
                len(owner.body) != 1 or not isinstance(owner.body[0], str)
                or not owner.body[0].startswith(":")
            ):
                raise NotificationError("logind unique owner is unavailable.")
            self._owner = owner.body[0]
            delay = await self._property("InhibitDelayMaxUSec", "t")
            if type(delay) is not int or delay <= 0:
                raise NotificationError("logind delay inhibitor budget is unavailable.")
            self._delay_seconds = delay / 1_000_000
            await self._inhibit()
            self._subscribed = True
            for match in (
                f"type='signal',sender='{self._owner}',path='{_PATH}',"
                f"interface='{_INTERFACE}',member='PrepareForShutdown'",
                f"type='signal',sender='{_DBUS}',path='/org/freedesktop/DBus',"
                f"interface='{_DBUS}',member='NameOwnerChanged',arg0='{_SERVICE}'",
            ):
                await self._call(
                    _DBUS, "/org/freedesktop/DBus", _DBUS, "AddMatch", "s", [match],
                )
            current = await self._call(
                _DBUS, "/org/freedesktop/DBus", _DBUS,
                "GetNameOwner", "s", [_SERVICE], expected="s",
            )
            if current.body != [self._owner] or self.status["status"] == "blocked":
                raise NotificationError("logind owner changed during notification setup.")
            sequence = self._signal_sequence
            preparing = await self._property("PreparingForShutdown", "b")
            if self.status["status"] == "blocked":
                return
            # A signal may have reached the handler while these calls were pending.
            if self._signal_sequence == sequence:
                self._prepare(preparing)
            if self.status["status"] == "connecting":
                self._state("watching")
        except (
            OSError, ValueError, DBusError, RecoveryEvidenceError,
            NotificationError, asyncio.TimeoutError,
        ) as exc:
            self._block(exc)

    def _prepare(self, preparing: bool) -> None:
        if type(preparing) is not bool:
            self._block("Invalid logind PrepareForShutdown boolean.")
            return
        try:
            if preparing:
                if self._notification_id is not None:
                    return
                if self._fd is None:
                    raise NotificationError("Shutdown arrived without an active delay inhibitor.")
                self._notification_id = str(uuid4())
                self._notification_ids.add(self._notification_id)
                try:
                    self.journal.shutdown_pending(
                        self.epoch.platform, self.epoch.environment_id, self.epoch.boot_id,
                        notification_id=self._notification_id,
                        deadline=time.monotonic() + min(1.0, self._delay_seconds / 2),
                    )
                    self._state("shutdown-pending")
                finally:
                    self._release()
            elif self._notification_ids:
                self.journal.shutdown_cancelled(
                    self.epoch.platform, self.epoch.environment_id, self.epoch.boot_id,
                    notification_id=frozenset(self._notification_ids),
                )
                self._notification_id = None
                self._notification_ids.clear()
                if self._fd is None:
                    self._state("rearming")
                    self._schedule(self._rearm())
                else:
                    self._state("watching")
        except (OSError, ValueError, RecoveryEvidenceError, NotificationError) as exc:
            self._block(exc)

    async def _rearm(self) -> None:
        try:
            await self._inhibit()
            if self.status["status"] == "rearming":
                self._state("watching")
            else:
                self._release()
        except (OSError, ValueError, DBusError, NotificationError, asyncio.TimeoutError) as exc:
            self._block(exc)

    def _message(self, message: Message) -> None:
        # dbus-next drops FD replies to cancelled futures without closing them.
        # Retain ownership at receipt, even before a coroutine can claim its FD.
        if message.unix_fds:
            self._received_fds.update(message.unix_fds)
            if (
                message.message_type != MessageType.METHOD_RETURN
                or message.sender != self._owner or len(self._received_fds) > 4
            ):
                self._block("Unexpected file descriptors on the native notification bus.")
                return
        if not self._subscribed:
            return
        self._signal(message)

    def _signal(self, message: Message) -> None:
        if self.status["status"] in {"blocked", "closed"} or message.message_type != MessageType.SIGNAL:
            return
        if (
            message.sender == _DBUS and message.path == "/org/freedesktop/DBus"
            and message.interface == _DBUS and message.member == "NameOwnerChanged"
            and message.signature == "sss" and len(message.body) == 3
            and message.body[0] == _SERVICE and message.body[2] != self._owner
        ):
            self._block("logind owner changed; notification coverage is unconfirmed.")
        elif (
            message.sender == self._owner and message.path == _PATH
            and message.interface == _INTERFACE and message.member == "PrepareForShutdown"
        ):
            if message.signature != "b" or len(message.body) != 1 or message.unix_fds:
                for fd in message.unix_fds:
                    self._received_fds.discard(fd)
                    self._close_fd(fd)
                self._block("Malformed native shutdown notification.")
            else:
                self._signal_sequence += 1
                self._prepare(message.body[0])

    def _schedule(self, coroutine) -> None:
        self._task = self._loop.create_task(coroutine)
        self._task.add_done_callback(self._completed)

    def _completed(self, task: asyncio.Task) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            log.error(
                "Native shutdown transport task failed",
                exc_info=(type(error), error, error.__traceback__),
            )
            self._block(error)

    def start(self) -> None:
        """Begin setup on the notification owner's event loop."""
        if self._loop is not None:
            return
        if self.epoch.platform != "linux":
            self._block("logind cannot qualify Windows or WSL environment shutdown.")
            return
        self._loop = self._loop_factory()
        self._state("connecting")
        self._schedule(self._setup())

    def pump(self, *, wait_s: float = 0.0) -> None:
        """Advance only on the active lease holder's admitted I/O thread."""
        if self._loop is None or self._loop.is_closed():
            return
        if self.status["status"] != "blocked":
            self._loop.run_until_complete(asyncio.sleep(wait_s))
        if self._bus is not None and not self._bus.connected and self._owner is not None:
            if self.status["status"] != "blocked":
                self._block("Native notification bus disconnected.")
        if self.status["status"] == "blocked":
            self._cleanup()

    def close(self) -> None:
        """Release owned resources before the active daemon lease can end."""
        if self._loop is None or self._loop.is_closed():
            return
        self._state("closed")
        self._cleanup()

    def _cleanup(self) -> None:
        # Called only outside run_until_complete, never from a bus/task callback.
        try:
            self._release()
            self._release_unclaimed()
        except OSError as exc:
            self._block(f"Native notification cleanup failed: {exc}")
        if self._bus is not None:
            self._bus.disconnect()
        tasks = asyncio.all_tasks(self._loop)
        for task in tasks:
            task.cancel()
        if tasks:
            self._loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        self._loop.run_until_complete(asyncio.sleep(0))
        self._release_unclaimed()
        if self._bus is not None and self._handler_added:
            self._bus.remove_message_handler(self._message)
            self._handler_added = False
        self._loop.close()
