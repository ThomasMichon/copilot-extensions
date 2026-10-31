"""Background local/SSH CLI feed enrollment and cancellation-owned reconnect."""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import threading
import time

from ...worktree_feed_contract import CurrentFeed, FeedUnavailable
from . import derive

INITIAL_TIMEOUT = 2.0
IDLE_TIMEOUT = 5.0
MAX_LINE_CHARS = 80 * 1024


class ReadFailure(str):
    def __new__(cls, message, *, kind):
        error = super().__new__(cls, message)
        error.kind = kind
        return error


class _Watchdog:
    """One sleeping watchdog per held source, never a timer thread per row."""

    def __init__(self, kill, timeout: float) -> None:
        self._condition = threading.Condition()
        self._deadline = time.monotonic() + timeout
        self._closed = False
        self.expired = False
        self._kill = kill
        self._thread = threading.Thread(target=self._run, daemon=True, name="worktrees-feed-deadline")
        self._thread.start()

    def arm(self, timeout: float) -> None:
        with self._condition:
            self._deadline = time.monotonic() + timeout
            self._condition.notify_all()

    def _run(self) -> None:
        with self._condition:
            while not self._closed:
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    self.expired = True
                    break
                self._condition.wait(remaining)
            else:
                return
        self._kill()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._thread.join(timeout=1)


def feed_argv(source):
    from . import data_ssh

    if not source.local:
        return data_ssh._edit_remote_cmd(
            data_ssh._stream_argv(source),
            lambda command: command.replace(" --stream", " --stream --follow"),
        )
    from ... import engine_client

    base = engine_client.engine_base_command()
    if base is None:
        raise FeedUnavailable("the engine is not installed", retry=False)
    return [
        *base, "--project", data_ssh._project(),
        "list", "--json", "--current", "--stream", "--follow", "--classify", "--mux-details",
    ]


