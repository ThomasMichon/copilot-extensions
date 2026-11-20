"""The existing synchronous callable seam, with separate per-consumer state."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing_extensions import Unpack
    from .buffer import BufferBounds, BufferedSink
    from .command import CommandRoutes

TelemetrySink = Callable[[dict[str, Any]], None]
LEGACY = object()


class SinkSlot:
    """Preserve synchronous legacy sinks; owners may register a bounded adapter."""

    def __init__(self) -> None:
        self.sink: TelemetrySink | None = None
        self._owned_buffer: BufferedSink | None = None
        self._last_buffer: BufferedSink | None = None
        self._configuration_lock = threading.RLock()
        self._command_owner: CommandRoutes | None = None

    def configure_commands(self, *, source: str = "agent-bridge") -> CommandRoutes:
        """Create one bound-route owner without replacing the legacy sink."""
        from .command import CommandRoutes

        with self._configuration_lock:
            if self._command_owner is not None and not self._command_owner.closed:
                raise RuntimeError("command owner is already configured")
            if self._command_owner is not None and any(
                binding.state == "cleanup_unconfirmed" or (
                    binding.pipe is not None and not binding.pipe.closed
                )
                for binding in self._command_owner.bindings
            ):
                raise RuntimeError("previous command custody is unconfirmed")
            self._command_owner = CommandRoutes(self, source=source)
            return self._command_owner

    def command_buffer(
        self, sink: TelemetrySink, *, on_iteration: Callable[[], None] | None = None,
    ) -> BufferedSink:
        """Allocate the one existing writer without changing synchronous legacy calls."""
        from .buffer import BufferedSink

        with self._configuration_lock:
            if self._last_buffer is not None and not self._last_buffer.join(0):
                raise RuntimeError("previous telemetry writer has not stopped")
            writer = BufferedSink(sink, on_iteration=on_iteration).start()
            self._last_buffer = writer
            return writer

    def set(self, sink: TelemetrySink | None) -> None:
        """Replace the sink, closing only a buffer explicitly created here."""
        with self._configuration_lock:
            previous = self._owned_buffer
            self._owned_buffer = None
            self.sink = sink
            if previous is not None:
                previous.close()

    def set_buffered(
        self, sink: TelemetrySink | Callable[[list[dict[str, Any]]], None],
        **bounds: Unpack[BufferBounds],
    ) -> BufferedSink:
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

    def emit(self, event: dict[str, Any], *, binding: Any = LEGACY) -> None:
        """Deliver through the selected seam; diagnostic failures are nonfatal."""
        if binding is not LEGACY:
            try:
                if binding is not None:
                    binding.offer(event)
            except Exception:
                pass
            return
        sink = self.sink
        if sink is not None:
            try:
                sink(event)
            except Exception:
                pass
