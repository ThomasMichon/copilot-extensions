"""Invocation-owned, bounded commands yielding provider-neutral readings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, DecimalException
from io import BufferedReader
import json
import math
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread
import time
from typing import Mapping

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

from .models import (
    BudgetConfig, BudgetReading, ExternalCommandAdapter, ModelError, StaticAdapter,
    _strict_object, parse_reading, unauthorized_adapter,
)


@dataclass(frozen=True)
class CommandReader:
    """An explicit caller grant, never parsed from budget configuration."""

    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float = 10
    max_output_bytes: int = 65536

    def __post_init__(self) -> None:
        if (
            not isinstance(self.argv, tuple) or not self.argv
            or any(not isinstance(arg, str) or "\0" in arg for arg in self.argv)
            or not Path(self.argv[0]).is_absolute()
        ):
            raise ModelError("reader argv requires an absolute executable and literal arguments")
        if not isinstance(self.cwd, Path) or not self.cwd.is_absolute():
            raise ModelError("reader cwd must be an explicit absolute path")
        executable_name = Path(self.argv[0]).name.rstrip(" .")
        if os.name == "nt" and ":" in executable_name:
            raise ModelError("reader executable streams are unsupported")
        if executable_name.lower().endswith((".cmd", ".bat")):
            raise ModelError("reader batch executables are unsupported; use an explicit interpreter")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 30
            or not math.isfinite(self.timeout_seconds)
        ):
            raise ModelError("reader timeout must be finite and within (0, 30] seconds")
        if type(self.max_output_bytes) is not int or not 1 <= self.max_output_bytes <= 65536:
            raise ModelError("reader output limit must be within [1, 65536] bytes")


class ReaderFailure(Exception):
    """A sanitized acquisition failure, with no command or output content."""


def _read_output(stream: BufferedReader, limit: int, result: Queue[bytes | ReaderFailure]) -> None:
    data = bytearray()
    try:
        while len(data) <= limit:
            chunk = stream.read1(min(4096, limit + 1 - len(data)))
            if not chunk:
                result.put(bytes(data))
                return
            data.extend(chunk)
        result.put(ReaderFailure("reader output exceeds byte limit"))
    except OSError:
        result.put(ReaderFailure("reader output could not be read"))


def _read_status(
    stream: BufferedReader, status: Queue[bytes | ReaderFailure],
    cleanup: Queue[bytes | ReaderFailure],
) -> None:
    reported = False
    try:
        status.put(stream.readline(65))
        reported = True
        cleanup.put(stream.read(65))
    except OSError:
        failure = ReaderFailure("reader host status could not be read")
        # Each queue has one producer; a failed second read must not refill status.
        if not reported:
            status.put(failure)
        cleanup.put(failure)


def _run(reader: CommandReader) -> bytes:
    windows = os.name == "nt"
    posix_capabilities = (
        "posix_spawn", "POSIX_SPAWN_OPEN", "killpg", "waitid", "waitpid",
        "P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "CLD_EXITED",
    )
    if not windows and not all(hasattr(os, name) for name in posix_capabilities):
        raise ReaderFailure("bounded reader execution is unsupported on this runtime")
    if not windows and (
        not sys.executable or not Path(sys.executable).is_absolute()
        or not Path(sys.executable).is_file() or not os.access(sys.executable, os.X_OK)
    ):
        raise ReaderFailure("reader host interpreter is unavailable")
    deadline = time.monotonic() + reader.timeout_seconds
    try:
        argv = list(reader.argv) if windows else [
            sys.executable, "-I", "-S",
            str(Path(__file__).resolve().with_name("_command_host.py")), *reader.argv,
        ]
        process, job = spawn_sync_in_kill_on_close_job(
            argv, cwd=reader.cwd, stdin=subprocess.DEVNULL if windows else subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL if windows else subprocess.PIPE,
            require_job=windows, allow_breakaway=False,
            **no_window_kwargs(),
        )
    except (OSError, RuntimeError):
        raise ReaderFailure("reader launch or containment failed") from None
    assert process.stdout is not None
    result: Queue[bytes | ReaderFailure] = Queue(maxsize=1)
    thread = Thread(
        target=_read_output, args=(process.stdout, reader.max_output_bytes, result),
        daemon=True,
    )
    status_result: Queue[bytes | ReaderFailure] = Queue(maxsize=1)
    cleanup_result: Queue[bytes | ReaderFailure] = Queue(maxsize=1)
    status_thread: Thread | None = None
    if not windows:
        assert process.stderr is not None
        status_thread = Thread(
            target=_read_status, args=(process.stderr, status_result, cleanup_result), daemon=True,
        )
    primary_error: ReaderFailure | None = None
    try:
        try:
            thread.start()
            if status_thread is not None:
                status_thread.start()
        except RuntimeError:
            raise ReaderFailure("reader output worker could not start") from None
        try:
            output = result.get(timeout=max(0, deadline - time.monotonic()))
        except Empty:
            raise ReaderFailure("reader deadline exceeded") from None
        if isinstance(output, ReaderFailure):
            raise output
        if not windows:
            try:
                status = status_result.get(timeout=max(0, deadline - time.monotonic()))
            except Empty:
                raise ReaderFailure("reader deadline exceeded") from None
            if isinstance(status, ReaderFailure):
                raise ReaderFailure("reader host status could not be read")
            try:
                returncode = int(status.decode("ascii").strip())
            except (UnicodeDecodeError, ValueError):
                raise ReaderFailure("reader host returned invalid process status") from None
            if time.monotonic() >= deadline:
                raise ReaderFailure("reader deadline exceeded")
            if returncode != 0:
                raise ReaderFailure("reader exited unsuccessfully")
            return output
        while True:
            if time.monotonic() >= deadline:
                raise ReaderFailure("reader deadline exceeded")
            returncode = process.poll()
            if returncode is not None:
                if time.monotonic() >= deadline:
                    raise ReaderFailure("reader deadline exceeded")
                if returncode != 0:
                    raise ReaderFailure("reader exited unsuccessfully")
                return output
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReaderFailure("reader deadline exceeded")
            time.sleep(min(0.01, remaining))
    except ReaderFailure as exc:
        primary_error = exc
        raise
    except OSError:
        primary_error = ReaderFailure("reader process status could not be read")
        raise primary_error from None
    finally:
        try:
            ownership_lost = False
            if job is not None:
                job.terminate_and_wait(5)
            else:
                ownership_lost = process.poll() is not None
                assert process.stdin is not None
                try:
                    process.stdin.write(b"C")
                    process.stdin.flush()
                finally:
                    process.stdin.close()
            exit_code = process.wait(timeout=5)
            if not windows:
                if status_thread is not None and status_thread.ident is None:
                    assert process.stderr is not None
                    _read_status(process.stderr, status_result, cleanup_result)
                acknowledgment = cleanup_result.get(timeout=5)
                if ownership_lost:
                    raise ReaderFailure("reader host ownership was lost")
                if exit_code != 0 or acknowledgment != b"cleaned\n":
                    raise ReaderFailure("reader host cleanup was not acknowledged")
            for worker in (thread, status_thread):
                if worker is not None and worker.ident is not None:
                    worker.join(timeout=5)
                    if worker.is_alive():
                        raise ReaderFailure("reader output cleanup could not be confirmed")
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        except (ReaderFailure, Empty, OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
            cleanup_error = "reader process cleanup could not be confirmed"
            if isinstance(exc, ReaderFailure):
                cleanup_error = f"{exc}; {cleanup_error}"
            if primary_error is not None:
                cleanup_error = f"{primary_error}; {cleanup_error}"
            raise ReaderFailure(cleanup_error) from None


def _acquire(adapter: ExternalCommandAdapter, reader: CommandReader | None) -> StaticAdapter:
    if reader is None:
        return unauthorized_adapter(adapter, datetime.now(timezone.utc))
    error: str | None = None
    availability = "error"
    try:
        raw = _run(reader)
        acquired_at = datetime.now(timezone.utc)
        try:
            value = json.loads(
                raw.decode("utf-8"), parse_float=Decimal, parse_int=Decimal,
                object_pairs_hook=_strict_object,
            )
            reading = parse_reading(value)
        except (ValueError, UnicodeDecodeError, TypeError, RecursionError, DecimalException):
            raise ReaderFailure("reader returned an invalid budget reading") from None
        if reading.source != adapter.source:
            raise ReaderFailure("reader source does not match configured source")
        if reading.captured_at > acquired_at:
            raise ReaderFailure("reader capture time is in the future")
        # Reader error text may contain credentials; retain state, not opaque text.
        if reading.error is not None:
            reading = replace(reading, error="reader reported an acquisition error")
        return StaticAdapter(adapter.adapter_id, adapter.authority, reading, acquired_at)
    except ReaderFailure as exc:
        error = str(exc)
    return StaticAdapter(
        adapter.adapter_id, adapter.authority,
        BudgetReading(
            source=adapter.source, captured_at=datetime.now(timezone.utc),
            freshness_seconds=0, availability=availability, error=error,
        ),
    )


def acquire_config(
    config: BudgetConfig, readers: Mapping[str, CommandReader] | None = None,
) -> BudgetConfig:
    """Acquire each external adapter once; configuration never grants execution."""
    grants = readers or {}
    external_ids = {
        adapter.adapter_id for adapter in config.adapters
        if isinstance(adapter, ExternalCommandAdapter)
    }
    unexpected = set(grants) - external_ids
    if unexpected:
        raise ModelError(
            "reader grant does not match a configured external adapter "
            f"({len(unexpected)} unmatched; {len(external_ids)} configured)"
        )
    if any(not isinstance(reader, CommandReader) for reader in grants.values()):
        raise ModelError("reader grants must be invocation-owned CommandReader values")
    return BudgetConfig(tuple(
        _acquire(adapter, grants.get(adapter.adapter_id))
        if isinstance(adapter, ExternalCommandAdapter) else adapter
        for adapter in config.adapters
    ))
