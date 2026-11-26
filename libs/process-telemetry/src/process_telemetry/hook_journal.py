"""Content-free sessionStart hook journal; hooks never launch a receiver.

A hook writes its start marker first, before any product work, and replaces it
with a completion record when it returns.  The host runtime can stop a timed
out hook before it reports, so the next `begin()` turns any stale marker into
one `unfinished` observation.  A separate finite tool owner later drains the
journal through its own command route (consumer-telemetry-command §5).

Capacity checks, appends and drains serialize on one advisory OS lock that the
kernel releases when its holder exits, so a killed hook cannot wedge it.  Every
wait is bounded; a writer that cannot take the lock drops its observation
rather than delay the hook.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .append import append_bytes
from .stage_projection import for_schema

_, _project = for_schema("hook.lifecycle.v1")
PENDING_LIMIT = 65536
DRAIN_LIMIT = 128
STALE_AFTER_MS = 120_000
LOCK_WAIT_S = 0.25
# Directory entries and markers one start may examine; the rest wait for later starts.
SCAN_LIMIT = 256
RECOVER_LIMIT = 8
MARKER_LIMIT = 64
RECLAIM_LIMIT = 16
# One aggregate wall budget for reading stale markers during a start.
RECOVERY_BUDGET_S = 0.05
_SCRATCH_SUFFIXES = (".tmp", ".seq", ".pending", ".recovering")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def _try_lock(fd: int) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def _journal_lock(directory: Path, *, wait_s: float | None = None) -> Iterator[bool]:
    """Yield whether this process holds the journal lock within the bounded wait."""
    fd = os.open(directory / ".journal.lock", os.O_RDWR | os.O_CREAT, 0o600)
    held = False
    try:
        deadline = time.monotonic() + (LOCK_WAIT_S if wait_s is None else wait_s)
        while not (held := _try_lock(fd)) and time.monotonic() < deadline:
            time.sleep(0.005)
        yield held
    finally:
        try:
            if held:
                _unlock(fd)
        finally:
            os.close(fd)


def _stamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(
        timespec="milliseconds",
    ).replace("+00:00", "Z")


def _append(directory: Path, event: dict[str, Any]) -> bool:
    return _append_many(directory, [event])


def _append_many(directory: Path, events: list[dict[str, Any]]) -> bool:
    """Append every admitted record under one lock acquisition, or none."""
    try:
        data = b"".join(
            (json.dumps(_project(event), separators=(",", ":")) + "\n").encode("ascii")
            for event in events
        )
    except ValueError:
        return False
    path = directory / "pending.jsonl"
    with _journal_lock(directory) as held:
        if not held:
            return False
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size + len(data) > PENDING_LIMIT:
            return False
        append_bytes(path, data, mode=0o600)
    return True

def _next_sequence(directory: Path, plugin: str, entry: str) -> int:
    path = directory / f"{plugin}.{entry}.seq"
    try:
        value = int(path.read_text("ascii").strip() or "0")
    except (OSError, ValueError):
        value = 0
    value = value + 1 if 0 <= value < 2**53 - 1 else 1
    scratch = directory / f".{uuid.uuid4().hex}.seq"
    scratch.write_text(str(value), "ascii")
    os.replace(scratch, path)
    return value


class HookRun:
    """One hook entry's journal custody; every method fails open."""

    def __init__(self, directory: Path, plugin: str, entry: str) -> None:
        self.directory = Path(directory)
        self.plugin = plugin
        self.entry = entry
        self.marker: Path | None = None
        self.sequence = 0
        self.started_ms = 0
        self.started_mono_ms = 0

    @classmethod
    def begin(
        cls, directory: Path, plugin: str, entry: str, *, stale_after_ms: int = STALE_AFTER_MS,
    ) -> HookRun:
        run = cls(directory, plugin, entry)
        try:
            run._begin(stale_after_ms)
        except (OSError, ValueError):
            run.marker = None
        return run

    def _begin(self, stale_after_ms: int) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.started_ms = _now_ms()
        self.started_mono_ms = _monotonic_ms()
        markers, scratch, crowded = self._scan()
        _reclaim(scratch, self.started_ms, stale_after_ms)
        recovered = self._recover(markers, stale_after_ms)
        if crowded or len(markers) - recovered >= MARKER_LIMIT:
            # Storage is bounded: an unobserved run is preferable to an unbounded directory.
            raise OSError("hook journal marker capacity reached")
        with _journal_lock(self.directory) as held:
            if not held:
                raise OSError("hook journal is busy")
            self.sequence = _next_sequence(self.directory, self.plugin, self.entry)
        marker = self.directory / f"{self.plugin}.{self.entry}.{uuid.uuid4().hex}.start"
        scratch_path = self.directory / f".{uuid.uuid4().hex}.tmp"
        scratch_path.write_text(json.dumps({
            "run_sequence": self.sequence, "started_ms": self.started_ms,
        }), "ascii")
        os.replace(scratch_path, marker)
        self.marker = marker

    def _scan(self) -> tuple[list[Path], list[Path], bool]:
        """One bounded enumeration: this entry's markers and abandoned scratch files."""
        prefix = f"{self.plugin}.{self.entry}."
        markers: list[Path] = []
        scratch: list[Path] = []
        with os.scandir(self.directory) as entries:
            for examined, item in enumerate(entries, 1):
                if examined > SCAN_LIMIT:
                    return markers, scratch, True
                name = item.name
                if name.startswith(prefix) and name.endswith(".start"):
                    markers.append(self.directory / name)
                elif name.startswith(".") and name.endswith(_SCRATCH_SUFFIXES):
                    scratch.append(self.directory / name)
        return markers, scratch, False

    def _recover(self, markers: list[Path], stale_after_ms: int) -> int:
        """Report up to RECOVER_LIMIT stale markers within one time budget; return the count."""
        deadline = time.monotonic() + RECOVERY_BUDGET_S
        claims: list[tuple[Path, Path]] = []
        events: list[dict[str, Any]] = []
        for stale in markers:
            if len(claims) >= RECOVER_LIMIT or time.monotonic() > deadline:
                break
            try:
                prior = json.loads(stale.read_text("ascii"))
                started = prior["started_ms"]
                sequence = prior["run_sequence"]
            except FileNotFoundError:
                continue
            except (OSError, ValueError, KeyError, TypeError):
                # A marker being replaced or read concurrently is judged by its age alone.
                try:
                    started, sequence = stale.stat().st_mtime_ns // 1_000_000, None
                except OSError:
                    continue
            if type(started) is not int or self.started_ms - started < stale_after_ms:
                continue
            # Renaming claims the marker: one concurrent start or the run's own
            # finish() wins, so each run is reported exactly once.
            claimed = self.directory / f".{uuid.uuid4().hex}.recovering"
            try:
                os.replace(stale, claimed)
            except OSError:
                continue
            event: dict[str, Any] = {
                "kind": "hook", "plugin": self.plugin, "entry": self.entry,
                "outcome": "unfinished", "failure_category": "unfinished_prior_run",
            }
            if type(sequence) is int and sequence > 0:
                event["run_sequence"] = sequence
            if started > 0:
                event["started_at"] = _stamp(started)
            claims.append((stale, claimed))
            events.append(event)
        if not claims:
            return 0
        if not _append_many(self.directory, events):
            # The journal is full or busy: keep the markers for a later start.
            for stale, claimed in claims:
                with contextlib.suppress(OSError):
                    os.replace(claimed, stale)
            return 0
        for _, claimed in claims:
            with contextlib.suppress(OSError):
                claimed.unlink()
        return len(claims)

    def finish(
        self, *, outcome: str, failure_category: str = "none", exit_code: int | None = None,
    ) -> None:
        marker, self.marker = self.marker, None
        if marker is None:
            return
        claimed = self.directory / f".{uuid.uuid4().hex}.recovering"
        try:
            os.replace(marker, claimed)
        except OSError:
            # A later start already reported this run as unfinished.
            return
        event: dict[str, Any] = {
            "kind": "hook", "plugin": self.plugin, "entry": self.entry,
            "outcome": outcome, "failure_category": failure_category,
            "duration_ms": max(0, _monotonic_ms() - self.started_mono_ms),
            "run_sequence": self.sequence, "started_at": _stamp(self.started_ms),
        }
        if exit_code is not None:
            event["exit_code"] = exit_code
        try:
            _append(self.directory, event)
        except OSError:
            pass
        with contextlib.suppress(OSError):
            claimed.unlink()


