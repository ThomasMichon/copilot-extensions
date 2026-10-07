"""Bounded, structured dial log: the connection attempts to a target, one line each.

Observe-only telemetry for diagnosing dial storms and relay drops without
cross-layer SSH forensics. Each layer that opens a connection -- the
``gh codespace ssh --config`` fetch (which cold-starts a stopped CodeSpace), a
ControlMaster start, a direct-mode exec (each one is a fresh connection), and a
health-check reconnect -- records one JSON line per attempt (dedicated port-forward
and relay-channel ssh processes, ``forward.py`` / ``relay_channel.py``, are not
logged yet):
``{at, target, kind, outcome, elapsed_s, attempt, reason, account, stderr}``.

- **Bounded.** One file per target under :func:`log_dir`, trimmed to its newest
  :data:`KEEP_LINES` lines whenever it passes :data:`MAX_LINES` lines or
  :data:`MAX_BYTES`, by an atomic replace under the same per-file lock writers
  take, so concurrent writers never interleave a line or lose the trim. The line
  count is kept beside the log (``.count``), so an append never rescans it.
- **Redacted.** Only a short stderr tail is kept, with token-like strings and
  authorization values replaced; the account is recorded as ``pinned`` or
  ``ambient`` (anything else a caller passes is stored as ``other``), never anything
  derived from a credential. No command lines.
- **Outcomes.** ``ok``; ``transient`` -- what the retry logic treats as a transport
  failure (ssh's exit 255, or a connection-reset message): ssh reports a remote
  command's own exit 255 the same way, so for a direct exec this can include one;
  ``timeout``; ``error`` / ``cancelled`` (the dial raised); ``spawned`` (a direct-mode
  stdio channel's ssh started; not a connection result).
- **Never in the way.** Recording is best-effort: a lock that can't be had within
  :data:`LOCK_WAIT_S`, or any I/O error, drops that one line and never raises into
  the dial. On an event loop's thread, ``record`` does no file I/O at all: it hands
  the line to a background writer thread (bounded by :data:`QUEUE_MAX`; a saturated
  writer drops the line), which then waits for the lock like any writer.
"""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("ssh-manager.dial-log")

#: Overrides where the per-target logs live (default ``~/.ssh-manager/dial-log``).
DIAL_LOG_ENV = "SSH_MANAGER_DIAL_LOG_DIR"
MAX_LINES = 5000
MAX_BYTES = 1024 * 1024
KEEP_LINES = 4000
STDERR_TAIL = 300
FIELD_CAP = 200
LOCK_WAIT_S = 2.0
#: Lines from event-loop threads waiting for the background writer; past this, dropped.
QUEUE_MAX = 256
#: The only account values recorded (see :func:`account_of`).
ACCOUNTS = ("pinned", "ambient")
#: Outcomes that are a connection attempt reaching (or failing to reach) the target.
DIAL_KINDS = ("config_fetch", "control_master", "direct_exec", "stdio_channel", "reconnect")
#: Outcomes that aren't failures: ``ok`` (connected), and ``spawned`` (a direct-mode
#: stdio channel's ssh started; whether it connected is its caller's to see).
NOT_FAILED = ("ok", "spawned")

_SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|(?i:authorization:\s*\S+(?:\s+\S+)?)|(?i:bearer\s+\S+)"
    r"|(?i:(?:token|password|secret)\s*[=:]\s*\S+)"
)


def log_dir() -> Path:
    override = os.environ.get(DIAL_LOG_ENV, "").strip()
    return Path(override) if override else Path.home() / ".ssh-manager" / "dial-log"


def _file_for(target: str) -> Path:
    """One file per target: a readable prefix plus a digest of the exact target, so
    targets that sanitize alike (``container:foo`` / ``container_foo``) never share one."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", target or "unknown")[:100] or "unknown"
    digest = hashlib.sha256((target or "").encode("utf-8")).hexdigest()[:12]
    return log_dir() / f"{safe}-{digest}.jsonl"


def _private_dir(path: Path) -> None:
    """The log directory, user-only (0700) on POSIX; a per-user profile dir on Windows."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o700)


