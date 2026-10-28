"""Compiler-free, pipe-bound lifetime for a synchronous shell callback."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
import re
import stat
import sys
import threading
import traceback
from typing import Callable


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


def _posix_callback(callback: Callable[[], int], descriptor: int, nonce: str) -> None:
    from agent_procutil._caller_owned import _retire

    reader, writer = os.pipe()
    completed_reader = completed_writer = -1
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

        def watch() -> None:
            try:
                os.read(descriptor, 1)
            finally:
                _retire()

        threading.Thread(
            target=watch, name="shell-caller-lifetime", daemon=True,
        ).start()
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
            for endpoint in (reader, writer, completed_reader, completed_writer):
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
        from agent_procutil._windows_parent_job import transfer_lifetime_to_pipe_server

        job = transfer_lifetime_to_pipe_server(
            parent_pid=owner_pid, parent_birth=owner_birth,
            pipe_handle=msvcrt.get_osfhandle(descriptor),
        )
        receipt.update(scope="windows-job", job=asdict(job))
    else:
        from agent_procutil._caller_owned import _require_posix_support

        if os.getppid() != owner_pid or not stat.S_ISFIFO(os.fstat(descriptor).st_mode):
            raise RuntimeError("shell lifetime pipe is not attached to its original caller")
        _require_posix_support()
        os.setsid()
        if os.getppid() != owner_pid:
            raise ProcessLookupError("original shell exited during lifetime admission")
        receipt.update(scope="posix-group", worker_pid=os.getpid())
    sys.stdout.write(json.dumps(receipt, ensure_ascii=True) + "\n")
    sys.stdout.flush()
    _acknowledge(descriptor, nonce)
    if os.name != "nt":
        _posix_callback(callback, descriptor, nonce)
        raise RuntimeError("caller-owned POSIX scope unexpectedly survived retirement")
    code = _callback_code(callback)
    _completion(nonce, code)
    return code