def _reclaim(scratch: list[Path], now_ms: int, stale_after_ms: int) -> None:
    """Remove a bounded number of abandoned scratch files left by interrupted writers."""
    for path in scratch[:RECLAIM_LIMIT]:
        try:
            if now_ms - path.stat().st_mtime_ns // 1_000_000 >= max(stale_after_ms, 1):
                path.unlink()
        except OSError:
            continue

def drain(directory: Path, *, limit: int = DRAIN_LIMIT) -> list[dict[str, Any]]:
    """Remove and return up to `limit` observations; the rest stay pending.

    Returned records leave the journal before they are handed to the caller,
    so a drained record is never replayed.
    """
    directory = Path(directory)
    source = directory / "pending.jsonl"
    try:
        with _journal_lock(directory) as held:
            if not held:
                return []
            try:
                with source.open("rb") as stream:
                    data = stream.read(PENDING_LIMIT + 1)
            except FileNotFoundError:
                return []
            lines = data[:PENDING_LIMIT].splitlines(keepends=True)
            if lines and not lines[-1].endswith(b"\n"):
                lines.pop()
            taken, rest = lines[:limit], b"".join(lines[limit:])
            if rest:
                scratch = directory / f".{uuid.uuid4().hex}.pending"
                scratch.write_bytes(rest)
                os.replace(scratch, source)
            else:
                source.unlink()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in taken:
        try:
            events.append(_project(json.loads(line)))
        except (ValueError, TypeError, AttributeError):
            continue
    return events
