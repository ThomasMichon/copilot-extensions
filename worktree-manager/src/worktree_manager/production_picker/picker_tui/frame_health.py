"""Non-blocking frame-gap diagnostics for the Textual Picker."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_STOP = object()
_DEFAULT_THRESHOLD_SECONDS = 0.5
_QUEUE_SIZE = 128
_source_reporter = None
_last_source_reporter = None
_scope_lock = threading.Lock()


@contextmanager
def source_observation_scope(reporter):
    """Explicit source-process admission, before UI callbacks; never auto-enroll."""
    global _source_reporter, _last_source_reporter
    with _scope_lock:
        previous_thread = getattr(_last_source_reporter, "_thread", None)
        if _source_reporter is not None or (previous_thread is not None and previous_thread.is_alive()):
            raise RuntimeError("a source observation reporter still owns delivery")
        if reporter.observation is None:
            raise ValueError("source observation configuration is required")
        if reporter._source_started or reporter._source_closed:
            raise RuntimeError("source observation reporters are single-use")
        _source_reporter = _last_source_reporter = reporter
    try:
        yield reporter
    finally:
        try:
            reporter.close()
            reporter.drain()
        finally:
            with _scope_lock:
                if _source_reporter is reporter:
                    _source_reporter = None


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _launch_trace_path() -> Path | None:
    from ... import picker_review

    if picker_review.active():
        return None
    raw = os.environ.get("AGENT_WORKTREES_LAUNCH_TRACE", "").strip()
    if not raw or raw.lower() in ("0", "false", "no", "off"):
        return None
    if raw.lower() in ("1", "true", "yes", "on"):
        return (
            Path.home()
            / ".agent-worktrees"
            / "logs"
            / "picker-launches.jsonl"
        )
    return Path(raw).expanduser()


def append_launch_event(event: str, **fields) -> None:
    """Append one pre-render launch checkpoint. Best-effort and fail-silent."""
    reporter = _source_reporter or _last_source_reporter
    if reporter is not None:
        reporter._enqueue({"event": event, **fields})
        return
    path = _launch_trace_path()
    if path is None:
        return
    payload = {
        "timestamp": _timestamp(),
        "event": event,
        "launch_id": os.environ.get("AGENT_WORKTREES_LAUNCH_ID", ""),
        **fields,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError:
        return


class FrameHealthReporter:
    """Enqueue timer gaps on the UI thread and persist them on a writer thread."""

    def __init__(
        self,
        path: Path,
        *,
        threshold_seconds: float = _DEFAULT_THRESHOLD_SECONDS,
        report_gaps: bool = True,
        launch_id: str = "",
        binstub_started: str = "",
        observation=None,
    ) -> None:
        self.path = path
        self.threshold_seconds = max(0.1, float(threshold_seconds))
        self.report_gaps = report_gaps
        self.launch_id = launch_id
        self.binstub_started = binstub_started
        self._observation = observation
        self._queue: queue.Queue[dict | object] = queue.Queue(maxsize=_QUEUE_SIZE)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_tick: float | None = None
        self._dropped = 0
        self._source_started = False
        self._source_writer_started = False
        self._source_closed = False
        self._owner_drain_ns = 0
        self._lifecycle_lock = threading.RLock()

    observation = property(lambda self: self._observation)

    @classmethod
    def from_env(cls) -> FrameHealthReporter | None:
        from ... import picker_review

        if picker_review.active():
            return None
        if _source_reporter is not None:
            return _source_reporter
        if _last_source_reporter is not None:
            return None  # Late source callbacks must not reactivate legacy file writes.
        health_raw = os.environ.get(
            "AGENT_WORKTREES_PICKER_FRAME_HEALTH", ""
        ).strip()
        trace_path = _launch_trace_path()
        health_enabled = bool(
            health_raw
            and health_raw.lower() not in ("0", "false", "no", "off")
        )
        trace_enabled = trace_path is not None
        if not health_enabled and not trace_enabled:
            return None
        truthy = ("1", "true", "yes", "on")
        if health_enabled and health_raw.lower() not in truthy:
            path = Path(health_raw).expanduser()
        elif trace_enabled:
            path = trace_path
        else:
            path = (
                Path.home()
                / ".agent-worktrees"
                / "logs"
                / "picker-frame-health.jsonl"
            )
        try:
            threshold = float(
                os.environ.get(
                    "AGENT_WORKTREES_PICKER_FRAME_GAP_SECONDS",
                    _DEFAULT_THRESHOLD_SECONDS,
                )
            )
        except ValueError:
            threshold = _DEFAULT_THRESHOLD_SECONDS
        return cls(
            path,
            threshold_seconds=threshold,
            report_gaps=health_enabled,
            launch_id=os.environ.get("AGENT_WORKTREES_LAUNCH_ID", ""),
            binstub_started=os.environ.get(
                "AGENT_WORKTREES_BINSTUB_STARTED", ""
            ),
        )

    def start(self) -> None:
        with self._lifecycle_lock:
            self._start()

    def _start(self) -> None:
        if self.observation is not None:
            if self._source_started or self._source_closed:
                return
            self._source_started = True
            self._enqueue({"event": "textual_first_refresh"})
            if not self.observation.sampled:
                return
        if self._thread is not None:
            return
        if self.observation is None:
            self._stop.clear()
        self._last_tick = time.monotonic()
        try:
            self._thread = threading.Thread(
                target=self._write_loop,
                name="picker-frame-health",
                daemon=True,
            )
            self._thread.start()
        except Exception:
            if self.observation is None:
                raise
            self.observation.increment("writer_failed")
            self._thread = None
            return
        self._source_writer_started = self.observation is not None
        if self.observation is None:
            self._enqueue({
                "event": "textual_first_refresh",
                "pid": os.getpid(),
                "launch_id": self.launch_id,
                "binstub_started": self.binstub_started,
            })

    def tick(self, *, frame: int, debug: str, busy: str | None) -> None:
        if not self.report_gaps:
            return
        now = time.monotonic()
        previous = self._last_tick
        self._last_tick = now
        if previous is None:
            return
        gap = now - previous
        if gap < self.threshold_seconds:
            return
        self._enqueue({
            "event": "gap",
            "frame": frame,
            "gap_ms": round(gap * 1000, 1),
            "debug": debug,
            "busy": busy,
            "launch_id": self.launch_id,
        })

    def close(self, *, wait: bool = False) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            self._request_close()
        if wait and thread is not None and self.observation is None:
            thread.join(timeout=2)

    def drain(self, timeout=3):
        """Explicit off-UI owner wait; source close never honors a UI wait request."""
        if self.observation is None or not self._source_closed:
            raise RuntimeError("close the source reporter before draining")
        if type(timeout) not in (int, float) or not 0 <= timeout <= 3:
            raise ValueError("drain timeout must be between zero and three seconds")
        thread = self._thread
        started = time.perf_counter_ns()
        try:
            if thread is not None:
                thread.join(timeout=timeout)
        finally:
            self._owner_drain_ns += time.perf_counter_ns() - started
        return thread is None or not thread.is_alive()

    def _request_close(self):
        if self.observation is not None:
            if not self._source_closed:
                self._enqueue({"event": "picker_stop"})
                self._source_closed = True
                self._stop.set()
            return
        if self._thread is None:
            return
        self._enqueue({
            "event": "picker_stop",
            "dropped": self._dropped,
            "launch_id": self.launch_id,
        })
        self._stop.set()
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            pass

    def _enqueue(self, event: dict) -> None:
        if self.observation is None:
            self._enqueue_locked(event)
        else:
            with self._lifecycle_lock:
                self._enqueue_locked(event)

    def _enqueue_locked(self, event: dict) -> None:
        if self.observation is not None:
            if self._source_closed:
                self.observation.increment("after_close")
                return
            event = self.observation.capture(event)
            if event is None:
                return
            event["enqueued_ns"] = time.perf_counter_ns()
        else:
            event = {"timestamp": _timestamp(), **event}
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._dropped += 1
            if self.observation is not None:
                self.observation.increment("queue_full")
        else:
            if self.observation is not None:
                self.observation.increment("queued")

    def observe(self, event: str, **measurements):
        """Explicit source adapter seam; disabled reporters perform no work."""
        if self.observation is not None:
            self._enqueue({"event": event, **measurements})

    def observation_summary(self):
        """Off-path owner snapshot, not a paint/authority or persistence claim."""
        if self.observation is None:
            return None
        thread = self._thread
        alive = thread is not None and thread.is_alive()
        state = self.observation.summary()
        unwritten = max(0, state["counts"]["queued"] - state["counts"]["written"])
        return {
            **state, "writer_started": self._source_writer_started,
            "first_refresh_observed": self._source_started,
            "writer_alive": alive, "unwritten": unwritten,
            "owner_drain_ns": self._owner_drain_ns,
            "abandoned": unwritten if self._source_closed and not alive else 0,
            "queued_remaining": self._queue.qsize(),
        }

    def _write_loop(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", buffering=1) as stream:
                while not self._stop.is_set() or not self._queue.empty():
                    try:
                        event = self._queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if event is _STOP:
                        return
                    if self.observation is None:
                        stream.write(json.dumps(event, separators=(",", ":")) + "\n")
                    else:
                        batch = [event]
                        while len(batch) < 32:
                            try:
                                batch.append(self._queue.get_nowait())
                            except queue.Empty:
                                break
                        self.observation.deliver(stream, batch)
        except OSError:
            if self.observation is not None:
                self.observation.increment("writer_failed")
            return
        except Exception:
            if self.observation is not None:
                self.observation.increment("writer_failed")
            else:
                raise
        finally:
            try:
                if self.observation is not None:
                    self.observation.close()
            except Exception:
                self.observation.increment("writer_failed")
            finally:
                self._thread = None
