"""Explicit retained-spool API; legacy spool factories/paths are not adopted."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .diagnostic_logs import DiagnosticLog, location


def milliseconds_timestamp(event: dict) -> datetime:
    value = event["ts"]
    if type(value) not in (int, float):
        raise ValueError("retained producer ts must be epoch milliseconds")
    return datetime.fromtimestamp(value / 1000, timezone.utc)


class RetainedSpool:
    """Callable sink in a separate, owner-preprovisioned ``retained`` directory."""

    def __init__(self, directory: Path, component: str, *, service: str, custody,
                 timestamp_decoder=milliseconds_timestamp, clock=None):
        options = {"clock": clock} if clock is not None else {}
        self.store = DiagnosticLog(
            component, Path(directory).expanduser() / "retained",
            service=service, custody=custody, create_parent=False,
            discovery_hint="inspect the retained sink.inventory()",
            timestamp_decoder=timestamp_decoder,
            encoder=lambda event: (
                json.dumps(event, separators=(",", ":"), default=str) + "\n"
            ).encode("utf-8"),
            **options,
        )

    def __call__(self, event: dict) -> None:
        try:
            record = dict(event)
            record.setdefault("ts", int(time.time() * 1000))
            self.store.write_event(record)
        except Exception as error:
            self.store._degraded(error)

    def maintain(self, *, force: bool = False) -> None:
        self.store.maintain(force=force)

    def inventory(self) -> dict:
        return location(self.store.directory, self.store.component, self.store.service)
