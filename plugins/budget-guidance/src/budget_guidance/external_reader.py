"""Invocation-owned, bounded commands yielding provider-neutral readings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from io import BufferedReader
import json
import math
import os
from pathlib import Path
from queue import Empty, Queue
import signal
import subprocess
import sys
from threading import Thread
import time
from typing import Mapping

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

from .models import (
    BudgetConfig, BudgetReading, ExternalCommandAdapter, ModelError, StaticAdapter,
    _strict_object, parse_reading,
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
        if Path(self.argv[0]).suffix.lower() in {".cmd", ".bat"}:
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


def _run(reader: CommandReader) -> bytes:
    windows = os.name == "nt"
    posix_capabilities = (
        "waitid", "WNOWAIT", "WEXITED", "WNOHANG", "P_PID", "CLD_EXITED",
        "waitstatus_to_exitcode", "setpgid", "execv", "kill", "killpg",
    )
    if not windows and (
        not all(hasattr(os, name) for name in posix_capabilities)
        or not all(hasattr(signal, name) for name in ("SIGSTOP", "SIGKILL"))
    ):
        raise ReaderFailure("bounded reader execution is unsupported on this runtime")
    deadline = time.monotonic() + reader.timeout_seconds
    try:
        argv = list(reader.argv) if windows else [
            sys.executable, str(Path(__file__).with_name("_command_host.py")), *reader.argv,
        ]
        process, job = spawn_sync_in_kill_on_close_job(
            argv, cwd=reader.cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
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
    try:
        try:
            thread.start()
        except RuntimeError:
            raise ReaderFailure("reader output worker could not start") from None
        try:
            output = result.get(timeout=max(0, deadline - time.monotonic()))
        except Empty:
            raise ReaderFailure("reader deadline exceeded") from None
        if isinstance(output, ReaderFailure):
            raise output
        while True:
            if windows:
                returncode = process.poll()
            else:
                # Keep the root unreaped until group cleanup: its PID cannot be reused.
                status = os.waitid(
                    os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT,
                )
                returncode = (
                    None if status is None else os.waitstatus_to_exitcode(
                        status.si_status << 8 if status.si_code == os.CLD_EXITED
                        else status.si_status,
                    )
                )
            if returncode is not None:
                if returncode != 0:
                    raise ReaderFailure("reader exited unsuccessfully")
                return output
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReaderFailure("reader deadline exceeded")
            time.sleep(min(0.01, remaining))
    except OSError:
        raise ReaderFailure("reader process status could not be read") from None
    finally:
        try:
            if job is not None:
                job.terminate_and_wait(5)
            else:
                # Freeze startup before testing its group: no race can fork outside cleanup.
                os.kill(process.pid, signal.SIGSTOP)
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    process.kill()
            process.wait(timeout=5)
            if thread.ident is not None:
                thread.join(timeout=5)
                if thread.is_alive():
                    raise ReaderFailure("reader output cleanup could not be confirmed")
            process.stdout.close()
        except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired):
            raise ReaderFailure("reader process cleanup could not be confirmed") from None


def _acquire(adapter: ExternalCommandAdapter, reader: CommandReader | None) -> StaticAdapter:
    error: str | None = None
    availability = "error"
    if reader is None:
        availability = "unavailable"
        error = "reader is not authorized for this invocation"
    else:
        try:
            raw = _run(reader)
            acquired_at = datetime.now(timezone.utc)
            try:
                value = json.loads(
                    raw.decode("utf-8"), parse_float=Decimal, parse_int=Decimal,
                    object_pairs_hook=_strict_object,
                )
                reading = parse_reading(value)
            except (ValueError, UnicodeDecodeError, TypeError, RecursionError):
                raise ReaderFailure("reader returned an invalid budget reading") from None
            if reading.source != adapter.source:
                raise ReaderFailure("reader source does not match configured source")
            if reading.captured_at > acquired_at:
                raise ReaderFailure("reader capture time is in the future")
            # Reader error text may contain credentials; retain state, not opaque text.
            if reading.error is not None:
                reading = replace(reading, error="reader reported an acquisition error")
            return StaticAdapter(adapter.adapter_id, adapter.authority, reading)
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