def _open_private(path: Path, mode: str):
    """Open *path* for writing, created user-only (0600) on POSIX."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if "a" in mode else os.O_TRUNC)
    fd = os.open(path, flags | getattr(os, "O_BINARY", 0), 0o600)
    if os.name != "nt":
        os.fchmod(fd, 0o600)
    return os.fdopen(fd, mode)


def redact(text: str) -> str:
    return _SECRET.sub("[REDACTED]", text or "")


def account_of(env: dict | None) -> str:
    """``pinned`` when the dial runs under an explicit account token, else ``ambient``."""
    return "pinned" if (env or {}).get("GH_TOKEN") else "ambient"


def _in_event_loop() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@contextlib.contextmanager
def _locked(path: Path, *, wait: float = LOCK_WAIT_S):
    """An exclusive per-file lock, waited for at most *wait* seconds (0: one try);
    yields whether it was had."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    if not lock_path.exists():
        _open_private(lock_path, "ab").close()
    fh = open(lock_path, "a+b")
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.02)
        try:
            yield True
        finally:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def _trim(path: Path) -> None:
    with path.open("rb") as fh:
        lines = fh.read().splitlines(keepends=True)
    if len(lines) <= MAX_LINES and sum(map(len, lines)) <= MAX_BYTES:
        _save_count(path, sum(map(len, lines)), len(lines))
        return
    keep = lines[-KEEP_LINES:]
    while keep and sum(map(len, keep)) > MAX_BYTES * 3 // 4:
        keep = keep[max(1, len(keep) // 4):]  # always progresses, even for a few huge lines
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _open_private(tmp, "wb") as fh:
        fh.write(b"".join(keep))
    os.replace(tmp, path)
    _save_count(path, sum(map(len, keep)), len(keep))


def _count_file(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".count")


def _save_count(path: Path, size: int, lines: int) -> None:
    with _open_private(_count_file(path), "w") as fh:
        fh.write(f"{size} {lines}")


def _lines_after_append(path: Path, before: int, added: int) -> int:
    """The log's line count after one line was appended at size *before*: kept as
    ``<size> <lines>`` in a sidecar, written under the writer lock, so a steady-state
    append never rescans the log. A sidecar that doesn't describe the file it was
    written for (a crash between the two writes, an edit) is recovered by one count,
    bounded because the log is trimmed at :data:`MAX_BYTES`."""
    try:
        size, lines = (int(x) for x in _count_file(path).read_text(encoding="ascii").split())
    except (OSError, ValueError):
        size, lines = -1, 0
    if size == before:
        lines += 1
    else:
        with path.open("rb") as fh:
            lines = sum(1 for _ in fh)
    _save_count(path, before + added, lines)
    return lines


def record(target: str, *, kind: str, outcome: str, elapsed_s: float, attempt: int | None = None,
           reason: str = "", stderr: str = "", account: str = "") -> None:
    """Append one dial attempt to *target*'s log; best-effort, never raises."""
    try:
        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "target": str(target)[:FIELD_CAP], "kind": str(kind)[:FIELD_CAP],
            "outcome": str(outcome)[:FIELD_CAP],
            "elapsed_s": round(max(elapsed_s, 0.0), 3), "attempt": attempt,
            "reason": redact(reason)[:FIELD_CAP],
            "account": account if account in ACCOUNTS else ("other" if account else ""),
            "stderr": redact((stderr or "").strip())[-STDERR_TAIL:],
        }
        path = _file_for(target)
        line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        if _in_event_loop():
            _enqueue(path, line)  # the loop's thread never touches the file
        else:
            _write(path, line)
    except Exception as exc:  # noqa: BLE001 -- telemetry must never break a dial
        log.debug("dial log write for %s failed: %s", target, exc)


def _write(path: Path, line: bytes) -> None:
    """Append *line* under the per-file lock (waiting up to :data:`LOCK_WAIT_S`), then
    trim if the log passed its bounds. Never raises."""
    try:
        _private_dir(path.parent)
        with _locked(path, wait=LOCK_WAIT_S) as held:
            if not held:
                return
            before = path.stat().st_size if path.exists() else 0
            with _open_private(path, "ab") as fh:
                fh.write(line)
            if path.stat().st_size > MAX_BYTES or _lines_after_append(path, before, len(line)) > MAX_LINES:
                _trim(path)
    except Exception as exc:  # noqa: BLE001 -- telemetry must never break a dial
        log.debug("dial log write to %s failed: %s", path.name, exc)


_writer_guard = threading.Lock()
_queue: "queue.Queue | None" = None


def _enqueue(path: Path, line: bytes) -> None:
    """Hand a line to the background writer (started on first use); a saturated
    writer drops it rather than grow without bound or block the loop."""
    global _queue
    with _writer_guard:
        if _queue is None:
            _queue = queue.Queue(maxsize=QUEUE_MAX)
            threading.Thread(target=_drain, args=(_queue,), name="dial-log-writer", daemon=True).start()
    try:
        _queue.put_nowait((path, line))
    except queue.Full:
        log.debug("dial log writer saturated; dropped a line for %s", path.name)


def _drain(q: "queue.Queue") -> None:
    while True:
        path, line = q.get()
        try:
            _write(path, line)
        finally:
            q.task_done()


def flush(timeout: float = 2 * LOCK_WAIT_S) -> bool:
    """Wait, at most *timeout* seconds, until lines recorded from event loops are on
    disk; whether they all are."""
    q = _queue
    if q is None:
        return True
    with q.all_tasks_done:
        return q.all_tasks_done.wait_for(lambda: q.unfinished_tasks == 0, timeout)


def _reset_writer() -> None:  # a forked child has no writer thread: start afresh
    global _queue, _writer_guard
    _queue, _writer_guard = None, threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_writer)
