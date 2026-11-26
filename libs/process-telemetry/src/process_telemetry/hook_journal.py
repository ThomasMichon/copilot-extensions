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


def _encode(events: list[dict[str, Any]]) -> bytes | None:
    try:
        return b"".join(
            (json.dumps(_project(event), separators=(",", ":")) + "\n").encode("ascii")
            for event in events
        )
    except ValueError:
        return None


def _append_held(directory: Path, events: list[dict[str, Any]]) -> bool:
    """Append every admitted record, or none; the caller holds the journal lock."""
    data = _encode(events)
    if data is None:
        return False
    path = directory / "pending.jsonl"
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        size = 0
    if size + len(data) > PENDING_LIMIT:
        return False
    append_bytes(path, data, mode=0o600)
    return True


def _append(directory: Path, event: dict[str, Any]) -> bool:
    with _journal_lock(directory) as held:
        return held and _append_held(directory, [event])


def _key(event: dict[str, Any]) -> tuple[Any, ...] | None:
    """A completed run's identity, from fields its record already carries."""
    if "run_sequence" not in event or "started_at" not in event:
        return None
    return (event.get("plugin"), event.get("entry"), event["run_sequence"], event["started_at"])


def _pending_keys(directory: Path) -> set[tuple[Any, ...]]:
    try:
        with (directory / "pending.jsonl").open("rb") as stream:
            data = stream.read(PENDING_LIMIT)
    except FileNotFoundError:
        return set()
    keys: set[tuple[Any, ...]] = set()
    for line in data.splitlines():
        try:
            key = _key(json.loads(line))
        except (ValueError, TypeError, AttributeError):
            continue
        if key is not None:
            keys.add(key)
    return keys


def _read_marker(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_bytes()[:4096])
    except FileNotFoundError:
        raise
    except (OSError, ValueError):
        return None
    return value if type(value) is dict else None


def _write_atomic(directory: Path, target: Path, value: dict[str, Any]) -> None:
    scratch = directory / f".{uuid.uuid4().hex}.tmp"
    scratch.write_text(json.dumps(value, separators=(",", ":")), "ascii")
    os.replace(scratch, target)


