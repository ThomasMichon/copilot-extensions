"""Logging adapter over the existing bounded, owner-started diagnostic writer."""

from __future__ import annotations

import logging
import re

from .buffer import BufferedSink
from .diagnostic_logs import DiagnosticLog


class BufferedDiagnosticLog(logging.Handler):
    """Producer does no formatting, file access, history work or blocking lock.

    Only bounded primitives enter BufferedSink. Nonprimitive/oversized records
    are counted as invalid rather than invoking arbitrary repr/format hooks.
    """

    def __init__(self, store: DiagnosticLog, *, capacity: int = 128):
        super().__init__()
        self.store = store
        self.baseFilename = store.baseFilename
        self.initialized = False

        def initialize():
            try:
                self.store.maintain(force=True)
            finally:
                self.initialized = True

        self.writer = BufferedSink(
            self._consume, capacity=capacity, batch_size=min(32, capacity), max_bytes=65536,
            on_start=initialize,
        ).start()

    def handle(self, record: logging.LogRecord) -> bool:
        # Handler.handle's blocking RLock is unnecessary: BufferedSink owns its
        # nonblocking producer lock; the worker never touches producer state.
        if self.filter(record):
            self.emit(record)
            return True
        return False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event = self.store._record_fields(record)
            event["_template"] = record.msg
            args = record.args
            event["_args"] = list(args) if type(args) is tuple and len(args) <= 32 else args
            for key in ("operation_id", "correlation_id", "generation"):
                if key in record.__dict__:
                    event[key] = record.__dict__[key]
            if record.exc_info:
                kind, _error, trace = record.exc_info
                event["exception_type"] = kind.__name__
                frames = []
                for _ in range(8):
                    if trace is None:
                        break
                    code = trace.tb_frame.f_code
                    frames.append({
                        "file": code.co_filename, "function": code.co_name,
                        "line": trace.tb_lineno,
                    })
                    trace = trace.tb_next
                event["exception_frames"] = frames
            self.writer(event)
        except Exception:
            # Let the existing copier count invalid input without formatting it.
            self.writer({"invalid": object()})

    def _consume(self, event: dict) -> None:
        control = event.get("_control")
        if control:
            self.store.maintain(force=control == "startup")
            return
        template = event.pop("_template")
        args = event.pop("_args")
        tokens = re.finditer(
            r"%(?:\([^)]*\))?[-+#0 ]*(\d+|\*)?(?:\.(\d+|\*))?[hlL]?[a-zA-Z%]", template,
        ) if args else ()
        for token in tokens:
            if any(part == "*" or (part is not None and int(part) > 512)
                   for part in token.groups()):
                raise ValueError("diagnostic format width/precision limit")
        event["message"] = template % (tuple(args) if type(args) is list else args) if args else template
        if len(event["message"]) > 8192:
            raise ValueError("diagnostic formatted message limit")
        self.store.write_event(event)

    def maintain(self) -> None:
        """Request maintenance on this same writer, never on the serving thread."""
        self.writer({"_control": "maintenance"})

    def stats(self) -> dict:
        return {**self.writer.stats(), "storage_error": self.store.last_error}

    def close(self) -> None:
        writer = getattr(self, "writer", None)
        if writer is not None:
            writer.close()
        super().close()