class CurrentFeedMixin:
    def _publish_source_records(self, source, records):
        """Publish a complete roster and its incarnation under the caller's lock."""
        self._records[source.cache_key] = records
        self._state[source.cache_key] = "ready"
        self._error[source.cache_key] = ""
        self._feed_complete[source.cache_key] = source.instance_id

    def read_status_for_source(self, source_id):
        """Presentation evidence only; a read failure never proves host liveness."""
        with self._lock:
            source = next((item for item in self._all_sources if item.cache_key == source_id), None)
            return {
                "error": self._error.get(source_id),
                "failure_kind": getattr(self._error.get(source_id), "kind", "unknown"),
                "instance_id": source.instance_id if source is not None else None,
                "has_snapshot": source is not None and (
                        self._feed_complete.get(source_id) == source.instance_id
                        or (not source.instance_id and (
                            bool(self._records.get(source_id)) or (
                                self._state.get(source_id) == "ready"
                                and source_id not in self._pinged_only
                                and self._stream_incomplete.get(source_id) != self._gen.get(source_id, 0)
                            )
                        ))
                ),
            }

    def _feed_current(self, source, gen) -> bool:
        return not self._cancelled.is_set() and self._gen.get(source.cache_key, 0) == gen

    def _feed_commit(self, source, gen, rows, *, availability=None) -> None:
        derive.NOW = _dt.datetime.now()
        with self._lock:
            previous = {row["id"]: row for row in self._records.get(source.cache_key, [])}
        records = []
        for row in rows:
            old = previous.get(row["id"])
            old_raw = {} if old is None else old["raw"]
            if old is not None and {
                key: value for key, value in old_raw.items() if key != "_projection"
            } == {key: value for key, value in row.items() if key != "_projection"}:
                records.append({**old, **derive.time_fields(row), "raw": row})
                continue
            normalized = derive.norm(
                {**row, "_availability": None}, source.machine, source.env,
                source_kind=source.source_kind, source_id=source.source_id,
                source_label=source.source_label,
                source_metadata={
                    "provider": source.provider, "target_id": source.target_id,
                    "instance_id": source.instance_id, "venue": dict(source.venue),
                },
                source_capabilities=source.capabilities,
            )
            records.append({**normalized, "raw": row})
        with self._lock:
            if self._feed_current(source, gen):
                self._publish_source_records(source, records)
                available = availability or (rows[0].get("_availability") if rows else None)
                self._error[source.cache_key] = (
                    ReadFailure(available.get("reason") or "Resident refresh is unavailable", kind="content")
                    if available and available.get("status") == "stale" else ""
                )
                self._feed_backoff[source.cache_key] = 0.25

    def _feed_error(self, source, gen, reason: str, *, kind: str = "connection") -> None:
        if source.local:
            reason = f"Local resident current-roster read failed: {reason}"
        with self._lock:
            if not self._feed_current(source, gen):
                return
            self._error[source.cache_key] = ReadFailure(reason, kind=kind)
            has_snapshot = self._feed_complete.get(source.cache_key) == source.instance_id
            self._state[source.cache_key] = "ready" if has_snapshot else "failed"
            if has_snapshot:
                records = []
                for row in self._records[source.cache_key]:
                    raw = {**row["raw"], "_availability": {"status": "stale", "reason": reason}}
                    records.append({**row, "raw": raw})
                self._records[source.cache_key] = records

    def _feed_once(self, source, gen) -> str:
        if source.local:
            from ... import resident_picker
            from .data_ssh import _project

            root = resident_picker.root_for(_project())
            if root is not None:
                return self._feed_once_resident(source, gen, root)
        from .data_ssh import _kill_proc_tree

        proc = self._spawn_stream(feed_argv(source))
        watchdog = _Watchdog(lambda: _kill_proc_tree(proc), source.timeout)
        stderr = []

        def drain_stderr():
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    return
                stderr.append(chunk)
                if sum(map(len, stderr)) > 8192:
                    stderr[:] = ["".join(stderr)[-8192:]]

        errors = threading.Thread(target=drain_stderr, name="worktrees-feed-stderr", daemon=True)
        errors.start()
        receiver = CurrentFeed()
        try:
            while self._feed_current(source, gen):
                raw = proc.stdout.readline(MAX_LINE_CHARS + 1)
                if not self._feed_current(source, gen) or watchdog.expired or not raw:
                    break
                if len(raw) > MAX_LINE_CHARS or not raw.endswith("\n"):
                    raise ValueError("current-roster feed exceeded its frame bound")
                stripped = raw.strip()
                if not stripped.startswith("{"):
                    # SSH login banners before the protocol are not publications.
                    if receiver.head is not None or receiver._begin is not None:
                        raise ValueError("current-roster feed included noise inside the protocol")
                    continue
                frame = json.loads(stripped)
                rows = receiver.receive(frame)
                if frame.get("type") in {"begin", "update"}:
                    watchdog.arm(INITIAL_TIMEOUT)
                if rows is not None:
                    self._feed_commit(source, gen, rows, availability=receiver.availability)
                    watchdog.arm(IDLE_TIMEOUT)
                elif frame.get("type") == "heartbeat":
                    watchdog.arm(IDLE_TIMEOUT)
                if receiver.notice is not None:
                    return f"resident going away: {receiver.notice}"
            if not self._feed_current(source, gen):
                return "cancelled"
            if watchdog.expired:
                raise FeedUnavailable(
                    "current-roster feed missed its startup/publication/heartbeat deadline",
                    connected=receiver.head is not None or receiver._begin is not None,
                )
            errors.join(timeout=0.1)
            error = "".join(stderr).strip()
            if "unrecognized arguments" in error or "update the engine" in error:
                raise FeedUnavailable(
                    "engine lacks --follow/worktrees-feed-v1; update the engine",
                    retry=False, connected=True,
                )
            raise FeedUnavailable(error or "current-roster feed disconnected without a going-away notice")
        finally:
            watchdog.close()
            _kill_proc_tree(proc)
            try:
                proc.wait(timeout=5)
                errors.join(timeout=1)
            finally:
                self._untrack(proc)

    def _feed_once_resident(self, source, gen, root) -> str:
        from ... import resident_transport
        from .data_ssh import _project

        receiver = CurrentFeed()
        stream = resident_transport.frames(
            root, _project(), alive=lambda: self._feed_current(source, gen),
            startup_timeout=source.timeout,
        )
        try:
            for frame in stream:
                if not self._feed_current(source, gen):
                    return "cancelled"
                rows = receiver.receive(frame)
                if rows is not None:
                    self._feed_commit(source, gen, rows, availability=receiver.availability)
                if receiver.notice is not None:
                    return f"resident going away: {receiver.notice}"
            return "cancelled"
        finally:
            stream.close()

    def _load_current_feed(self, source, gen) -> None:
        with self._lock:
            if not self._feed_current(source, gen):
                return
            self._held_sources.add(source.cache_key)
        try:
            while self._feed_current(source, gen):
                kind = "connection"
                try:
                    reason = self._feed_once(source, gen)
                except FeedUnavailable as exc:
                    kind = "content" if exc.connected else "connection"
                    if not exc.retry:
                        self._feed_error(source, gen, str(exc), kind=kind)
                        return
                    reason = str(exc)
                except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
                    self._feed_error(
                        source, gen, str(exc) or type(exc).__name__,
                        kind="content" if isinstance(exc, (TypeError, ValueError)) else "connection",
                    )
                    return
                self._feed_error(source, gen, reason, kind=kind)
                with self._lock:
                    delay = self._feed_backoff.get(source.cache_key, 0.25)
                    self._feed_backoff[source.cache_key] = min(delay * 2, 5.0)
                if not self._feed_current(source, gen) or self._cancelled.wait(delay):
                    return
        finally:
            with self._lock:
                self._held_sources.discard(source.cache_key)
