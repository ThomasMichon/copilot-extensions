"""Logging adapter over the existing bounded, owner-started diagnostic writer."""

from __future__ import annotations

import logging
import math
import os

from .buffer import BufferedSink
from .diagnostic_logs import DiagnosticLog, _utc


def _validate_percent(template: str) -> None:
    """Validate every conversion before Python can allocate formatted output."""
    index = 0
    while index < len(template):
        if template[index] != "%":
            index += 1
            continue
        index += 1
        if index < len(template) and template[index] == "%":
            index += 1
            continue
        if index < len(template) and template[index] == "(":
            end = template.find(")", index + 1)
            if end < 0 or "(" in template[index + 1:end]:
                raise ValueError("unsupported diagnostic mapping key")
            index = end + 1
        while index < len(template) and template[index] in "-+#0 ":
            index += 1
        for precision in (False, True):
            if precision:
                if index >= len(template) or template[index] != ".":
                    break
                index += 1
            if index < len(template) and template[index] == "*":
                raise ValueError("diagnostic dynamic format limit")
            start = index
            while index < len(template) and "0" <= template[index] <= "9":
                index += 1
            if index > start and int(template[start:index]) > 512:
                raise ValueError("diagnostic format width/precision limit")
        if index < len(template) and template[index] in "hlL":
            index += 1
        if index >= len(template) or template[index] not in "diouxXeEfFgGcrsa%":
            raise ValueError("unsupported diagnostic conversion")
        index += 1


def _safe_argument(value):
    """Retain a failure's context without calling user repr/str or path hooks."""
    kind = type(value)
    if value is None or kind is bool:
        return value, False
    if kind is str:
        return value[:512], len(value) > 512
    if kind is int and -(2**63) <= value < 2**63:
        return value, False
    if kind is float and math.isfinite(value):
        return value, False
    return "<" + kind.__name__[:128] + ">", True


class BufferedDiagnosticLog(logging.Handler):
    """Producer does no formatting, file access, history work or blocking lock.

    Only bounded primitives enter BufferedSink. Nonprimitive arguments become
    explicit type placeholders without invoking repr/format/equality hooks.
    Unsupported envelopes are counted as invalid.
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
            on_start=initialize, on_iteration=self._maintenance,
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
            event["_template"], adapted = _safe_argument(record.msg)
            args = record.args
            if type(args) is tuple and len(args) <= 32:
                prepared = [_safe_argument(value) for value in args]
                event["_args"] = [value for value, _changed in prepared]
                adapted = adapted or any(changed for _value, changed in prepared)
            elif type(args) is dict and len(args) <= 32 and all(
                type(key) is str and len(key) <= 512 for key in args
            ):
                prepared = {key: _safe_argument(value) for key, value in args.items()}
                event["_args"] = {key: value for key, (value, _changed) in prepared.items()}
                adapted = adapted or any(changed for _value, changed in prepared.values())
            else:
                event["_args"] = []
                adapted = True
            if adapted:
                event["argument_adaptation"] = "bounded primitives or type placeholders"
            for key in ("operation_id", "correlation_id", "generation"):
                if key in record.__dict__:
                    value, changed = _safe_argument(record.__dict__[key])
                    event[key] = None if changed else value
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
            self._maintenance(force=control == "startup")
            return
        template = event.pop("_template")
        args = event.pop("_args")
        if type(template) is not str:
            template = str(template)  # only snapshotted primitive types reach here
        if args:
            _validate_percent(template)
        event["message"] = template % (tuple(args) if type(args) is list else args) if args else template
        if len(event["message"]) > 8192:
            raise ValueError("diagnostic formatted message limit")
        self.store.write_event(event)

    def _maintenance(self, *, force: bool = False) -> None:
        previous = self.store._last_maintenance
        self.store.maintain(force=force)
        writer = getattr(self, "writer", None)
        if writer is not None and self.store._last_maintenance != previous:
            observation = {
                "ts": _utc(self.store.clock()).isoformat(),
                "service": self.store.service, "component": self.store.component,
                "logger": self.store.service + ".diagnostics", "level": "INFO",
                "pid": os.getpid(), **self.store.attribution,
                "message": "diagnostic logging status",
                "diagnostic_buffer_callbacks": writer.stats(),
                "storage_error": self.store.last_error,
            }
            self.store.write_event(observation)

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
