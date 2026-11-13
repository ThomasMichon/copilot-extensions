"""Finite Windows broker transaction over an already authenticated native pipe.

The optional broker transport is agent-zdd; ordinary process utilities remain
stdlib-only. Activation and installation authority never come from a frame.
"""

from __future__ import annotations

import ntpath
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from zdd.private_protocol import (
    Channel,
    Deadline,
    PrivateIPCError,
    Refusal,
    deadline_lock,
    read_frame,
    write_frame,
)

from . import DaemonSpawnError


class Child(Protocol):
    """Native custody of the actual resident, not a wrapper or numeric PID."""

    pid: int

    def offer(self) -> dict[str, object]: ...
    def resume(self) -> None: ...
    def retire(self, deadline: Deadline) -> None: ...
    def close(self) -> None: ...


_ChildT = TypeVar("_ChildT", bound=Child, covariant=True)


class Closer(Protocol):
    def close(self) -> None: ...


class BrokerPeer(Protocol[_ChildT]):
    """Activation-pinned broker custody retained independently of its pipe."""

    def adopt(self, offer: dict[str, object], request: LaunchRequest) -> _ChildT: ...
    def wait_exit(self, deadline: Deadline) -> int: ...


_pending: list[Child] = []
_pending_closes: list[Closer] = []
_pending_lock = threading.RLock()


def close_resource(resource: Closer) -> None:
    """Retain failed native closes rather than dropping the last owned handle."""
    try:
        resource.close()
    except Exception:
        with _pending_lock:
            if not any(item is resource for item in _pending_closes):
                _pending_closes.append(resource)
        raise _refuse("handle cleanup unconfirmed", cleanup_confirmed=False) from None


def retire_child(child: Child, deadline: Deadline) -> None:
    """Keep retryable native custody if termination, exit or handle close fails."""
    try:
        child.retire(deadline)
        child.close()
    except Exception:
        with _pending_lock:
            if not any(item is child for item in _pending):
                _pending.append(child)
        raise _refuse("child cleanup unconfirmed", cleanup_confirmed=False) from None


def retry_cleanup(deadline: Deadline) -> None:
    """Refuse new work until every earlier retained cleanup obligation resolves."""
    with deadline_lock(_pending_lock, deadline):
        for child in tuple(_pending):
            retire_child(child, deadline)
            _pending.remove(child)
        for resource in tuple(_pending_closes):
            deadline.remaining()
            close_resource(resource)
            _pending_closes.remove(resource)


def _refuse(
    reason: str, *, cleanup_confirmed: bool = True,
    process_created: bool | None = None, admission_sent: bool = True,
    process_identity: tuple[int, str] | None = None,
) -> DaemonSpawnError:
    return DaemonSpawnError(
        "Windows launch broker: " + reason, cleanup_confirmed=cleanup_confirmed,
        process_created=process_created, admission_sent=admission_sent,
        process_identity=process_identity,
    )


def _text(value: object, *, empty: bool = False) -> str:
    if not isinstance(value, str) or "\0" in value or (not value and not empty):
        raise _refuse("invalid request")
    return value


def _path(value: object) -> str:
    result = _text(value)
    # Reject drive-relative and root-relative paths independently of host OS.
    if not ntpath.isabs(result) or not ntpath.splitdrive(result)[0]:
        raise _refuse("invalid request")
    return result


@dataclass(frozen=True, repr=False)
class LaunchRequest:
    """Validated live-only launch data; never persist, log or repr this object."""

    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str]
    creationflags: int
    log_path: str
    stderr_log_path: str | None

    @classmethod
    def parse(cls, value: dict[str, object]) -> LaunchRequest:
        from . import (
            _CREATE_NEW_PROCESS_GROUP,
            _CREATE_NO_WINDOW,
            _DETACHED_PROCESS,
            _validated_extra_creationflags,
        )

        if set(value) != {
            "argv", "cwd", "env", "creationflags", "log_path", "stderr_log_path",
        }:
            raise _refuse("invalid request")
        argv, env, flags = value["argv"], value["env"], value["creationflags"]
        if (
            type(argv) is not list or not argv or len(argv) > 4096
            or type(env) is not dict or len(env) > 32768
            or type(flags) is not int
        ):
            raise _refuse("invalid request")
        policy_mask = _CREATE_NO_WINDOW | _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
        policy = flags & policy_mask
        if policy not in (_CREATE_NO_WINDOW, _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP):
            raise _refuse("invalid request")
        command = tuple(_text(part, empty=index > 0) for index, part in enumerate(argv))
        environment: dict[str, str] = {}
        names: set[str] = set()
        for name, item in env.items():
            name = _text(name)
            if "=" in name or name.casefold() in names:
                raise _refuse("invalid request")
            names.add(name.casefold())
            environment[name] = _text(item, empty=True)
        try:
            _validated_extra_creationflags(flags & ~policy_mask)
        except (TypeError, ValueError):
            raise _refuse("invalid request") from None
        error = value["stderr_log_path"]
        return cls(
            command, _path(value["cwd"]), environment, flags, _path(value["log_path"]),
            None if error is None else _path(error),
        )

    def frame(self) -> dict[str, object]:
        return {
            "argv": list(self.argv), "cwd": self.cwd, "env": dict(self.env),
            "creationflags": self.creationflags, "log_path": self.log_path,
            "stderr_log_path": self.stderr_log_path,
        }


