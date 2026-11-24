"""Local command evidence; callers own message shaping and operation semantics."""
from __future__ import annotations

import contextvars
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from agent_procutil import python_cache_permissions as permissions
from agent_procutil._daemon_instance_owner import open_private
from agent_procutil.diagnostics import safe_diagnostic_text
from single_instance_lease.operation import OperationLock, OperationLockedError

from .diagnostic_logs import DiagnosticLog

SCHEMA = "command-error.v1"
_IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
_PHASES = {"entry", "parser", "application", "child", "completion"}
_CURRENT: contextvars.ContextVar[CommandErrorRecorder | None] = contextvars.ContextVar(
    "command_error_recorder", default=None,
)


def default_directory() -> Path:
    """Return the user-local fallback without inspecting or creating a runtime."""
    return Path.home() / ".copilot-extensions" / "logs" / "diagnostics" / "commands"


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("command evidence requires a fixed identifier")
    return value


def _prepare(directory: Path) -> None:
    missing = []
    current = directory
    while not current.exists() and not current.is_symlink():
        missing.append(current)
        current = current.parent
    permissions.check_ancestors(current)
    permissions.check(current, ancestor=True)
    for path in reversed(missing):
        permissions.mkdir(path)
    permissions.check(directory, private=True)


@contextmanager
def _custody(path: Path) -> Iterator[None]:
    permissions.check(path.parent, private=True)
    lock = OperationLock(path)
    deadline = time.monotonic() + 0.1
    while True:
        try:
            lock.acquire()
            break
        except OperationLockedError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.005)
    try:
        active = path.parent / "command-errors.jsonl"
        try:
            fd = open_private(active, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            permissions.check_file(active)
        else:
            os.close(fd)
        yield
    finally:
        lock.release()


class CommandErrorRecorder:
    """Append explicit safe causes using existing diagnostic retention/custody.

    ``message`` is producer-shaped diagnostic text, never an arbitrary stream,
    exception representation, argument vector or request body. Redaction is
    defense in depth, not permission to supply those excluded inputs.
    """

    def __init__(
        self, command: str, source: str, *, directory: Path | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.command = _identifier(command)
        self.source = _identifier(source)
        self.directory = (directory if directory is not None else default_directory()).absolute()
        self.correlation_id = _identifier(correlation_id or uuid.uuid4().hex)
        parent = current_recorder()
        self.parent_id = parent.correlation_id if parent else None
        self.last_error: str | None = None
        self._store: DiagnosticLog | None = None
        self._warned = False

    def _degraded(self, error: Exception) -> None:
        self.last_error = type(error).__name__
        if not self._warned:
            self._warned = True
            try:
                print(
                    f"{self.command}: command evidence unavailable ({self.last_error}); "
                    "inspect command-error discovery",
                    file=sys.stderr,
                )
            except Exception:
                pass

    def _writer(self) -> DiagnosticLog:
        if self._store is None:
            self._store = DiagnosticLog(
                "command-errors", self.directory, service=self.command, custody=_custody,
                initial_maintenance=False, create_parent=False, file_mode=0o600,
                file_opener=open_private,
                discovery_hint="inspect command-error discovery",
            )
        return self._store

    def record(
        self, code: str, message: str, *, phase: str = "application",
        exit_code: int | None = None, completeness: str = "producer-shaped",
    ) -> bool:
        """Record one error without changing the primary result on write failure."""
        try:
            _identifier(code)
            if phase not in _PHASES or type(exit_code) not in (int, type(None)):
                raise ValueError("invalid command error phase or result")
            if completeness not in {"producer-shaped", "metadata-only", "incomplete"}:
                raise ValueError("invalid command error completeness")
            if self._store is None:
                _prepare(self.directory)
            store = self._writer()
            event: dict[str, Any] = {
                "schema": SCHEMA, "ts": datetime.now(timezone.utc).isoformat(),
                "command": self.command, "source": self.source, "phase": phase,
                "code": code, "message": safe_diagnostic_text(message, 1600),
                "exit_code": exit_code, "correlation_id": self.correlation_id,
                "completeness": completeness, "pid": os.getpid(),
            }
            if self.parent_id:
                event["parent_id"] = self.parent_id
            store.last_error = None
            store.write_event(event)
            self.last_error = store.last_error
            return self.last_error is None
        except Exception as error:
            self._degraded(error)
            return False

    def maintain(self, *, force: bool = False) -> None:
        """Entry-startup/owner maintenance; at most hourly across invocations.

        A fresh recorder can maintain existing storage. Absent storage stays
        absent. Record emission and read-only discovery never call this method.
        """
        try:
            if not os.path.lexists(self.directory):
                return
            permissions.check_ancestors(self.directory)
            permissions.check(self.directory, private=True)
            marker = self.directory / ".command-errors-maintenance"
            now = time.time()

            def due() -> bool:
                try:
                    permissions.check_file(marker)
                    elapsed = now - marker.stat().st_mtime
                    return elapsed >= 3600 or elapsed < -3600
                except FileNotFoundError:
                    return True

            if not force and not due():
                return
            with _custody(self.directory / ".command-errors.lock"):
                if not force and not due():
                    return
                try:
                    fd = open_private(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    permissions.check_file(marker)
                    os.utime(marker, None)
                else:
                    os.close(fd)
            store = self._writer()
            store.maintain(force=True)
            self.last_error = store.last_error
        except Exception as error:
            self._degraded(error)

    @contextmanager
    def activate(self) -> Iterator[CommandErrorRecorder]:
        """Make explicit producer reports available inside this invocation."""
        token = _CURRENT.set(self)
        previous = getattr(sys, "_command_error_recorder", None)
        sys._command_error_recorder = self  # type: ignore[attr-defined]
        try:
            yield self
        finally:
            if previous is None:
                del sys._command_error_recorder  # type: ignore[attr-defined]
            else:
                sys._command_error_recorder = previous  # type: ignore[attr-defined]
            _CURRENT.reset(token)


def current_recorder() -> CommandErrorRecorder | None:
    """Return only the current in-process invocation, without discovery."""
    return _CURRENT.get() or cast(
        CommandErrorRecorder | None, getattr(sys, "_command_error_recorder", None),
    )


def recent_errors(
    directory: Path | None = None, *, limit: int = 20, max_bytes: int = 262144,
    max_files: int = 16,
) -> dict[str, Any]:
    """Read bounded tails and inventory without creating storage or taking locks."""
    if not 1 <= limit <= 1000 or not 1 <= max_files <= 128 or not 1 <= max_bytes <= 4194304:
        raise ValueError("command discovery bounds exceeded")
    directory = (directory if directory is not None else default_directory()).absolute()
    result: dict[str, Any] = {
        "schema": "command-error-discovery.v1", "directory": str(directory),
        "exists": False, "records": [], "truncated": False, "unavailable": [],
        "write_health": "unknown-from-discovery",
    }
    if not os.path.lexists(directory):
        return result
    try:
        permissions.check_ancestors(directory)
        permissions.check(directory, private=True)
        result["exists"] = True
        active = directory / "command-errors.jsonl"
        paths = [active] if os.path.lexists(active) else []
        pattern = re.compile(r"command-errors(?:\.\d{8}T\d{12}Z-[0-9a-f]{32})?\.jsonl\Z")
        # Bound inventory work as well as result size.
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= 256:
                    result["truncated"] = True
                    # Do not present an arbitrary prefix as the newest archives.
                    paths = [active] if os.path.lexists(active) else []
                    result["unavailable"].append({"reason": "archive-inventory-truncated"})
                    break
                if entry.name != active.name and pattern.fullmatch(entry.name):
                    paths.append(Path(entry.path))
        paths.sort(key=lambda path: (path == active, path.name), reverse=True)
        result["truncated"] |= len(paths) > max_files
        remaining = max_bytes
        records = []
        lines_read = 0
        for path in paths[:max_files]:
            if not remaining:
                result["truncated"] = True
                break
            try:
                permissions.check_file(path)
                with path.open("rb") as stream:
                    size = os.fstat(stream.fileno()).st_size
                    take = min(size, remaining)
                    stream.seek(size - take)
                    data = stream.read(take)
                remaining -= take
                if take < size:
                    result["truncated"] = True
                    data = data.partition(b"\n")[2]
                for line in data.splitlines():
                    lines_read += 1
                    if lines_read > 4096:
                        result["truncated"] = True
                        break
                    try:
                        event = json.loads(line)
                        if event.get("schema") == SCHEMA:
                            records.append(event)
                    except (ValueError, AttributeError):
                        result["unavailable"].append({"file": path.name, "reason": "invalid-record"})
            except Exception as error:
                result["unavailable"].append({"file": path.name, "reason": type(error).__name__})
            if lines_read > 4096:
                break
        records.sort(key=lambda event: str(event.get("ts", "")), reverse=True)
        result["truncated"] |= len(records) > limit
        result["records"] = records[:limit]
    except Exception as error:
        result["unavailable"].append({"reason": type(error).__name__})
    return result
