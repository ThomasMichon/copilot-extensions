"""Explicit retained-spool API; legacy spool factories/paths are not adopted."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .diagnostic_logs import CustodyFactory, DiagnosticLog, Event, TimestampDecoder, location, utc_now


def milliseconds_timestamp(event: Event) -> datetime:
    value = event["ts"]
    if type(value) not in (int, float):
        raise ValueError("retained producer ts must be epoch milliseconds")
    return datetime.fromtimestamp(value / 1000, timezone.utc)


class RetainedSpool:
    """Callable sink in a separate, owner-preprovisioned ``retained`` directory."""

    def __init__(
        self, directory: Path, component: str, *, service: str, custody: CustodyFactory,
        timestamp_decoder: TimestampDecoder = milliseconds_timestamp,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = DiagnosticLog(
            component, Path(directory).expanduser() / "retained",
            service=service, custody=custody, create_parent=False,
            discovery_hint="inspect the retained sink.inventory()",
            timestamp_decoder=timestamp_decoder,
            encoder=lambda event: (
                json.dumps(event, separators=(",", ":"), default=str) + "\n"
            ).encode("utf-8"),
            clock=clock if clock is not None else utc_now,
        )

    def __call__(self, event: Event) -> None:
        try:
            record = dict(event)
            record.setdefault("ts", int(time.time() * 1000))
            self.store.write_event(record)
        except Exception as error:
            self.store._degraded(error)

    def maintain(self, *, force: bool = False) -> None:
        self.store.maintain(force=force)

    def inventory(self) -> dict[str, object]:
        return location(self.store.directory, self.store.component, self.store.service)
