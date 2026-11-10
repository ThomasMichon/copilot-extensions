"""Retain an unreaped group owner until all launch descendants are cleaned up."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from collections.abc import Iterator
from typing import IO
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time


_OWNER = """
import json,os,signal,subprocess,sys
try:
    try:
        try:
            child = subprocess.Popen(
                json.loads(sys.argv[1]), stdin=subprocess.DEVNULL,
                stdout=int(sys.argv[2]), stderr=sys.stderr,
            )
        finally:
            os.close(int(sys.argv[2]))
    except OSError as exc:
        print(json.dumps({"error": str(exc)}), flush=True)
    else:
        print(json.dumps({"returncode": child.wait()}), flush=True)
    sys.stdin.buffer.read(1)
finally:
    os.killpg(os.getpgrp(), signal.SIGKILL)
"""

MAX_RECEIPT_BYTES = 4096


class EngineCleanupError(RuntimeError):
    """An owned engine resource failed retirement, independently of platform or read outcome."""


@contextmanager
def cleanup_phase() -> Iterator[None]:
    try:
        yield
    except EngineCleanupError:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise EngineCleanupError(f"owned engine resource retirement failed: {exc}") from exc


def close_descriptor(fd: int) -> None:
    with cleanup_phase():
        os.close(fd)


def close_stream(stream: IO[bytes] | IO[str]) -> None:
    with cleanup_phase():
        stream.close()


def output_pipe() -> tuple[IO[bytes], int]:
    read_fd, write_fd = os.pipe()
    try:
        return os.fdopen(read_fd, "rb"), write_fd
    except (OSError, ValueError):
        with cleanup_phase():
            os.close(read_fd)
            os.close(write_fd)
        raise


@contextmanager
def _capture_file() -> Iterator[IO[bytes]]:
    output = tempfile.TemporaryFile()
    try:
        yield output
    finally:
        close_stream(output)


def parse_completion_receipt(raw: bytes) -> dict:
    if len(raw) > MAX_RECEIPT_BYTES or not raw.endswith(b"\n"):
        raise OSError("launch group owner returned an oversized or truncated completion receipt")

    def fields(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate completion receipt field")
            value[key] = item
        return value

    try:
        receipt = json.loads(raw, object_pairs_hook=fields)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise OSError("launch group owner returned no valid completion receipt") from exc
    if isinstance(receipt, dict) and set(receipt) == {"error"}:
        if isinstance(receipt["error"], str) and receipt["error"]:
            return receipt
    if isinstance(receipt, dict) and set(receipt) == {"returncode"}:
        if type(receipt["returncode"]) is int:
            return receipt
    raise OSError("launch group owner returned an invalid completion receipt")


def read_completion_receipt(output, *, argv, timeout, cancelled=None) -> dict:
    """Read bounded partial receipt bytes without waiting/reaping the group owner."""
    deadline = None if timeout is None else time.monotonic() + timeout
    stream = getattr(output, "buffer", output)
    read = getattr(stream, "read1", stream.read)
    pending = bytearray()
    while True:
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError("owned engine read cancelled")
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        wait = 0.1 if remaining is None else min(remaining, 0.1)
        ready, _, _ = select.select([stream], [], [], wait)
        if not ready:
            if cancelled is None and deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(argv, timeout)
            continue
        chunk = read(MAX_RECEIPT_BYTES + 1 - len(pending))
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        if not chunk:
            raise OSError("launch group owner returned a missing or truncated completion receipt")
        pending.extend(chunk)
        if len(pending) > MAX_RECEIPT_BYTES:
            raise OSError("launch group owner returned an oversized completion receipt")
        if b"\n" in pending:
            return parse_completion_receipt(bytes(pending))


def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    timeout = kwargs.pop("timeout")
    cancelled = kwargs.pop("cancelled", None)
    kwargs.pop("check", None)
    kwargs.pop("stdin", None)
    destination = kwargs.pop("stdout", None)
    text = kwargs.get("text", False)
    external_output = callable(getattr(destination, "fileno", None))
    with (nullcontext(destination) if external_output else _capture_file()) as output, ExitStack() as cleanup:
        capture_errors = kwargs.get("stderr") == subprocess.PIPE
        errors = cleanup.enter_context(_capture_file()) if capture_errors else None
        if errors is not None:
            kwargs["stderr"] = errors
        owner = subprocess.Popen(
            [sys.executable, "-B", "-c", _OWNER, json.dumps(argv), str(output.fileno())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            start_new_session=True, pass_fds=(output.fileno(),), **kwargs,
        )
        stdout = stderr = None
        receipt = None
        try:
            receipt = read_completion_receipt(
                owner.stdout, argv=argv, timeout=timeout, cancelled=cancelled,
            )
            if "error" in receipt:
                raise OSError(receipt["error"])
            stdout = None
            if not external_output:
                output.seek(0)
                stdout = output.read()
            stderr = None
            if errors is not None:
                errors.seek(0)
                stderr = errors.read()
            if text:
                if stdout is not None:
                    stdout = stdout.decode(kwargs.get("encoding") or "utf-8", errors="replace")
                if stderr is not None:
                    stderr = stderr.decode(kwargs.get("encoding") or "utf-8", errors="replace")
            return subprocess.CompletedProcess(argv, receipt["returncode"], stdout, stderr)
        finally:
            # No poll/wait reaps this owner before the group sweep, reserving its PID
            # even if the engine already exited or the owner itself became a zombie.
            try:
                with cleanup_phase():
                    try:
                        os.killpg(owner.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    owner.communicate(timeout=5)
            except EngineCleanupError as error:
                error.process = owner
                error.group_id = owner.pid
                error.stdout = stdout
                error.stderr = stderr
                error.returncode = receipt.get("returncode") if receipt else None
                raise
