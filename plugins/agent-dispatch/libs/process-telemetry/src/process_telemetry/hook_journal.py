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

Occupancy is bounded for the whole shared journal, not per entry: markers are
capped in aggregate, sequence files exist only for the admitted plugin and
entry vocabulary, and every scratch file is written under the lock, so any
scratch file a lock holder finds was abandoned.  The sum stays inside the one
complete-scan limit, so a start and a drain always examine every marker.
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
from .stage_projection import HOOK_ENTRIES, HOOK_PLUGINS, for_schema

_, _project = for_schema("hook.lifecycle.v1")
PENDING_LIMIT = 65536
DRAIN_LIMIT = 128
STALE_AFTER_MS = 120_000
LOCK_WAIT_S = 0.25
# Directory entries one start or drain may examine.
SCAN_LIMIT = 256
RECOVER_LIMIT = 8
# Markers across every plugin and entry sharing the journal.
MARKER_LIMIT = 64
RECLAIM_LIMIT = 16
# The lock file and the pending journal.
_FIXED_ENTRIES = 2
# One aggregate wall budget for reading stale markers during a start.
RECOVERY_BUDGET_S = 0.05
_SCRATCH_SUFFIXES = (".tmp", ".seq", ".pending", ".recovering")


def _occupancy_bound() -> int:
    """Worst-case entries: markers, sequence files, fixed files and the scratch
    left by holders interrupted between two reclaiming starts."""
    sequences = len(HOOK_PLUGINS) * len(HOOK_ENTRIES)
    return MARKER_LIMIT + sequences + _FIXED_ENTRIES + RECLAIM_LIMIT


assert _occupancy_bound() <= SCAN_LIMIT, "hook journal occupancy must fit one complete scan"


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
    if size and not _ends_with_newline(path, size):
        # A torn tail from an interrupted writer must stay its own (invalid)
        # line rather than absorb this healthy record.
        data = b"\n" + data
    if size + len(data) > PENDING_LIMIT:
        return False
    append_bytes(path, data, mode=0o600)
    return True


def _ends_with_newline(path: Path, size: int) -> bool:
    with path.open("rb") as stream:
        stream.seek(size - 1)
        return stream.read(1) == b"\n"


def _append(directory: Path, event: dict[str, Any]) -> bool:
    with _journal_lock(directory) as held:
        return held and _append_held(directory, [event])


def _key(event: dict[str, Any]) -> tuple[Any, ...] | None:
    """A completed run's identity, from fields its record already carries."""
    if "run_sequence" not in event or "started_at" not in event:
        return None
    return (event.get("plugin"), event.get("entry"), event["run_sequence"], event["started_at"])


def _discard_torn_tail(directory: Path) -> bytes:
    """Drop an unterminated final line and return the complete lines; the lock is held.

    A writer stopped mid-append can leave a whole JSON object without its
    newline.  `drain` never reports such a line, so it must not count as
    pending either: its committed marker remains the record of that run.
    """
    path = directory / "pending.jsonl"
    try:
        with path.open("rb") as stream:
            data = stream.read(PENDING_LIMIT + 1)
    except FileNotFoundError:
        return b""
    if not data or data.endswith(b"\n"):
        return data
    kept = data[: data.rfind(b"\n") + 1]
    if kept:
        scratch = directory / f".{uuid.uuid4().hex}.pending"
        scratch.write_bytes(kept)
        os.replace(scratch, path)
    else:
        path.unlink()
    return kept


def _pending_keys(directory: Path) -> set[tuple[Any, ...]]:
    data = _discard_torn_tail(directory)
    keys: set[tuple[Any, ...]] = set()
    for line in data.splitlines():
        try:
            key = _key(json.loads(line))
        except (ValueError, TypeError, AttributeError):
            continue
        if key is not None:
            keys.add(key)
    return keys


class MarkerUnreadable(OSError):
    """A marker exists but its contents could not be read."""


