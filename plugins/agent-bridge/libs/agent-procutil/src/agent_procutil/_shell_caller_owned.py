"""Compiler-free, pipe-bound lifetime for a synchronous shell callback."""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_procutil._windows_parent_job import ParentJobOffer


def _callback_code(callback: Callable[[], int]) -> int:
    try:
        code = callback()
        if type(code) is not int or not 0 <= code <= 255:
            raise ValueError("shell callback returned an invalid exit code")
        return code
    except BaseException:
        traceback.print_exc()
        return 1


def _completion(nonce: str, code: int) -> None:
    sys.stdout.write(f"\n__COPILOT_CALLER_EXIT_{nonce}={code}\n")
    sys.stdout.flush()
    sys.stderr.flush()


def _acknowledge(descriptor: int, nonce: str) -> None:
    for expected in (nonce + "\n").encode("ascii"):
        if os.read(descriptor, 1) != bytes((expected,)):
            raise RuntimeError("original caller did not acknowledge lifetime custody")


def _windows_handoff(descriptor: int, nonce: str, offer: ParentJobOffer) -> None:
    try:
        message = bytearray()
        for _ in range(len(nonce) + 7):
            part = os.read(descriptor, 1)
            if not part:
                break
            message.extend(part)
            if part == b"\n":
                break
        if message == (nonce + "\n").encode("ascii"):
            offer.accept()
            return
        if message == ("abort:" + nonce + "\n").encode("ascii"):
            offer.abort()
            raise RuntimeError("original caller refused the unadopted Job offer")
        raise RuntimeError(
            "original caller did not acknowledge lifetime custody; Job adoption is unconfirmed"
        )
    finally:
        if offer.parent:
            # EOF is not proof of non-adoption. The caller could already have
            # closed/reused its handle, so release only our pinned process ref.
            offer.abandon()


def _posix_watch(descriptor: int, ready: int, group: int, closed: tuple[int, ...]) -> None:
    """Remain an original group member so leader death cannot unpin custody."""
    try:
        for endpoint in closed:
            os.close(endpoint)
        with open(os.devnull, "wb") as empty:
            os.dup2(empty.fileno(), 1)
            os.dup2(empty.fileno(), 2)
        if os.getpgrp() != group or os.getsid(0) != group or os.getpid() == group:
            raise RuntimeError("caller lifetime watcher lost its original group")
        if os.write(ready, b"1") != 1:
            raise RuntimeError("caller lifetime watcher could not acknowledge startup")
        os.close(ready)
        os.read(descriptor, 1)
    finally:
        # This member never runs application code or changes groups. Its own
        # membership keeps the original PGID occupied even after leader death.
        if os.getpgrp() == group and os.getsid(0) == group and os.getpid() != group:
            os.killpg(group, signal.SIGKILL)
        os._exit(1)


def _posix_callback(callback: Callable[[], int], descriptor: int, nonce: str) -> None:
    from agent_procutil._caller_owned import _retire

    reader, writer = os.pipe()
    completed_reader = completed_writer = -1
    ready_reader = ready_writer = -1
    try:
        completed_reader, completed_writer = os.pipe()
        os.set_blocking(completed_reader, False)
        child = os.fork()
        if child == 0:
            os.close(writer)
            os.close(completed_reader)
            code = 1
            try:
                # The callback cannot run until the original lifetime reader
                # is being watched. It never inherits a caller pipe endpoint.
                if os.read(reader, 1) != b"1":
                    raise RuntimeError("caller lifetime watcher did not start")
                os.close(reader)
                with open(os.devnull, "rb") as empty:
                    os.dup2(empty.fileno(), descriptor)
                code = _callback_code(callback)
                sys.stdout.flush()
                sys.stderr.flush()
                if os.write(completed_writer, bytes((code,))) != 1:
                    raise RuntimeError("shell callback completion could not be published")
            except BaseException:
                traceback.print_exc()
            finally:
                sys.stderr.flush()
                os._exit(code)

        os.close(reader)
        reader = -1
        os.close(completed_writer)
        completed_writer = -1

        group = os.getpid()
        if os.getpgrp() != group or os.getsid(0) != group:
            raise RuntimeError("caller lifetime leader lost its original group")
        ready_reader, ready_writer = os.pipe()
        watcher = os.fork()
        if watcher == 0:
            _posix_watch(
                descriptor, ready_writer, group,
                (ready_reader, writer, completed_reader),
            )
        os.close(ready_writer)
        ready_writer = -1
        if os.read(ready_reader, 1) != b"1":
            raise RuntimeError("caller lifetime watcher did not start")
        os.close(ready_reader)
        ready_reader = -1
        os.write(writer, b"1")
        os.close(writer)
        writer = -1
        observed, status = os.waitpid(child, 0)
        if observed != child:
            raise RuntimeError("shell callback exit identity is unconfirmed")
        code = os.waitstatus_to_exitcode(status)
        try:
            returned = os.read(completed_reader, 2)
        except BlockingIOError:
            returned = b""
        if code < 0 or returned != bytes((code,)):
            raise RuntimeError("shell callback completion is unconfirmed")
        _completion(nonce, code)
    except BaseException:
        traceback.print_exc()
    finally:
        try:
            for endpoint in (
                reader, writer, completed_reader, completed_writer, ready_reader, ready_writer,
            ):
                if endpoint != -1:
                    os.close(endpoint)
        finally:
            # This still-live session/group leader pins the exact scope. The
            # same canonical retirement kills it and every remaining member.
            _retire()


