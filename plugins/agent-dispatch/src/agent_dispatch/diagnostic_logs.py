"""Dispatch-owned diagnostic files, not native output or authoritative state."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from single_instance_lease import OperationLock, OperationLockedError

from . import __version__, install_paths

RETENTION = timedelta(days=30)
MAINTENANCE_SECONDS = 60.0
MAX_BYTES = 5_000_000
COMPONENTS = ("coordinator", "supervisor")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("diagnostic retention requires an aware clock")
    return value.astimezone(timezone.utc)


def _diagnostic_files(directory: Path, component: str) -> list[Path]:
    pattern = re.compile(
        rf"{component}(?:\.\d{{8}}T\d{{12}}Z-[0-9a-f]{{32}})?\.jsonl"
    )
    return sorted(p for p in directory.glob(f"{component}*.jsonl")
                  if pattern.fullmatch(p.name))


class DiagnosticLog(logging.Handler):
    """Short-lived file handles serialized with rotation and record-age pruning.

    Native/legacy capture handles never enter this namespace. The existing
    operation lock supplies thread/process exclusion, including Windows.
    """

    def __init__(self, component: str, root: Path, *, clock=utc_now, max_bytes=MAX_BYTES):
        super().__init__()
        if component not in COMPONENTS:
            raise ValueError("unknown diagnostic component")
        self.component = component
        self.directory = root.resolve() / "logs" / "diagnostics"
        self.path = self.directory / f"{component}.jsonl"
        self.baseFilename = str(self.path)
        self.clock = clock
        self.max_bytes = max_bytes
        self.last_error: str | None = None
        self._warned = False
        self._last_maintenance: datetime | None = None
        self._last_monotonic = 0.0
        from .companion import process_start_token

        try:
            self.start_token = process_start_token(os.getpid())
        except Exception:
            self.start_token = None
        self.maintain(force=True)

    @contextmanager
    def _custody(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = OperationLock(self.directory / f".{self.component}.lock")
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

    def _degraded(self, error: Exception) -> None:
        self.last_error = type(error).__name__
        if not self._warned:
            self._warned = True
            try:
                print(
                    f"agent-dispatch: {self.component} diagnostics degraded "
                    f"({self.last_error}); inspect `agent-dispatch logs`",
                    file=sys.stderr,
                )
            except Exception:
                pass

    def _files(self) -> list[Path]:
        return _diagnostic_files(self.directory, self.component)

    def _prune(self, path: Path, cutoff: datetime) -> None:
        # Compare records, not mtime or segment names: the frontier segment may
        # contain records on both sides of the exact elapsed-age cutoff.
        self._plain_file(path)
        temporary = path.with_name(f".{path.name}.prune-{uuid.uuid4().hex}")
        removed = False
        retained = False
        try:
            with path.open("rb") as source, temporary.open("xb") as target:
                for line in source:
                    try:
                        stamp = datetime.fromisoformat(json.loads(line)["ts"])
                        expired = _utc(stamp) < cutoff
                    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
                        expired = False
                        self._degraded(ValueError("unparseable diagnostic timestamp"))
                    if expired:
                        removed = True
                    else:
                        target.write(line)
                        retained = True
            if removed:
                if retained or path == self.path:
                    os.replace(temporary, path)
                else:
                    path.unlink()
        finally:
            temporary.unlink(missing_ok=True)

    def _maintain_locked(self, now: datetime, *, force: bool = False) -> None:
        monotonic = time.monotonic()
        if (
            not force and self._last_maintenance is not None
            and now >= self._last_maintenance
            and now - self._last_maintenance < timedelta(seconds=MAINTENANCE_SECONDS)
            and monotonic - self._last_monotonic < MAINTENANCE_SECONDS
        ):
            return
        self.last_error = None
        for path in self._files():
            try:
                self._prune(path, now - RETENTION)
            except Exception as error:
                self._degraded(error)
        self._last_maintenance = now
        self._last_monotonic = monotonic
        if self.last_error is None:
            self._warned = False

    def maintain(self, *, force: bool = False) -> None:
        """Prune on startup/owner maintenance, even without new log messages."""
        try:
            now = _utc(self.clock())
            with self._custody():
                self._maintain_locked(now, force=force)
        except Exception as error:
            self._degraded(error)

    def _rollover(self, now: datetime) -> None:
        try:
            self._plain_file(self.path)
            size = self.path.stat().st_size
            with self.path.open("rb") as source:
                first = json.loads(source.readline())
            first_day = _utc(datetime.fromisoformat(first["ts"])).date()
        except FileNotFoundError:
            return
        except (ValueError, KeyError, TypeError):
            first_day = None
            size = self.path.stat().st_size
        if size and (size >= self.max_bytes or first_day != now.date()):
            stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
            archive = self.path.with_name(
                f"{self.component}.{stamp}-{uuid.uuid4().hex}.jsonl"
            )
            os.replace(self.path, archive)

    @staticmethod
    def _plain_file(path: Path) -> None:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or path.is_symlink()
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise OSError("diagnostic data path is not a plain file")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            now = _utc(self.clock())
            stamp = datetime.fromtimestamp(record.created, timezone.utc)
            if stamp < now - RETENTION:
                return
            event = {
                "ts": stamp.isoformat(),
                "service": "agent-dispatch",
                "component": self.component,
                "logger": record.name,
                "level": record.levelname,
                "pid": record.process,
                "process_start_token": self.start_token if record.process == os.getpid() else None,
                "runtime_version": __version__ if record.process == os.getpid() else None,
                "runtime_prefix": sys.prefix if record.process == os.getpid() else None,
                "message": record.getMessage(),
            }
            for field in ("operation_id", "correlation_id", "generation"):
                if field in record.__dict__:
                    event[field] = record.__dict__[field]
            if record.exc_info:
                event["exception"] = logging.Formatter().formatException(record.exc_info)
            data = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
            with self._custody():
                self._maintain_locked(now)
                self._rollover(now)
                with self.path.open("ab") as target:
                    target.write(data)
        except Exception as error:
            self._degraded(error)


def maintain_file_logging() -> None:
    """Called by existing daemon maintenance; never starts a worker or timer."""
    for handler in logging.getLogger("agent-dispatch").handlers:
        if isinstance(handler, DiagnosticLog):
            handler.maintain()


def inventory(root: Path | None = None) -> dict:
    """Read-only discovery; absence and unmanaged coverage are not healthy logs."""
    root = (root if root is not None else install_paths.install_dir()).resolve()
    entries = []
    for component in COMPONENTS:
        directory = root / "logs" / "diagnostics"
        active = directory / f"{component}.jsonl"
        entries.append({
            "owner": f"agent-dispatch.{component}",
            "active": str(active),
            "exists": active.is_file(),
            "archives": [str(p) for p in _diagnostic_files(directory, component) if p != active],
            "retention": "30-day-record-age",
            "maintenance": "startup, emission and existing daemon maintenance",
            "write_health": "unknown-from-discovery",
            "unparseable_timestamps": "retained with a degraded diagnostic; never age-guessed",
        })
    # Only known diagnostic names: never enumerate session/task databases,
    # ownership receipts, credentials or arbitrary files under the runtime.
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
    override = os.environ.get("COPILOT_EXTENSIONS_BOOT_TRACE_LOG_PATH")
    if override:
        entries.append({
            "owner": "native boot-trace override",
            "active": override,
            "exists": Path(override).is_file(),
            "retention": "not-managed-by-application-handler",
        })
    return {
        "schema": "agent-dispatch.diagnostic-logs.v1",
        "root": str(root),
        "logs": entries,
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
