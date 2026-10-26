"""Causal call records through the existing Dispatch application handler."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

from agent_procutil.diagnostics import (
    normalized_diagnostic_text,
    safe_diagnostic_text,
    structured_diagnostic_fields,
)

log = logging.getLogger("agent-dispatch.spawn")
_OPAQUE = re.compile(r"""["'](?:env|environment|prompt|startup_invocation)["']\s*:""")


def diagnostic_text(text: str, secrets: Mapping[str, str]) -> str:
    """Retain bounded causal text, not serialized task/input envelopes."""
    if _OPAQUE.search(normalized_diagnostic_text(text)) or text.lstrip().startswith(("{", "[")):
        fields = structured_diagnostic_fields(text, environment=secrets)
        return json.dumps(fields) if fields else "<omitted:opaque-envelope>"
    return safe_diagnostic_text(text, 1600, secrets)


@dataclass
class SpawnDiagnostics:
    supervisor_id: str | None = None
    task_id: str | None = None
    generation: int | None = None
    reservation_key: str | None = None
    creation_key: str | None = None
    cycle_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    secrets: dict[str, str] = field(default_factory=dict, repr=False)

    def event(self, stage: str, outcome: str, **fields: object) -> None:
        record = {
            "event": "spawn_stage", "stage": stage, "outcome": outcome,
            "cycle_id": self.cycle_id, "supervisor_id": self.supervisor_id,
            "task_id": self.task_id, "generation": self.generation,
            "reservation_key": self.reservation_key, "creation_key": self.creation_key,
            **fields,
        }
        record = {
            key: safe_diagnostic_text(value, 1600, self.secrets) if isinstance(value, str) else value
            for key, value in record.items()
        }
        # Existing handlers preserve the message and these three correlation extras.
        log.info(
            "%s", json.dumps(record, ensure_ascii=True),
            extra={"operation_id": self.reservation_key or self.cycle_id,
                   "correlation_id": self.cycle_id, "generation": self.generation},
        )

    @contextmanager
    def stage(self, name: str) -> Iterator[dict[str, object]]:
        call_id = uuid.uuid4().hex
        started = time.monotonic()
        self.event(name, "entered", call_id=call_id)
        result: dict[str, object] = {}
        try:
            yield result
        except Exception as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                detail = f"subprocess timed out after {exc.timeout} seconds"
            else:
                detail = diagnostic_text(str(exc), self.secrets)
            self.event(
                name, "error", call_id=call_id,
                elapsed_seconds=time.monotonic() - started,
                error_type=type(exc).__name__, error=detail,
            )
            raise
        else:
            self.event(
                name, "returned", call_id=call_id,
                elapsed_seconds=time.monotonic() - started, **result,
            )