def serve(
    callback: Callable[[], int], *, owner_pid: int, owner_birth: str | None, nonce: str,
) -> int:
    """Admit one callback only after the original shell adopts its lifetime.

    stdin is a private lifetime pipe, never application input. The first stdout
    line is a custody receipt; after acknowledgement, stdout/stderr belong to
    the callback until the final correlated completion line. The shell must
    keep stdin and any transferred Job handle open until process completion,
    close both on cancellation, and reject a missing completion witness.
    """
    if threading.current_thread() is not threading.main_thread() or threading.active_count() != 1:
        raise RuntimeError("shell lifetime admission requires single-threaded startup")
    if not callable(callback) or type(owner_pid) is not int or not 0 < owner_pid <= 0xFFFFFFFF:
        raise ValueError("shell lifetime admission requires its original caller")
    if not isinstance(nonce, str) or re.fullmatch(r"[a-f0-9]{32}", nonce) is None:
        raise ValueError("shell lifetime admission requires a correlated request")
    descriptor = sys.stdin.fileno()
    receipt: dict = {"version": 1, "nonce": nonce, "owner_pid": owner_pid}
    if os.name == "nt":
        import msvcrt

        from agent_procutil._windows_parent_job import offer_lifetime_to_pipe_server

        # Until the complete request is delivered, cancellation cannot leave a
        # remote handle: the shell has not permitted creating an offer yet.
        _acknowledge(descriptor, "offer:" + nonce)
        offer = offer_lifetime_to_pipe_server(
            parent_pid=owner_pid, parent_birth=owner_birth,
            pipe_handle=msvcrt.get_osfhandle(descriptor),
        )
        receipt.update(scope="windows-job", job=asdict(offer.receipt))
        try:
            sys.stdout.write(json.dumps(receipt, ensure_ascii=True) + "\n")
            sys.stdout.flush()
        finally:
            # Even a failed publication must accept an explicit unadopted
            # abort; a partial/failed write cannot tell us what the shell saw.
            _windows_handoff(descriptor, nonce, offer)
    else:
        from agent_procutil._caller_owned import _require_posix_support

        if os.getppid() != owner_pid or not stat.S_ISFIFO(os.fstat(descriptor).st_mode):
            raise RuntimeError("shell lifetime pipe is not attached to its original caller")
        _require_posix_support()
        os.setsid()
        if os.getppid() != owner_pid:
            raise ProcessLookupError("original shell exited during lifetime admission")
        receipt.update(scope="posix-group", worker_pid=os.getpid())
    if os.name != "nt":
        sys.stdout.write(json.dumps(receipt, ensure_ascii=True) + "\n")
        sys.stdout.flush()
        _acknowledge(descriptor, nonce)
        _posix_callback(callback, descriptor, nonce)
        raise RuntimeError("caller-owned POSIX scope unexpectedly survived retirement")
    code = _callback_code(callback)
    _completion(nonce, code)
    return code
