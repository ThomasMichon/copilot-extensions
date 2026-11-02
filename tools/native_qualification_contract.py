"""Shared, source-bound phases for a finite native qualification.

These predicates do not establish Job membership, native exit or desktop
qualification. The outside containment owner supplies those observations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, BinaryIO, Callable

WORK_SECONDS = 165
WHOLE_SECONDS = 180
STREAM_BYTES = 1024 * 1024
# A receiver envelope includes two base64 streams and a copy of its terminal.
OWNER_STDOUT_BYTES = 4 * STREAM_BYTES + 65536
DESCENDANT_PHASE = "descendants-settled-v2"
WORK_SCHEMA = "native-qualification.work-completed"
PROCESS_MODE = "process-safety-only"


class QualificationError(RuntimeError):
    pass


def decode_object(data: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise QualificationError("Qualification evidence contains duplicate keys.")
            value[key] = item
        return value

    value = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise QualificationError("Qualification evidence must be an object.")
    return value


def decode_terminal(output: bytes, scope: int, mode: str) -> dict[str, Any]:
    prefix, separator, data = output.partition(b"\n")
    if mode not in {"functional-only", PROCESS_MODE}:
        raise QualificationError("Unknown native qualification mode.")
    expected = "PROCESS SAFETY START" if mode == PROCESS_MODE else "FUNCTIONAL START"
    if not separator or not prefix.startswith(f"{expected}: scope {scope};".encode("ascii")):
        raise QualificationError("Receiver raw stdout lacks its exact native terminal.")
    value = decode_object(data)
    if mode == PROCESS_MODE and (
        value.get("qualification") != PROCESS_MODE
        or value.get("process_safety_positive") is not True
        or value.get("desktop_observed") is not False or "functional_positive" in value
    ):
        raise QualificationError("Process evidence cannot borrow functional or desktop qualification.")
    return value


@dataclass(frozen=True)
class Boundary:
    start: float
    utc: str

    @classmethod
    def supplied(
        cls, start: float, utc: str, clock: Callable[[], float] = time.monotonic,
    ) -> Boundary:
        if type(start) not in (int, float) or not math.isfinite(start) or start <= 0:
            raise QualificationError("Original monotonic origin must be finite and positive.")
        if not isinstance(utc, str):
            raise QualificationError("Original UTC origin is missing.")
        parsed = datetime.fromisoformat(utc)
        if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
            raise QualificationError("Original audit origin must have an explicit UTC offset.")
        result = cls(start, utc)
        result.require(clock, work=True)
        return result

    def require(self, clock: Callable[[], float], *, work: bool = False) -> float:
        now = clock()
        elapsed = now - self.start
        if not math.isfinite(now) or not math.isfinite(self.start) or not 0 <= elapsed < (
            WORK_SECONDS if work else WHOLE_SECONDS
        ):
            raise QualificationError("Original qualification deadline exhausted; no replay or rearming.")
        return now

    def remaining(self, *, work: bool = False, clock: Callable[[], float] = time.monotonic) -> float:
        now = self.require(clock, work=work)
        return self.start + (WORK_SECONDS if work else WHOLE_SECONDS) - now

    @property
    def whole_deadline(self) -> float:
        return self.start + WHOLE_SECONDS


@dataclass
class Stream:
    limit: int = STREAM_BYTES
    data: bytearray = field(default_factory=bytearray)
    errors: list[str] = field(default_factory=list)

    def read(self, source: BinaryIO) -> None:
        try:
            try:
                descriptor = source.fileno()
            except (AttributeError, io.UnsupportedOperation):
                descriptor = None
            while True:
                chunk = os.read(descriptor, 4096) if descriptor is not None else source.read(4096)
                if not chunk:
                    break
                remaining = self.limit - len(self.data)
                self.data.extend(chunk[:remaining])
                if len(chunk) > remaining and not self.errors:
                    self.errors.append("Native output exceeded its bounded stream.")
                # Continue draining without retaining overflow, so a full pipe
                # cannot keep the original owner from observing process exit.
        except (OSError, ValueError) as error:
            self.errors.append(f"{type(error).__name__}: {error}")


@dataclass
class Capture:
    streams: tuple[Stream, Stream] = field(default_factory=lambda: (Stream(limit=OWNER_STDOUT_BYTES), Stream()))
    readers: list[threading.Thread] = field(default_factory=list)
    settled: bool = False
    closed: bool = False

    def start(self, sources: tuple[BinaryIO, BinaryIO]) -> None:
        for source, stream in zip(sources, self.streams):
            reader = threading.Thread(target=stream.read, args=(source,), daemon=True)
            self.readers.append(reader)
            reader.start()

    def finish(
        self, sources: tuple[BinaryIO, BinaryIO], boundary: Boundary,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for reader in self.readers:
            reader.join(max(0, boundary.whole_deadline - clock()))
        self.settled = all(not reader.is_alive() for reader in self.readers)
        if not self.settled:
            raise QualificationError("Native capture readers remain live; stream custody is unconfirmed.")
        failures = []
        for source in sources:
            try:
                source.close()
                if not source.closed:
                    failures.append("Native stream closure remains unconfirmed.")
            except OSError as error:
                failures.append(f"Stream close: {error}")
        self.closed = not failures
        if failures or any(stream.errors for stream in self.streams):
            raise QualificationError(
                "; ".join(failures + [error for stream in self.streams for error in stream.errors])
            )
        boundary.require(clock)


@dataclass
class Qualification:
    """One outside owner's mutable observation sink, never a child attestation."""

    boundary: Boundary
    scope: int
    manifest_sha256: str
    runtime_root: Path
    capture: Capture = field(default_factory=Capture)
    work_completed: bool = False
    worker_reaped: bool = False
    worker_handle_closed: bool = False
    job_zero_observed: bool = False
    job_handle_closed: bool = False
    forced_termination: bool = False
    lifecycle_settled: bool = False
    registry_unchanged: bool = False
    native_exit: int | None = None
    receiver: dict[str, Any] | None = None
    work_receipt_sha256: str | None = None
    admitted: bool = False
    mode: str = "functional-only"

    @property
    def work_receipt(self) -> Path:
        return self.runtime_root / "evidence" / "work-completed.json"

    def validate(self, sandbox: Path, clock: Callable[[], float] = time.monotonic) -> None:
        Boundary.supplied(self.boundary.start, self.boundary.utc, clock)
        if (
            type(self.scope) is not int or self.scope <= 0
            or not isinstance(self.manifest_sha256, str)
            or re.fullmatch(r"[a-f0-9]{64}", self.manifest_sha256) is None
            or not self.runtime_root.is_absolute() or self.runtime_root.resolve() != self.runtime_root
            or sandbox.resolve() != self.runtime_root
            or self.mode not in {"functional-only", PROCESS_MODE}
        ):
            raise QualificationError("Qualification requires an exact original scope, source and runtime root.")
        if (
            self.admitted or self.work_completed or self.worker_reaped or self.worker_handle_closed
            or self.job_zero_observed or self.job_handle_closed or self.forced_termination
            or self.lifecycle_settled or self.registry_unchanged or self.native_exit is not None
            or self.receiver is not None or self.capture.readers
        ):
            raise QualificationError("Qualification observation sink was already used; no replay.")
        if self.work_receipt.exists():
            raise QualificationError("Original work-completion evidence already exists; no replay.")
        self.boundary.require(clock, work=True)
        self.admitted = True

    def observe_work(self, clock: Callable[[], float] = time.monotonic) -> bool:
        path = self.work_receipt
        if not path.exists():
            if self.work_completed:
                raise QualificationError("Previously observed work-completion evidence disappeared.")
            return False
        if path.resolve() != path or not path.is_file() or path.stat().st_size > 16384:
            raise QualificationError("Work-completion evidence is redirected or unbounded.")
        with path.open("rb") as source:
            data = source.read(16385)
        digest = hashlib.sha256(data).hexdigest()
        if self.work_completed:
            if digest != self.work_receipt_sha256:
                raise QualificationError("Previously observed work-completion evidence changed.")
            self.boundary.require(clock)
            return True
        value = decode_object(data)
        observed = self.boundary.require(clock)
        if (
            set(value) != {
                "schema", "version", "scope", "manifest_sha256", "monotonic_start",
                "completed_monotonic", "candidate_exit", "workers_exited",
            }
            or value["schema"] != WORK_SCHEMA or type(value["version"]) is not int
            or value["version"] != 1 or type(value["scope"]) is not int or value["scope"] != self.scope
            or value["manifest_sha256"] != self.manifest_sha256
            or type(value["monotonic_start"]) not in (int, float)
            or value["monotonic_start"] != self.boundary.start
            or type(value["completed_monotonic"]) not in (int, float)
            or not math.isfinite(value["completed_monotonic"])
            or not self.boundary.start <= value["completed_monotonic"] <= observed
            or value["completed_monotonic"] >= self.boundary.start + WORK_SECONDS
            or type(value["candidate_exit"]) is not int or value["candidate_exit"] != 0
            or value["workers_exited"] is not True
        ):
            raise QualificationError("Work-completion phase is late, incomplete or bound to another source.")
        self.work_completed = True
        self.work_receipt_sha256 = digest
        return True

    def phase_remaining(self, clock: Callable[[], float] = time.monotonic) -> float:
        self.observe_work(clock)
        return self.boundary.remaining(work=not self.work_completed, clock=clock)

    def observe_receiver(self, clock: Callable[[], float] = time.monotonic) -> None:
        value = decode_object(bytes(self.capture.streams[0].data))
        if (
            value.get("kind") != "native-qualification-receiver"
            or value.get("evidence_phase") != DESCENDANT_PHASE or value.get("passed") is not True
            or value.get("original_job_drained") is not False
            or value.get("original_owner_settlement_required") is not True
            or type(value.get("execution_scope")) is not int or value["execution_scope"] != self.scope
            or value.get("manifest_sha256") != self.manifest_sha256
            or type(value.get("monotonic_start")) not in (int, float)
            or value["monotonic_start"] != self.boundary.start
            or value.get("started_utc") != self.boundary.utc
            or value.get("runtime_root") != str(self.runtime_root)
            or value.get("desktop_qualified") is not False
            or type(value.get("visible_controls_attempted")) is not int
            or value["visible_controls_attempted"] != 0
            or value.get("errors") != []
            or any(value.get(name) is not True for name in (
                "native_exited", "streams_closed", "readers_settled",
                "process_handle_closed", "preparation_sources_unchanged_after",
            ))
            or type(value.get("native_exit")) is not int or value["native_exit"] != 0
        ):
            raise QualificationError("Captured receiver evidence is not a settled provisional descendant phase.")
        observed = self.boundary.require(clock)
        times = [value.get(name) for name in (
            "post_capture_monotonic", "post_artifact_verification_monotonic",
        )]
        if (
            any(type(stamp) not in (int, float) or not math.isfinite(stamp) for stamp in times)
            or not self.boundary.start <= times[0] <= times[1] <= observed
        ):
            raise QualificationError("Receiver capture timestamps are missing, late or inconsistent.")
        captured = value.get("captured_streams")
        if not isinstance(captured, dict) or set(captured) != {"stdout", "stderr"}:
            raise QualificationError("Receiver raw captured streams are missing.")
        raw_streams: dict[str, bytes] = {}
        for name, stream in captured.items():
            if (
                not isinstance(stream, dict) or set(stream) != {"bytes", "sha256", "base64", "complete"}
                or type(stream["bytes"]) is not int or not 0 <= stream["bytes"] <= STREAM_BYTES
                or not isinstance(stream["base64"], str) or stream["complete"] is not True
            ):
                raise QualificationError("Receiver raw capture is incomplete or unbounded.")
            raw = base64.b64decode(stream["base64"], validate=True)
            if len(raw) != stream["bytes"] or hashlib.sha256(raw).hexdigest() != stream["sha256"]:
                raise QualificationError("Receiver raw capture digest or byte count changed.")
            raw_streams[name] = raw
        terminal = decode_terminal(raw_streams["stdout"], self.scope, self.mode)
        if (
            terminal != value.get("terminal") or terminal.get("evidence_phase") != DESCENDANT_PHASE
            or terminal.get("original_job_drained") is not False
            or terminal.get("original_owner_settlement_required") is not True
            or (self.mode == "functional-only" and terminal.get("functional_positive") is not True)
            or (self.mode == PROCESS_MODE and (
                value.get("qualification") != PROCESS_MODE or value.get("desktop_observed") is not False
            ))
            or type(terminal.get("execution_scope")) is not int or terminal["execution_scope"] != self.scope
            or type(terminal.get("monotonic_start")) not in (int, float)
            or terminal["monotonic_start"] != self.boundary.start
        ):
            raise QualificationError("Receiver terminal does not match its raw descendant capture.")
        self.boundary.require(clock)
        self.receiver = value

    def require_settled(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.observe_work(clock)
        self.boundary.require(clock)
        if (
            not self.work_completed or type(self.native_exit) is not int or self.native_exit != 0
            or not self.worker_reaped or not self.worker_handle_closed
            or not self.job_zero_observed or not self.job_handle_closed or self.forced_termination
            or not self.lifecycle_settled or not self.registry_unchanged
            or not self.capture.settled or not self.capture.closed or self.receiver is None
        ):
            raise QualificationError("Original outside-owner settlement is incomplete; no positive result.")


def publish_work_completion(
    qualification: Qualification, *, candidate_exit: int, workers_exited: bool,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Called by the source-qualified stage only after actual work/worker exit."""
    completed = qualification.boundary.require(clock, work=True)
    if type(candidate_exit) is not int or candidate_exit != 0 or workers_exited is not True:
        raise QualificationError("Work completion requires actual successful candidate and worker exits.")
    path = qualification.work_receipt
    if path.parent.resolve() != path.parent or not path.parent.is_dir():
        raise QualificationError("Work completion requires the original ordinary evidence directory.")
    value = {
        "schema": WORK_SCHEMA, "version": 1, "scope": qualification.scope,
        "manifest_sha256": qualification.manifest_sha256,
        "monotonic_start": qualification.boundary.start, "completed_monotonic": completed,
        "candidate_exit": candidate_exit, "workers_exited": workers_exited,
    }
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
    pending = path.with_name(".work-completed.pending")
    with pending.open("xb") as target:
        target.write(data)
        target.flush()
    qualification.boundary.require(clock, work=True)
    # Publish complete bytes atomically without overwriting a previous receipt.
    os.link(pending, path, follow_symlinks=False)
    pending.unlink()
    qualification.boundary.require(clock, work=True)