atexit.register(flush, 1.0)


def read(target: str, *, last: int = 50) -> list[dict]:
    """The newest *last* entries for *target* (oldest first); unreadable lines skipped.
    Lines this process recorded from an event loop are flushed first (bounded)."""
    flush()
    path = _file_for(target)
    if last <= 0 or not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines()[-last:]:
        with contextlib.suppress(ValueError):
            entry = json.loads(raw)
            if isinstance(entry, dict):
                out.append(entry)
    return out


def summary(target: str, *, now: datetime | None = None) -> dict:
    """Dial counts by outcome over the last 10 minutes and hour, and the last failure."""
    now = now or datetime.now(timezone.utc)
    entries = read(target, last=MAX_LINES)
    windows = {"last_10m": 600, "last_1h": 3600}
    counts: dict[str, dict] = {w: {"dials": 0, "by_outcome": {}} for w in windows}
    last_failure = None
    for e in entries:
        try:
            age = (now - datetime.fromisoformat(e.get("at", ""))).total_seconds()
        except ValueError:
            continue
        if e.get("outcome") not in NOT_FAILED:
            last_failure = e
        for w, span in windows.items():
            if 0 <= age <= span and e.get("kind") in DIAL_KINDS:
                counts[w]["dials"] += 1
                by = counts[w]["by_outcome"]
                by[e.get("outcome", "?")] = by.get(e.get("outcome", "?"), 0) + 1
    return {"target": target, "log": str(_file_for(target)), **counts, "last_failure": last_failure,
            "entries": len(entries)}


class Dial:
    """Times one attempt: ``with Dial(target, "config_fetch", attempt=1) as d: ...;
    d.outcome = "ok"``. An exception escaping the block records ``error`` (with its
    message as the reason) unless an outcome was already set, and propagates."""

    def __init__(self, target: str, kind: str, *, attempt: int | None = None, account: str = ""):
        self.target, self.kind, self.attempt, self.account = target, kind, attempt, account
        self.outcome, self.reason, self.stderr = "", "", ""

    def __enter__(self) -> "Dial":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc is not None and not self.outcome:
            self.outcome, self.reason = "error", f"{exc_type.__name__}: {exc}"
        record(self.target, kind=self.kind, outcome=self.outcome or "unknown",
               elapsed_s=time.monotonic() - self._start, attempt=self.attempt,
               reason=self.reason, stderr=self.stderr, account=self.account)
        return False
