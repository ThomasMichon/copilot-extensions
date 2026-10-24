"""Bridge-owned application logging; native capture and installers stay separate."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from process_telemetry.buffered_logs import BufferedDiagnosticLog
from process_telemetry.diagnostic_logs import DiagnosticLog, location
from single_instance_lease import OperationLock, OperationLockedError

from . import install_paths

_last_handler = None
NAMESPACES = ("agent-bridge", "agent_bridge")


@contextmanager
def operation_custody(path: Path):
    # Worker/synchronous opt-in only, never entered by the async log producer.
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


def configure_file_logging(root: Path | None = None) -> BufferedDiagnosticLog:
    global _last_handler
    package = logging.getLogger("agent-bridge")
    for handler in package.handlers:
        if isinstance(handler, BufferedDiagnosticLog):
            if not handler.writer.closed:
                return handler
            close_file_logging(handler)
            break
    if _last_handler is not None and not _last_handler.writer.join(0):
        raise RuntimeError("previous Bridge diagnostic writer has not stopped")
    from zdd.diagnostics import process_start_time
    from . import __version__

    try:
        token = process_start_time(os.getpid())
    except Exception:
        token = None
    root = root if root is not None else install_paths.effective_config_dir()
    store = DiagnosticLog(
        "daemon", root.resolve() / "logs" / "diagnostics",
        service="agent-bridge", custody=operation_custody, initial_maintenance=False,
        discovery_hint="inspect `agent-bridge logs`",
        attribution={"process_start_token": token, "runtime_version": __version__,
                     "runtime_prefix": sys.prefix},
    )
    handler = BufferedDiagnosticLog(store)
    handler._previous_loggers = {}
    for namespace in NAMESPACES:
        logger = logging.getLogger(namespace)
        handler._previous_loggers[namespace] = (logger.level, logger.propagate)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    _last_handler = handler
    return handler


def maintain_file_logging() -> None:
    for handler in logging.getLogger("agent-bridge").handlers:
        if isinstance(handler, BufferedDiagnosticLog):
            handler.maintain()


def close_file_logging(handler: BufferedDiagnosticLog) -> None:
    handler.close()  # reject/abandon, no joins or EOF waits on daemon shutdown
    for namespace, (level, propagate) in handler._previous_loggers.items():
        logger = logging.getLogger(namespace)
        if handler in logger.handlers:
            logger.removeHandler(handler)
            logger.setLevel(level)
            logger.propagate = propagate


def logging_status() -> dict:
    for handler in logging.getLogger("agent-bridge").handlers:
        if isinstance(handler, BufferedDiagnosticLog):
            return {"configured": True, "scope": "application-only",
                    "accepting": not handler.writer.closed,
                    "buffer_callbacks": handler.writer.stats(),
                    "storage_error": handler.store.last_error}
    return {"configured": False, "scope": "application-only"}


def inventory() -> dict:
    from . import telemetry

    root = install_paths.effective_config_dir().resolve()
    native_root = install_paths.install_dir().resolve()
    entry = location(root / "logs" / "diagnostics", "daemon", "agent-bridge.daemon")
    entry["delivery"] = "bounded BufferedSink; invalid/full/contended/abandoned records may be lost"
    entry["maintenance"] = "startup and existing bounded writer iteration, including idle periods"
    entry["counter_observation"] = "ordinary diagnostic_buffer_callbacks records in these files"
    patterns = (
        "agent-bridge.log*", "agent-bridge-err.log*", "lifecycle.log*",
        "reconcile.log*", "reconcile.err.log*", "logs/boot-trace.jsonl*",
    )
    roots = {root, native_root}
    logs = [entry, {
        "owner": "native capture, bootstrap, lifecycle and legacy writers",
        "locations": sorted(str(base / pattern) for base in roots for pattern in patterns),
        "existing": sorted({str(p) for base in roots for pattern in patterns
                            for p in base.glob(pattern) if p.is_file()}),
        "retention": "not-managed-by-application-handler",
        "protection": "never renamed, truncated or deleted by this handler",
    }]
    override = os.environ.get("COPILOT_EXTENSIONS_BOOT_TRACE_LOG_PATH")
    if override:
        logs.append({"owner": "native boot-trace override", "active": override,
                     "exists": Path(override).is_file(),
                     "retention": "not-managed-by-application-handler"})
    return {
        "schema": "agent-bridge.diagnostic-logs.v1", "root": str(root), "logs": logs,
        "telemetry": telemetry.configured_sink_inventory(),
        "outside_scope": [
            "installer attempts (#8370): active/incomplete evidence stays protected",
            "legacy telemetry spools: unchanged producer-configured paths",
            "native stdout/stderr, session child diagnostics and container output",
            "authoritative sessions, event databases and ownership receipts",
        ],
    }


def register_log_commands(subparsers) -> None:
    command = subparsers.add_parser("logs", help="discover local diagnostic locations (JSON)")

    def show(_args):
        print(json.dumps(inventory()))

    command.set_defaults(func=show)
