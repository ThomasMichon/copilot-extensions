"""The existing synchronous callable seam, with separate per-consumer state."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

TelemetrySink = Callable[[dict[str, Any]], None]


class SinkSlot:
    """Preserve synchronous legacy sinks; owners may register a bounded adapter."""

    def __init__(self) -> None:
        self.sink: TelemetrySink | None = None
        self._owned_buffer = None
        self._last_buffer = None
        self._configuration_lock = threading.RLock()

    def set(self, sink: TelemetrySink | None) -> None:
        """Replace the sink, closing only a buffer explicitly created here."""
        with self._configuration_lock:
            previous = self._owned_buffer
            self._owned_buffer = None
            self.sink = sink
            if previous is not None:
                previous.close()

    def set_buffered(self, sink: TelemetrySink, **bounds):
        """Opt into one owner-managed buffer; return it for counters/bounded join."""
        from .buffer import BufferedSink

        with self._configuration_lock:
            if self._last_buffer is not None and not self._last_buffer.join(0):
                raise RuntimeError("previous telemetry writer has not stopped")
            writer = BufferedSink(sink, **bounds).start()
            self.set(writer)
            self._owned_buffer = writer
            self._last_buffer = writer
            return writer

    def enabled(self) -> bool:
        """Return configuration state without consulting any external surface."""
        return self.sink is not None

    def emit(self, event: dict[str, Any]) -> None:
        """Deliver through the selected seam; diagnostic failures are nonfatal."""
        sink = self.sink
        if sink is not None:
            try:
                sink(event)
            except Exception:
                pass