def _read_marker(path: Path) -> dict[str, Any] | None:
    try:
        data = path.read_bytes()[:4096]
    except FileNotFoundError:
        raise
    except OSError as error:
        # An unreadable marker may still carry a committed record, so callers
        # must not mistake it for one without a record.
        raise MarkerUnreadable from error
    try:
        value = json.loads(data)
    except ValueError:
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
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # Retained: its committed record keeps a later report deduplicated,
        # but it still occupies capacity and must not count as freed.
        return False
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
        if self.plugin not in HOOK_PLUGINS or self.entry not in HOOK_ENTRIES:
            # Unadmitted names would add files outside the bounded vocabulary.
            raise ValueError("hook plugin or entry is not admitted")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.started_ms = _now_ms()
        self.started_mono_ms = _monotonic_ms()
        # Scanning, recovery, the capacity check and publishing the new marker
        # share one lock acquisition, so simultaneous starts cannot overshoot it.
        with _journal_lock(self.directory) as held:
            if not held:
                raise OSError("hook journal is busy")
            markers, scratch, crowded = self._scan()
            _reclaim(scratch)
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
        """One bounded enumeration: every entry's markers and abandoned scratch files.

        Markers that do not name an admitted plugin and entry are treated as
        scratch, so foreign names cannot consume the shared marker capacity.
        """
        own = f"{self.plugin}.{self.entry}."
        markers: list[Path] = []
        others: list[Path] = []
        scratch: list[Path] = []
        with os.scandir(self.directory) as entries:
            for examined, item in enumerate(entries, 1):
                if examined > SCAN_LIMIT:
                    return markers + others, scratch, True
                name = item.name
                if name.startswith(".") and name.endswith(_SCRATCH_SUFFIXES):
                    scratch.append(self.directory / name)
                elif name.endswith(".start"):
                    if _marker_owner(name) is None:
                        scratch.append(self.directory / name)
                    elif name.startswith(own):
                        markers.append(self.directory / name)
                    else:
                        others.append(self.directory / name)
        # This entry's own markers first; then other entries' leftovers.
        return markers + others, scratch, False

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
            owner = _marker_owner(marker.name)
            if owner is None:
                continue
            plugin, entry = owner
            # Another entry's hook may run under a longer budget than this one.
            stale_ms = stale_after_ms if owner == (self.plugin, self.entry) else max(
                stale_after_ms, STALE_AFTER_MS,
            )
            record = state.get("record")
            if type(record) is dict:
                # A holder stopped after committing its outcome; report that outcome.
                check = True
            else:
                started = state.get("started_ms")
                if type(started) is not int or self.started_ms - started < stale_ms:
                    continue
                record = {
                    "kind": "hook", "plugin": plugin, "entry": entry,
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


def _marker_owner(name: str) -> tuple[str, str] | None:
    """The admitted (plugin, entry) a marker file name carries, if any."""
    parts = name.split(".")
    if len(parts) != 4 or parts[3] != "start":
        return None
    plugin, entry = parts[0], parts[1]
    if plugin not in HOOK_PLUGINS or entry not in HOOK_ENTRIES:
        return None
    return plugin, entry


def _reclaim(scratch: list[Path]) -> None:
    """Remove a bounded number of abandoned scratch files; the lock is held.

    Every scratch file is written and renamed while its writer holds the
    journal lock, so one found by the current holder cannot be in flight.
    """
    for path in scratch[:RECLAIM_LIMIT]:
        with contextlib.suppress(OSError):
            path.unlink()


def _settle(directory: Path, keys: set[tuple[Any, ...]]) -> set[tuple[Any, ...]]:
    """Remove committed markers for `keys`; return the keys not confirmed settled.

    The lock is held.  A key stays unconfirmed when its marker could not be
    removed, or when the bounded scan ended before every entry was examined.
    The same scan reclaims abandoned scratch files.
    """
    unsettled: set[tuple[Any, ...]] = set()
    scratch: list[Path] = []
    with os.scandir(directory) as entries:
        for examined, item in enumerate(entries, 1):
            if examined > SCAN_LIMIT:
                _reclaim(scratch)
                return set(keys)
            name = item.name
            if name.startswith(".") and name.endswith(_SCRATCH_SUFFIXES):
                scratch.append(directory / name)
                continue
            if not keys or not name.endswith(".start"):
                continue
            path = directory / item.name
            try:
                state = _read_marker(path)
            except FileNotFoundError:
                continue
            except OSError:
                # It may carry one of `keys`; leave every key pending.
                _reclaim(scratch)
                return set(keys)
            record = state.get("record") if state is not None else None
            key = _key(record) if type(record) is dict else None
            if key is None or key not in keys:
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                unsettled.add(key)
    _reclaim(scratch)
    return unsettled


def drain(directory: Path, *, limit: int = DRAIN_LIMIT) -> list[dict[str, Any]]:
    """Remove and return up to `limit` observations; the rest stay pending.

    A record leaves the journal only after any committed marker that carries
    it is confirmed removed, so neither a drained record nor its marker can be
    reported again.  A record whose marker removal is unconfirmed stays
    pending, behind the other records, without being returned.
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
            taken, rest = lines[:limit], lines[limit:]
            candidates: list[tuple[bytes, dict[str, Any]]] = []
            for line in taken:
                try:
                    candidates.append((line, _project(json.loads(line))))
                except (ValueError, TypeError, AttributeError):
                    continue
            unsettled = _settle(
                directory, {key for _, event in candidates if (key := _key(event)) is not None},
            )
            retained = [line for line, event in candidates if _key(event) in unsettled]
            remaining = b"".join(rest + retained)
            if remaining:
                scratch = directory / f".{uuid.uuid4().hex}.pending"
                scratch.write_bytes(remaining)
                os.replace(scratch, source)
            else:
                source.unlink()
            events = [event for _, event in candidates if _key(event) not in unsettled]
    except OSError:
        return []
    return events
