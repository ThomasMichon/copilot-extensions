"""Non-blocking frame-gap diagnostics for the Textual Picker."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_STOP = object()
_DEFAULT_THRESHOLD_SECONDS = 0.5
_QUEUE_SIZE = 128


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class FrameHealthReporter:
    """Enqueue timer gaps on the UI thread and persist them on a writer thread."""

    def __init__(
        self,
        path: Path,
        *,
        threshold_seconds: float = _DEFAULT_THRESHOLD_SECONDS,
    ) -> None:
        self.path = path
        self.threshold_seconds = max(0.1, float(threshold_seconds))
        self._queue: queue.Queue[dict | object] = queue.Queue(maxsize=_QUEUE_SIZE)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_tick: float | None = None
        self._dropped = 0

    @classmethod
    def from_env(cls) -> FrameHealthReporter | None:
        raw = os.environ.get("AGENT_WORKTREES_PICKER_FRAME_HEALTH", "").strip()
        if not raw or raw.lower() in ("0", "false", "no", "off"):
            return None
        path = (
            Path.home() / ".agent-worktrees" / "logs" / "picker-frame-health.jsonl"
            if raw.lower() in ("1", "true", "yes", "on")
            else Path(raw).expanduser()
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
        return cls(path, threshold_seconds=threshold)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._last_tick = time.monotonic()
        self._thread = threading.Thread(
            target=self._write_loop,
            name="picker-frame-health",
            daemon=True,
        )
        self._thread.start()
        self._enqueue({"event": "start", "pid": os.getpid()})

    def tick(self, *, frame: int, debug: str, busy: str | None) -> None:
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
        })

    def close(self, *, wait: bool = False) -> None:
        if self._thread is None:
            return
        self._enqueue({"event": "stop", "dropped": self._dropped})
        self._stop.set()
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            pass
        thread = self._thread
        if wait:
            thread.join(timeout=2)
        if not thread.is_alive():
            self._thread = None

    def _enqueue(self, event: dict) -> None:
        event = {"timestamp": _timestamp(), **event}
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._dropped += 1

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
                    stream.write(json.dumps(event, separators=(",", ":")) + "\n")
        except OSError:
            return
        finally:
            self._thread = None
