"""Content-free sessionStart hook journal; hooks never launch a receiver.

A hook writes its start marker first, before any product work, and replaces it
with a completion record when it returns.  The host runtime can stop a timed
out hook before it reports, so the next `begin()` turns any stale marker into
one `unfinished` observation.  A separate finite tool owner later drains the
journal through its own command route (consumer-telemetry-command §5).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .stage_projection import for_schema

_, _project = for_schema("hook.lifecycle.v1")
PENDING_LIMIT = 65536
DRAIN_LIMIT = 128
STALE_AFTER_MS = 120_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _stamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(
        timespec="milliseconds",
    ).replace("+00:00", "Z")


def _append(directory: Path, event: dict[str, Any]) -> bool:
    try:
        record = _project(event)
    except ValueError:
        return False
    line = (json.dumps(record, separators=(",", ":")) + "\n").encode("ascii")
    path = directory / "pending.jsonl"
    try:
        if path.stat().st_size + len(line) > PENDING_LIMIT:
            return False
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
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
        self.sequence = _next_sequence(self.directory, self.plugin, self.entry)
        marker = self.directory / f"{self.plugin}.{self.entry}.{uuid.uuid4().hex}.start"
        scratch = self.directory / f".{uuid.uuid4().hex}.tmp"
        scratch.write_text(json.dumps({
            "run_sequence": self.sequence, "started_ms": self.started_ms,
        }), "ascii")
        os.replace(scratch, marker)
        self.marker = marker
        for stale in self.directory.glob(f"{self.plugin}.{self.entry}.*.start"):
            if stale == marker:
                continue
            try:
                prior = json.loads(stale.read_text("ascii"))
                started = prior["started_ms"]
                sequence = prior["run_sequence"]
            except (OSError, ValueError, KeyError, TypeError):
                started, sequence = 0, None
            if type(started) is int and self.started_ms - started < stale_after_ms:
                continue
            try:
                stale.unlink()
            except FileNotFoundError:
                continue
            event: dict[str, Any] = {
                "kind": "hook", "plugin": self.plugin, "entry": self.entry,
                "outcome": "unfinished", "failure_category": "unfinished_prior_run",
            }
            if type(sequence) is int and sequence > 0:
                event["run_sequence"] = sequence
            if type(started) is int and started > 0:
                event["started_at"] = _stamp(started)
            _append(self.directory, event)

    def finish(
        self, *, outcome: str, failure_category: str = "none", exit_code: int | None = None,
    ) -> None:
        marker, self.marker = self.marker, None
        if marker is None:
            return
        event: dict[str, Any] = {
            "kind": "hook", "plugin": self.plugin, "entry": self.entry,
            "outcome": outcome, "failure_category": failure_category,
            "duration_ms": max(0, _now_ms() - self.started_ms),
            "run_sequence": self.sequence, "started_at": _stamp(self.started_ms),
        }
        if exit_code is not None:
            event["exit_code"] = exit_code
        try:
            _append(self.directory, event)
            marker.unlink()
        except OSError:
            pass


def drain(directory: Path, *, limit: int = DRAIN_LIMIT) -> list[dict[str, Any]]:
    """Claim and return pending observations; a claimed batch is never replayed."""
    source = Path(directory) / "pending.jsonl"
    claimed = Path(directory) / f".{uuid.uuid4().hex}.draining"
    try:
        os.replace(source, claimed)
    except OSError:
        return []
    try:
        with claimed.open("rb") as stream:
            data = stream.read(PENDING_LIMIT + 1)
    except OSError:
        return []
    finally:
        try:
            claimed.unlink()
        except OSError:
            pass
    events: list[dict[str, Any]] = []
    for line in data[:PENDING_LIMIT].splitlines()[:limit]:
        try:
            value = json.loads(line)
            events.append(_project(value))
        except (ValueError, TypeError, AttributeError):
            continue
    return events
