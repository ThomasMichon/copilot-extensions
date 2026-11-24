"""Dispatch compatibility and discovery over the shared optional logging owner."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from process_telemetry.buffer import BufferedSink
from process_telemetry.diagnostic_logs import (
    MAINTENANCE_SECONDS as MAINTENANCE_SECONDS, MAX_BYTES, RETENTION as RETENTION,
    DiagnosticLog as SharedDiagnosticLog, _diagnostic_files as _diagnostic_files,
    _utc as _utc, location, utc_now,
)
from single_instance_lease import OperationLock, OperationLockedError

from . import __version__, install_paths
from .spawn_diagnostics import current_inputs, diagnostic_text, exception_detail

COMPONENTS = ("coordinator", "supervisor")


class BufferStatus(TypedDict):
    mode: str
    last_error: str | None
    counts: dict[str, int]


@contextmanager
def operation_custody(path: Path) -> Iterator[None]:
    lock = OperationLock(path)
    deadline = time.monotonic() + 2.0
    while True:
        try:
            lock.acquire()
            break
        except OperationLockedError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.005)
    try:
        yield
    finally:
        lock.release()


class DiagnosticLog(SharedDiagnosticLog):
    def __init__(
        self, component: str, root: Path, *, clock: Callable[[], datetime] = utc_now,
        max_bytes: int = MAX_BYTES, buffered: bool = False,
    ) -> None:
        self._buffer: BufferedSink | None = None
        self._next_loss_report = 0.0
        if component not in COMPONENTS:
            raise ValueError("unknown diagnostic component")
        from .companion import process_start_token

        try:
            token = process_start_token(os.getpid())
        except Exception:
            token = None
        super().__init__(
            component, root.resolve() / "logs" / "diagnostics",
            service="agent-dispatch", custody=operation_custody,
            discovery_hint="inspect `agent-dispatch logs`",
            attribution={"process_start_token": token, "runtime_version": __version__,
                         "runtime_prefix": sys.prefix},
            clock=clock, max_bytes=max_bytes, initial_maintenance=not buffered,
        )
        if buffered:
            self._buffer = BufferedSink(
                self._write_buffered, capacity=128, max_bytes=65536, batch_size=32,
                on_start=lambda: self._maintain_buffered(force=True),
                on_iteration=self._maintain_buffered,
            )
            self._buffer.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            filtered = logging.makeLogRecord(record.__dict__.copy())
            secrets = current_inputs()
            # Spawn records are structured and already filtered by their producer.
            filtered.msg = (record.getMessage() if record.name == "agent-dispatch.spawn"
                            else diagnostic_text(record.getMessage(), secrets))
            filtered.args = ()
            filtered.exc_info = None
            filtered.exc_text = None
            filtered._dispatch_exception = None
            if record.exc_info:
                _kind, error, trace = record.exc_info
                if error is not None:
                    filtered._dispatch_exception = exception_detail(error, trace, secrets)
            for key in ("operation_id", "correlation_id", "generation"):
                value = getattr(filtered, key, None)
                if isinstance(value, str):
                    setattr(filtered, key, diagnostic_text(value, secrets))
        except Exception as error:
            self._degraded(error)
            return
        if self._buffer is None:
            super().emit(filtered)
            return
        try:
            event = self._record_fields(filtered)
            event["message"] = filtered.getMessage()
            for key in ("operation_id", "correlation_id", "generation"):
                if key in filtered.__dict__:
                    event[key] = filtered.__dict__[key]
            # The shared bounded buffer admits strings up to 512 characters.
            chunked = [key for key, value in event.items()
                       if isinstance(value, str) and len(value) > 512]
            self._buffer.offer({
                "fields": {
                    key: [value[i:i + 512] for i in range(0, len(value), 512)]
                    if key in chunked else value
                    for key, value in event.items()
                },
                "chunked": chunked,
            })
        except Exception as error:
            self._degraded(error)

    def _write_buffered(self, event: dict[str, Any]) -> None:
        self.write_event({
            key: "".join(value) if key in event["chunked"] else value
            for key, value in event["fields"].items()
        })

    def _maintain_buffered(self, *, force: bool = False) -> None:
        self.maintain(force=force)
        if self._buffer is None:
            return
        counts = self._buffer.stats()
        if not any(counts[key] for key in (
            "full", "invalid", "contended", "inactive", "sink_failed", "abandoned",
        )):
            return
        now = time.monotonic()
        if now < self._next_loss_report:
            return
        self._next_loss_report = now + MAINTENANCE_SECONDS
        self.write_event({
            "ts": _utc(self.clock()).isoformat(),
            "service": self.service, "component": self.component,
            "logger": "agent-dispatch.diagnostics", "level": "WARNING",
            "pid": os.getpid(), **self.attribution,
            "message": "diagnostic buffer loss accounting",
            "diagnostic_buffer": counts, "storage_error": self.last_error,
        })

    def buffer_status(self) -> BufferStatus:
        return {
            "mode": "buffered" if self._buffer is not None else "synchronous",
            "last_error": self.last_error,
            "counts": self._buffer.stats() if self._buffer is not None else {},
        }

    def flush(self) -> None:
        buffer = self._buffer
        if buffer is None:
            return
        target = buffer.stats()["accepted"]
        deadline = time.monotonic() + 0.25
        while time.monotonic() < deadline:
            counts = buffer.stats()
            if sum(counts[key] for key in ("delivered", "sink_failed", "abandoned")) >= target:
                return
            time.sleep(min(0.005, max(0.0, deadline - time.monotonic())))

    def close(self) -> None:
        if self._buffer is not None:
            self._buffer.close()
        super().close()

    def _record_fields(self, record: logging.LogRecord) -> dict[str, Any]:
        event = super()._record_fields(record)
        exception = getattr(record, "_dispatch_exception", None)
        if exception is not None:
            event["exception"] = exception
        return event


def maintain_file_logging() -> None:
    for handler in logging.getLogger("agent-dispatch").handlers:
        if isinstance(handler, DiagnosticLog) and handler._buffer is None:
            handler.maintain()


def file_logging_status() -> list[BufferStatus]:
    return [
        handler.buffer_status()
        for handler in logging.getLogger("agent-dispatch").handlers
        if isinstance(handler, DiagnosticLog)
    ]


def inventory(root: Path | None = None) -> dict[str, Any]:
    from . import telemetry

    root = (root if root is not None else install_paths.install_dir()).resolve()
    directory = root / "logs" / "diagnostics"
    entries = [location(directory, component, f"agent-dispatch.{component}")
               for component in COMPONENTS]
    legacy = (
        "logs/coordinator.log*", "logs/supervisor.log*",
        "logs/coordinator-generation.log*", "logs/self-deploy.log*",
        "logs/supervisor-daemon.log*", "logs/supervisor-successor.log*",
        "serve-service*.log*", "supervise-service*.log*", "supervise-launcher.log*",
        "reconcile.log*", "reconcile.err.log*", "logs/boot-trace.jsonl*",
        "lifecycle.log*",
    )
    entries.append({
        "owner": "native, legacy application, bootstrap and lifecycle writers",
        "locations": [str(root / pattern) for pattern in legacy],
        "existing": sorted({str(p) for pattern in legacy for p in root.glob(pattern)
                            if p.is_file()}),
        "retention": "not-managed-by-application-handler",
        "protection": "never renamed, truncated or deleted by this handler",
    })
    from .config import routing_dir

    routing = routing_dir().resolve() if root == install_paths.install_dir().resolve() else root
    entries.append({
        "owner": "zdd lifecycle writer (configured routing directory)",
        "locations": [str(routing / "lifecycle.log"), str(routing / "lifecycle.log.1")],
        "existing": [str(p) for p in (routing / "lifecycle.log", routing / "lifecycle.log.1")
                     if p.is_file()],
        "retention": "not-managed-by-application-handler",
    })
    override = os.environ.get("COPILOT_EXTENSIONS_BOOT_TRACE_LOG_PATH")
    if override:
        entries.append({
            "owner": "native boot-trace override", "active": override,
            "exists": Path(override).is_file(),
            "retention": "not-managed-by-application-handler",
        })
    return {
        "schema": "agent-dispatch.diagnostic-logs.v1", "root": str(root), "logs": entries,
        "telemetry": telemetry.configured_sink_inventory(),
        "outside_scope": [
            "installer attempts (#8370): active/incomplete evidence remains protected",
            "configured telemetry spools: producer configuration owns their paths",
            "container stdout/stderr: deployment adapter owns discovery and retention",
            "session/task state and ownership receipts: not diagnostic log files",
        ],
    }


def register_log_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = subparsers.add_parser("logs", help="discover local diagnostic log locations (JSON)")

    def show(_args: argparse.Namespace) -> int:
        print(json.dumps(inventory()))
        return 0

    command.set_defaults(func=show)