class Stager(Protocol):
    def __call__(self, request: LaunchRequest, deadline: Deadline) -> Child: ...


def _expect(channel: Channel, expected: dict[str, object], deadline: Deadline) -> None:
    if read_frame(channel, deadline) != expected:
        raise _refuse("transaction refused")


def serve(channel: Channel, stage: Stager, deadline: Deadline) -> None:
    """Serve one request; retain child cleanup until the final client commit.

    The activation owner waits for this finite instance's successful exit before
    returning durable success. A lost commit or resume acknowledgement is a
    failed transaction, never permission to retry before confirmed cleanup.
    """
    child: Child | None = None
    committed = False
    try:
        retry_cleanup(deadline)
        request = LaunchRequest.parse(read_frame(channel, deadline))
        deadline.remaining()
        child = stage(request, deadline)
        write_frame(channel, {"staged": child.offer()}, deadline)
        _expect(channel, {"adopted": child.pid}, deadline)
        deadline.remaining()
        child.resume()
        write_frame(channel, {"started": child.pid}, deadline)
        _expect(channel, {"committed": child.pid}, deadline)
        # The native channel checks peer liveness after writes. Remain alive
        # until the client's final write has completed and it closes the pipe.
        try:
            channel.read_exact(1, deadline)
        except PrivateIPCError as error:
            if error.reason is not Refusal.CLOSED:
                raise
        else:
            raise _refuse("transaction refused")
        deadline.remaining()
        committed = True
    finally:
        try:
            if child is not None and not committed:
                # Cleanup has its own fixed budget; it never extends admission.
                retiring, child = child, None
                retire_child(retiring, Deadline(5))
        finally:
            try:
                if child is not None:
                    child.close()
            finally:
                channel.close()


def launch(
    channel: Channel, broker: BrokerPeer[_ChildT], request: LaunchRequest, deadline: Deadline,
    *, before_admit: Callable[[_ChildT], None] | None = None,
) -> _ChildT:
    """Acquire actual native child control before resume; join only the broker."""
    child: _ChildT | None = None
    requested = False
    process_created: bool | None = False
    process_identity = None
    admission_sent = False
    try:
        retry_cleanup(deadline)
        # A partial write may already have delivered a complete request.
        requested = True
        process_created = None
        write_frame(channel, request.frame(), deadline)
        response = read_frame(channel, deadline)
        if set(response) != {"staged"} or type(response["staged"]) is not dict:
            raise _refuse("invalid offer", cleanup_confirmed=False)
        child = broker.adopt(response["staged"], request)
        process_created = True
        from ._daemon_instance_identity import created_process_identity

        process_identity = created_process_identity(child)
        if before_admit is not None:
            before_admit(child)
        deadline.remaining()
        admission_sent = True
        write_frame(channel, {"adopted": child.pid}, deadline)
        _expect(channel, {"started": child.pid}, deadline)
        write_frame(channel, {"committed": child.pid}, deadline)
        close_resource(channel)
        if broker.wait_exit(deadline) != 0:
            raise _refuse("finite broker failed", cleanup_confirmed=False)
        return child
    except Exception:
        # Do not expose credential-bearing frames or native exception strings.
        cleanup_confirmed = not requested
        cleanup_failed = False
        try:
            close_resource(channel)
        except Exception:
            cleanup_failed = True
        if child is not None:
            try:
                retiring, child = child, None
                retire_child(retiring, Deadline(5))
                cleanup_confirmed = True
            except Exception:
                cleanup_failed = True
        # Exit 1 is emitted only after broker-side child cleanup completes;
        # zero may mean it committed but our acknowledgement was lost.
        try:
            code = broker.wait_exit(Deadline(5))
            cleanup_confirmed = cleanup_confirmed or code == 1
        except Exception:
            cleanup_failed = True
        raise _refuse(
            "transaction failed",
            cleanup_confirmed=(
                cleanup_confirmed and not cleanup_failed and not _pending and not _pending_closes
            ),
            process_created=process_created, admission_sent=admission_sent,
            process_identity=process_identity,
        ) from None
