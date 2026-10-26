"""Dispatch compatibility and discovery over the shared optional logging owner."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from process_telemetry.diagnostic_logs import (
    MAINTENANCE_SECONDS as MAINTENANCE_SECONDS, MAX_BYTES, RETENTION as RETENTION,
    DiagnosticLog as SharedDiagnosticLog, _diagnostic_files as _diagnostic_files,
    _utc as _utc, location, utc_now,
)
from single_instance_lease import OperationLock, OperationLockedError

from . import __version__, install_paths
from .spawn_diagnostics import current_inputs, diagnostic_text, exception_detail

COMPONENTS = ("coordinator", "supervisor")


@contextmanager
def operation_custody(path: Path):
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
    def __init__(self, component: str, root: Path, *, clock=utc_now, max_bytes=MAX_BYTES):
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
            clock=clock, max_bytes=max_bytes,
        )

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
        super().emit(filtered)

    def _record_fields(self, record: logging.LogRecord) -> dict:
        event = super()._record_fields(record)
        if getattr(record, "_dispatch_exception", None) is not None:
            event["exception"] = record._dispatch_exception
        return event


def maintain_file_logging() -> None:
    for handler in logging.getLogger("agent-dispatch").handlers:
        if isinstance(handler, DiagnosticLog):
            handler.maintain()


def inventory(root: Path | None = None) -> dict:
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


def register_log_commands(subparsers) -> None:
    command = subparsers.add_parser("logs", help="discover local diagnostic log locations (JSON)")

    def show(_args) -> int:
        print(json.dumps(inventory()))
        return 0

    command.set_defaults(func=show)