def _finalize(
    directory: Path, marker: Path, state: dict[str, Any], record: dict[str, Any], *, check: bool,
) -> bool:
    """Commit one run's record into its marker, then report it; the lock is held.

    Writing the record into the marker first is the commit point: a holder
    stopped before the append leaves a marker that still carries its real
    outcome, and one stopped after the append leaves a marker whose key the
    pending journal (or a later drain) already accounts for.  Each run is
    therefore reported exactly once.
    """
    if _encode([record]) is None:
        return False
    if state.get("record") != record:
        _write_atomic(directory, marker, {**state, "record": record})
    key = _key(record)
    if not (check and key is not None and key in _pending_keys(directory)):
        if not _append_held(directory, [record]):
            # Full: the committed marker is reported by a later start.
            return False
    with contextlib.suppress(OSError):
        marker.unlink()
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
        # Scanning, recovery, the capacity check and publishing the new marker
        # share one lock acquisition, so simultaneous starts cannot overshoot it.
        with _journal_lock(self.directory) as held:
            if not held:
                raise OSError("hook journal is busy")
            markers, scratch, crowded = self._scan()
            _reclaim(scratch, self.started_ms, stale_after_ms)
            recovered = self._recover(markers, stale_after_ms)
            if crowded or len(markers) - recovered >= MARKER_LIMIT:
                # Storage is bounded: an unobserved run is preferable to an unbounded directory.
                raise OSError("hook journal marker capacity reached")
            self.sequence = _next_sequence(self.directory, self.plugin, self.entry)
            marker = self.directory / f"{self.plugin}.{self.entry}.{uuid.uuid4().hex}.start"
            _write_atomic(self.directory, marker, {
                "run_sequence": self.sequence, "started_ms": self.started_ms,
            })
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
        """Report up to RECOVER_LIMIT markers within one time budget; the lock is held."""
        deadline = time.monotonic() + RECOVERY_BUDGET_S
        recovered = 0
        for marker in markers:
            if recovered >= RECOVER_LIMIT or time.monotonic() > deadline:
                break
            try:
                state = _read_marker(marker)
                if state is None:
                    state = {"started_ms": marker.stat().st_mtime_ns // 1_000_000}
            except OSError:
                continue
            record = state.get("record")
            if type(record) is dict:
                # A holder stopped after committing its outcome; report that outcome.
                check = True
            else:
                started = state.get("started_ms")
                if type(started) is not int or self.started_ms - started < stale_after_ms:
                    continue
                record = {
                    "kind": "hook", "plugin": self.plugin, "entry": self.entry,
                    "outcome": "unfinished", "failure_category": "unfinished_prior_run",
                }
                sequence = state.get("run_sequence")
                if type(sequence) is int and sequence > 0:
                    record["run_sequence"] = sequence
                if started > 0:
                    record["started_at"] = _stamp(started)
                check = False
            if not _finalize(self.directory, marker, state, record, check=check):
                break
            recovered += 1
        return recovered

    def finish(
        self, *, outcome: str, failure_category: str = "none", exit_code: int | None = None,
    ) -> None:
        marker, self.marker = self.marker, None
        if marker is None:
            return
        record: dict[str, Any] = {
            "kind": "hook", "plugin": self.plugin, "entry": self.entry,
            "outcome": outcome, "failure_category": failure_category,
            "duration_ms": max(0, _monotonic_ms() - self.started_mono_ms),
            "run_sequence": self.sequence, "started_at": _stamp(self.started_ms),
        }
        if exit_code is not None:
            record["exit_code"] = exit_code
        try:
            with _journal_lock(self.directory) as held:
                if not held:
                    # Drop the observation rather than delay the hook; recovery
                    # serializes on the lock, so this cannot double-report.
                    with contextlib.suppress(OSError):
                        marker.unlink()
                    return
                try:
                    state = _read_marker(marker)
                except FileNotFoundError:
                    return  # A later start already reported this run as unfinished.
                if state is None or type(state.get("record")) is dict:
                    return
                _finalize(self.directory, marker, state, record, check=False)
        except OSError:
            pass


def _reclaim(scratch: list[Path], now_ms: int, stale_after_ms: int) -> None:
    """Remove a bounded number of abandoned scratch files left by interrupted writers."""
    for path in scratch[:RECLAIM_LIMIT]:
        try:
            if now_ms - path.stat().st_mtime_ns // 1_000_000 >= max(stale_after_ms, 1):
                path.unlink()
        except OSError:
            continue


def _settle(directory: Path, keys: set[tuple[Any, ...]]) -> None:
    """Remove committed markers whose records this drain handed off; the lock is held."""
    if not keys:
        return
    with os.scandir(directory) as entries:
        for examined, item in enumerate(entries, 1):
            if examined > SCAN_LIMIT:
                return
            if not item.name.endswith(".start"):
                continue
            path = directory / item.name
            try:
                state = _read_marker(path)
            except OSError:
                continue
            record = state.get("record") if state is not None else None
            if type(record) is dict and _key(record) in keys:
                with contextlib.suppress(OSError):
                    path.unlink()


def drain(directory: Path, *, limit: int = DRAIN_LIMIT) -> list[dict[str, Any]]:
    """Remove and return up to `limit` observations; the rest stay pending.

    Returned records leave the journal before they are handed to the caller,
    so a drained record is never replayed.
    """
    directory = Path(directory)
    source = directory / "pending.jsonl"
    events: list[dict[str, Any]] = []
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
            for line in taken:
                try:
                    events.append(_project(json.loads(line)))
                except (ValueError, TypeError, AttributeError):
                    continue
            _settle(directory, {key for event in events if (key := _key(event)) is not None})
    except OSError:
        return events
    return events