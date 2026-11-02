"""Causal call records through the existing Dispatch application handler."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import traceback
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import TracebackType

from agent_procutil.diagnostics import (
    normalized_diagnostic_text,
    safe_diagnostic_text,
    structured_diagnostic_fields,
)

log = logging.getLogger("agent-dispatch.spawn")
_OPAQUE = re.compile(r"""["'](?:env|environment|prompt|startup_invocation)["']\s*:""")
_OS_ERROR = re.compile(r"^\[(?:Errno|WinError) -?\d+\]")
_INPUTS: ContextVar[Mapping[str, str]] = ContextVar("dispatch_diagnostic_inputs", default={})


def current_inputs() -> Mapping[str, str]:
    return _INPUTS.get()


def _without_inputs(text: str, secrets: Mapping[str, str]) -> str:
    # Remove exact supplied inputs before the shared owner's bounded normalization:
    # truncating a long prompt first would leave an unmatched, exposed prefix.
    values = {
        variant
        for value in secrets.values() if value
        for variant in (value, json.dumps(value, ensure_ascii=True)[1:-1],
                        json.dumps(value, ensure_ascii=False)[1:-1])
    }
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    return text


def diagnostic_fields(text: str, secrets: Mapping[str, str]) -> dict[str, str]:
    return structured_diagnostic_fields(_without_inputs(text, secrets), environment=secrets)


def diagnostic_text(text: str, secrets: Mapping[str, str]) -> str:
    """Retain bounded causal text, not serialized task/input envelopes."""
    normalized = normalized_diagnostic_text(text)
    opaque = _OPAQUE.search(normalized) or (
        text.lstrip().startswith(("{", "[")) and not _OS_ERROR.match(normalized)
    )
    text = _without_inputs(text, secrets)
    if opaque:
        fields = structured_diagnostic_fields(text, environment=secrets)
        return json.dumps(fields) if fields else "<omitted:opaque-envelope>"
    return safe_diagnostic_text(text, 1600, secrets)


def exception_detail(
    error: BaseException, trace: TracebackType | None, secrets: Mapping[str, str],
) -> str:
    """Keep bounded visible causes before stack context, without mutating errors."""
    chain: list[tuple[str, BaseException]] = []
    seen: set[int] = set()
    relation = ""
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append((relation, current))
        if current.__cause__ is not None:
            current, relation = current.__cause__, "caused by: "
        elif not current.__suppress_context__:
            current, relation = current.__context__, "during handling: "
        else:
            current = None
    budget = 1280 // len(chain) - 20
    summaries = [
        relation + safe_diagnostic_text(
            f"{type(item).__name__}: {diagnostic_text(str(item), secrets)}",
            budget, secrets,
        )
        for relation, item in chain
    ]
    if current is not None:
        summaries.append("<exception chain cycle or limit>")
    stack = diagnostic_text("".join(traceback.format_tb(trace)), secrets) if trace else ""
    return safe_diagnostic_text(" | ".join(summaries) + ("; stack: " + stack if stack else ""),
                                1600, secrets)


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
        token = _INPUTS.set(self.secrets)
        try:
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
                    detail = exception_detail(exc, None, self.secrets)
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
        finally:
            _INPUTS.reset(token)
