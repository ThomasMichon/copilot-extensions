"""Optional owner-injected JSONL retention; no root, configuration or lock discovery."""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .append import (
    STRICT_RECORD_BYTES, append_bytes, append_bytes_strict, strict_write_capability,
)

RETENTION = timedelta(days=30)
MAINTENANCE_SECONDS = 60.0
MAX_BYTES = 5_000_000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("diagnostic retention requires an aware clock")
    return value.astimezone(timezone.utc)


def _invalid_json_constant(value: str) -> None:
    raise ValueError(f"strict diagnostic JSON forbids {value}")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("strict diagnostic JSON number is not finite")
    return parsed


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("strict diagnostic JSON contains duplicate fields")
        result[key] = value
    return result


def _same_json_fields(left: object, right: object) -> bool:
    if (
        type(left) is not type(right)
        or type(left) not in (type(None), bool, int, float, str, dict, list)
    ):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        if any(type(key) is not str for key in right) or left.keys() != right.keys():
            return False
        return all(_same_json_fields(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_json_fields(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, float) and isinstance(right, float):
        return math.isfinite(left) and math.isfinite(right) and left == right
    return left == right


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

    def __init__(
        self, component: str, directory: Path, *, service: str, custody,
        attribution: dict | None = None, timestamp_decoder=None,
        clock=utc_now, max_bytes=MAX_BYTES, create_parent: bool = True,
        initial_maintenance: bool = True, encoder=None, discovery_hint: str | None = None,
    ):
        super().__init__()
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", component):
            raise ValueError("unknown diagnostic component")
        self.component = component
        self.service = service
        self.discovery_hint = discovery_hint or "inspect the owner's diagnostic inventory"
        self.directory = directory.resolve()
        self.path = self.directory / f"{component}.jsonl"
        self.baseFilename = str(self.path)
        self.clock = clock
        self.max_bytes = max_bytes
        self.last_error: str | None = None
        self._warned = False
        self._last_maintenance: datetime | None = None
        self._last_monotonic = 0.0
        self._expiry: dict[Path, tuple[tuple, datetime | None, bool]] = {}
        self._custody_factory = custody
        self._create_parent = create_parent
        self.attribution = dict(attribution or {})
        self.timestamp_decoder = timestamp_decoder or iso_timestamp
        self.encoder = encoder or (
            lambda event: (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        )
        if initial_maintenance:
            self.maintain(force=True)

    @contextmanager
    def _custody(self, *, create_parent: bool | None = None):
        if create_parent is None:
            create_parent = self._create_parent
        if create_parent:
            self.directory.mkdir(parents=True, exist_ok=True)
        with self._custody_factory(self.directory / f".{self.component}.lock"):
            yield

    def _degraded(self, error: Exception) -> None:
        self.last_error = type(error).__name__
        if not self._warned:
            self._warned = True
            try:
                print(
                    f"{self.service}: {self.component} diagnostics degraded "
                    f"({self.last_error}); {self.discovery_hint}",
                    file=sys.stderr,
                )
            except Exception:
                pass

    def _files(self) -> list[Path]:
        return _diagnostic_files(self.directory, self.component)

    def _stamp(self, line: bytes) -> datetime | None:
        try:
            return self._event_stamp(json.loads(line))
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            return None

    def _event_stamp(self, event: dict) -> datetime | None:
        try:
            return _utc(self.timestamp_decoder(event))
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, OSError):
            return None

    @staticmethod
    def _signature(path: Path) -> tuple:
        info = path.stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def _scan(self, path: Path) -> tuple[datetime | None, bool]:
        earliest = None
        malformed = False
        with path.open("rb") as source:
            for line in source:
                stamp = self._stamp(line)
                if stamp is None:
                    malformed = True
                elif earliest is None or stamp < earliest:
                    earliest = stamp
        return earliest, malformed

    def _abandoned_prunes(self) -> None:
        pattern = re.compile(
            rf"\.{self.component}(?:\.\d{{8}}T\d{{12}}Z-[0-9a-f]{{32}})?"
            r"\.jsonl\.prune-[0-9a-f]{32}"
        )
        for path in self.directory.glob(f".{self.component}*.prune-*"):
            if pattern.fullmatch(path.name):
                self._plain_file(path)
                # The component lock proves no participating pruner is active.
                path.unlink()

    def _prune(self, path: Path, cutoff: datetime) -> None:
        # Compare records, not mtime or segment names: the frontier segment may
        # contain records on both sides of the exact elapsed-age cutoff.
        self._plain_file(path)
        signature = self._signature(path)
        cached = self._expiry.get(path)
        if cached is not None and cached[0] == signature:
            _, earliest, malformed = cached
        else:
            earliest, malformed = self._scan(path)
            self._expiry[path] = signature, earliest, malformed
        if malformed:
            self._degraded(ValueError("unparseable diagnostic timestamp"))
        if earliest is None or earliest >= cutoff:
            return
        temporary = path.with_name(f".{path.name}.prune-{uuid.uuid4().hex}")
        retained = False
        retained_earliest = None
        try:
            with path.open("rb") as source, temporary.open("xb") as target:
                for line in source:
                    stamp = self._stamp(line)
                    if stamp is None or stamp >= cutoff:
                        target.write(line)
                        retained = True
                        if stamp is not None and (
                            retained_earliest is None or stamp < retained_earliest
                        ):
                            retained_earliest = stamp
            if retained or path == self.path:
                os.replace(temporary, path)
                self._expiry[path] = self._signature(path), retained_earliest, malformed
            else:
                path.unlink()
                self._expiry.pop(path, None)
        finally:
            temporary.unlink(missing_ok=True)

    def maintain(self, *, force: bool = False) -> None:
        """Prune on startup/owner maintenance, even without new log messages."""
        try:
            self._maintain(_utc(self.clock()), force=force)
        except Exception as error:
            self._degraded(error)

    def _maintain(self, now: datetime, *, force: bool = False) -> None:
        monotonic = time.monotonic()
        if (
            not force and self._last_maintenance is not None
            and now >= self._last_maintenance
            and now - self._last_maintenance < timedelta(seconds=MAINTENANCE_SECONDS)
            and monotonic - self._last_monotonic < MAINTENANCE_SECONDS
        ):
            return
        # Unavailable storage must not turn an idle worker tick into an I/O retry.
        self._last_maintenance = now
        self._last_monotonic = monotonic
        self.last_error = None
        with self._custody():
            self._abandoned_prunes()
            paths = self._files()
            live = set(paths)
            self._expiry = {path: cached for path, cached in self._expiry.items() if path in live}
        for path in paths:
            try:
                # Yield custody between segments, not after the whole history.
                with self._custody():
                    self._prune(path, now - RETENTION)
            except FileNotFoundError:
                self._expiry.pop(path, None)
            except Exception as error:
                self._degraded(error)
        if self.last_error is None:
            self._warned = False

    def _rollover(self, now: datetime) -> None:
        try:
            self._plain_file(self.path)
            size = self.path.stat().st_size
            if not size:
                return
            with self.path.open("rb") as source:
                first = self._stamp(source.readline())
                source.seek(-1, os.SEEK_END)
                terminated = source.read(1) == b"\n"
            first_day = first.date() if first is not None else None
        except FileNotFoundError:
            return
        except (ValueError, KeyError, TypeError):
            first_day = None
            terminated = False
            size = self.path.stat().st_size
        if size and (size >= self.max_bytes or first_day != now.date() or not terminated):
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
            event = self._record_fields(record)
            event["message"] = record.getMessage()
            for field in ("operation_id", "correlation_id", "generation"):
                if field in record.__dict__:
                    event[field] = record.__dict__[field]
            if record.exc_info:
                event["exception"] = logging.Formatter().formatException(record.exc_info)
            self.write_event(event)
        except Exception as error:
            self._degraded(error)

    def _record_fields(self, record: logging.LogRecord) -> dict:
        event = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "service": self.service, "component": self.component,
            "logger": record.name, "level": record.levelname, "pid": record.process,
        }
        event.update({
            key: value if record.process == os.getpid() else None
            for key, value in self.attribution.items()
        })
        return event

    def write_event(self, event: dict) -> None:
        """Write caller-shaped fields verbatim; maintenance is an owner operation."""
        try:
            now = _utc(self.clock())
            stamp = self._event_stamp(event)
            if stamp is not None and stamp < now - RETENTION:
                return
            if stamp is None:
                self._degraded(ValueError("unparseable diagnostic timestamp"))
            data = self.encoder(event)
            with self._custody():
                self._rollover(now)
                append_bytes(self.path, data)
        except Exception as error:
            self._degraded(error)

    def write_event_strict(self, event: dict) -> None:
        """Synchronously persist an explicit pre-effect record or raise.

        No buffering, maintenance, silent age skip or automatic parent creation.
        Caller-owned persistent storage and custody are prerequisites, not facts
        inferred from this method's successful return.
        """
        try:
            if strict_write_capability() is None:
                raise NotImplementedError("strict diagnostic sync is unavailable on this platform")
            if type(event) is not dict or not _same_json_fields(event, event):
                raise ValueError("strict diagnostics require finite primitive JSON fields")
            baseline = copy.deepcopy(event)
            now = _utc(self.clock())
            stamp = self._event_stamp(copy.deepcopy(baseline))
            if stamp is None or stamp < now - RETENTION:
                raise ValueError("strict diagnostic timestamp is invalid or expired")
            data = self.encoder(copy.deepcopy(baseline))
            if (
                type(data) is not bytes or not 0 < len(data) <= STRICT_RECORD_BYTES
                or not data.endswith(b"\n") or b"\n" in data[:-1] or b"\r" in data
            ):
                raise ValueError("strict diagnostics require one bounded UTF-8 JSONL record")
            decoded = json.loads(
                data.decode("utf-8"), parse_constant=_invalid_json_constant,
                parse_float=_finite_json_float, object_pairs_hook=_unique_json_object,
            )
            if type(decoded) is not dict or not _same_json_fields(decoded, baseline):
                raise ValueError("strict diagnostic encoder changed the record fields")
            if not self.directory.is_dir():
                raise FileNotFoundError("strict diagnostic directory must already exist")
            with self._custody(create_parent=False):
                self._rollover(now)
                append_bytes_strict(self.path, data)
        except Exception as error:
            self._degraded(error)
            raise


def iso_timestamp(event: dict) -> datetime:
    return datetime.fromisoformat(event["ts"])


def location(directory: Path, component: str, owner: str) -> dict:
    """Discover an owner's explicit namespace without creating it or reading logs."""
    active = directory / f"{component}.jsonl"
    return {
        "owner": owner, "active": str(active), "exists": active.is_file(),
        "archives": [str(p) for p in _diagnostic_files(directory, component) if p != active],
        "retention": "30-day-record-age",
        "maintenance": "startup and existing daemon maintenance (not emission)",
        "write_health": "unknown-from-discovery",
        "unparseable_timestamps": "retained with a degraded diagnostic; never age-guessed",
    }
