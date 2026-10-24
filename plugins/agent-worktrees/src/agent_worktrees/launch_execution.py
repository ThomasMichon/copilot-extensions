"""Durable, prompt-free observations, not native acceptance or retry authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
from uuid import uuid4

from . import launch_attempt, locks, tracking, tracking_write

_SCHEMA = "worktree-launch-execution"
_PHASES = {"execution-requested", "seed-claimed", "process-spawned", "execution-returned", "unknown"}
_FIELDS = {
    "schema", "version", "attempt_id", "resolution_id", "operation_id", "parent_id",
    "worktree_id", "creation_nonce", "events",
}
_ERRORS = (ValueError, OSError, TimeoutError, tracking_write.AmbiguousWriteOutcome)


@dataclass(frozen=True)
class Correlation:
    attempt_id: str
    resolution_id: str
    parent_id: str | None = None

    def __post_init__(self):
        launch_attempt.identity(self.attempt_id)
        launch_attempt.identity(self.resolution_id)
        if self.parent_id is not None:
            launch_attempt.identity(self.parent_id)

    def options(self) -> list[str]:
        return [
            "--launch-attempt-id", self.attempt_id,
            "--launch-resolution-id", self.resolution_id,
            *(["--launch-parent-id", self.parent_id] if self.parent_id else []),
        ]


def _details(phase: str, value: object) -> None:
    fields = {
        "execution-requested": set(),
        "seed-claimed": {"seed_id", "handoff_id"},
        "process-spawned": {"pid", "process_start_time", "boundary"},
        "execution-returned": {"exit_code", "process_exit_code", "receipt_uncertain"},
        "unknown": set(),
    }
    if (
        not isinstance(value, dict) or not isinstance(phase, str)
        or phase not in fields or set(value) != fields[phase]
    ):
        raise ValueError("Invalid launch execution metadata")
    if phase == "seed-claimed":
        launch_attempt._details({"seed_id": value["seed_id"]})
        launch_attempt.identity(value["handoff_id"])
    elif phase == "process-spawned":
        birth = value["process_start_time"]
        if (
            type(value["pid"]) is not int or value["pid"] < 1
            or value["boundary"] not in ("launcher-command", "backend-command")
            or (birth is not None and (
                not isinstance(birth, str) or not birth or len(birth) > 128
                or re.fullmatch(r"(?:0|[1-9][0-9]*)(?::(?:0|[1-9][0-9]{0,5}))?", birth) is None
            ))
        ):
            raise ValueError("Invalid launch execution process")
    elif phase == "execution-returned":
        launch_attempt._details({"exit_code": value["exit_code"]})
        process_exit = value["process_exit_code"]
        if (
            (process_exit is not None and type(process_exit) is not int)
            or type(value["receipt_uncertain"]) is not bool
        ):
            raise ValueError("Invalid launch execution outcome")


def read(directory: Path, attempt_id: str, operation_id: str) -> dict:
    """Inspect exactly one execution without creating or repairing state."""
    path = launch_attempt._path(directory, attempt_id, operation_id, execution=True)
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Invalid launch execution file")
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("Launch execution exceeds its budget")
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate launch execution field")
            value[key] = item
        return value
    return _validate(json.loads(raw, object_pairs_hook=unique_object), attempt_id, operation_id)


def _validate(value: object, attempt_id: str, operation_id: str) -> dict:
    if (
        not isinstance(value, dict) or set(value) != _FIELDS
        or value["schema"] != _SCHEMA or type(value["version"]) is not int or value["version"] != 1
        or value["attempt_id"] != attempt_id or value["operation_id"] != operation_id
        or not isinstance(value["events"], list) or not 1 <= len(value["events"]) <= 4
    ):
        raise ValueError("Invalid launch execution state")
    Correlation(value["attempt_id"], value["resolution_id"], value["parent_id"])
    launch_attempt.identity(value["operation_id"])
    launch_attempt._details({"worktree_id": value["worktree_id"]})
    nonce = value["creation_nonce"]
    if nonce is not None and (not isinstance(nonce, str) or not nonce or len(nonce) > 512):
        raise ValueError("Invalid launch execution incarnation")
    phases = []
    for event in value["events"]:
        if not isinstance(event, dict) or set(event) != {"at", "phase", "details"}:
            raise ValueError("Invalid launch execution event")
        phase = event["phase"]
        if not isinstance(phase, str) or phase not in _PHASES:
            raise ValueError("Invalid launch execution phase")
        _details(phase, event["details"])
        if not isinstance(event["at"], str) or datetime.fromisoformat(event["at"]).tzinfo is None:
            raise ValueError("Invalid launch execution timestamp")
        phases.append(phase)
    if (
        phases[0] != "execution-requested" or len(set(phases)) != len(phases)
        or any(p in phases[:-1] for p in ("execution-returned", "unknown"))
        or ("seed-claimed" in phases and "process-spawned" in phases
            and phases.index("seed-claimed") > phases.index("process-spawned"))
    ):
        raise ValueError("Invalid launch execution sequence")
    return value


def _apply(args: dict) -> dict:
    try:
        if (
            not isinstance(args, dict)
            or set(args) != {"subject", "phase", "details", "tracking_dir"}
            or not isinstance(args["tracking_dir"], str) or not args["tracking_dir"]
        ):
            raise ValueError("Invalid launch execution append")
        value, phase, details = args["subject"], args["phase"], args["details"]
        if (
            not isinstance(value, dict) or set(value) != _FIELDS - {"events"}
            or not isinstance(phase, str) or phase not in _PHASES
        ):
            raise ValueError("Invalid launch execution append")
        _details(phase, details)
        directory = Path(args["tracking_dir"])
        path = launch_attempt._path(
            directory, value["attempt_id"], value["operation_id"], execution=True,
        )
        launch_attempt._mkdir(path.parent)
        with tracking._RecordLock(path.with_suffix(".yaml"), require_sidecar=True):
            try:
                current = read(directory, value["attempt_id"], value["operation_id"])
            except FileNotFoundError:
                if phase != "execution-requested":
                    raise ValueError("No initial launch execution receipt") from None
                current = {**value, "events": []}
            if {k: v for k, v in current.items() if k != "events"} != value:
                raise ValueError("Launch execution identity changed")
            current["events"].append({
                "at": datetime.now(timezone.utc).isoformat(), "phase": phase, "details": details,
            })
            _validate(current, value["attempt_id"], value["operation_id"])
            launch_attempt._write(path, current)
        return {"recorded": True}
    except (ValueError, OSError, TimeoutError):
        return {"error": "Could not retain launch execution receipt"}


class Execution:
    def __init__(self, record_path: Path, correlation: Correlation):
        record = tracking.load_record(record_path)
        self.directory = record_path.parent
        self.subject = {
            "schema": _SCHEMA, "version": 1, "attempt_id": correlation.attempt_id,
            "resolution_id": correlation.resolution_id, "operation_id": uuid4().hex,
            "parent_id": correlation.parent_id, "worktree_id": record.worktree_id,
            "creation_nonce": record.creation_nonce,
        }
        self.failed = False
        self.process_exit_code: int | None = None

    def append(self, phase: str, **details) -> None:
        from .launch_seed_state import _dispatch

        if phase == "execution-returned":
            details.update(process_exit_code=self.process_exit_code, receipt_uncertain=self.failed)
        result = _dispatch("launch_attempt_execution", {
            "tracking_dir": str(self.directory), "subject": self.subject,
            "phase": phase, "details": details,
        }, min_version=1)
        if result != {"recorded": True}:
            raise ValueError("Could not retain launch execution receipt")

    def observed(self, phase: str, **details) -> None:
        try:
            self.append(phase, **details)
        except (*_ERRORS, KeyboardInterrupt):
            self.failed = True
            print("Launch execution receipt is unconfirmed; do not automatically retry.", file=sys.stderr)

    def child(self) -> Correlation:
        return Correlation(
            self.subject["attempt_id"], self.subject["resolution_id"], self.subject["operation_id"],
        )

    def spawned(self, pid: int, *, invoke: bool) -> None:
        try:
            birth = locks.process_start_time(pid)
        except KeyboardInterrupt:
            birth = None
            self.failed = True
            print("Launch process identity is unconfirmed; retaining child wait ownership.", file=sys.stderr)
        self.observed(
            "process-spawned", pid=pid, process_start_time=birth,
            boundary="backend-command" if invoke else "launcher-command",
        )


def run(record_path: Path, correlation: Correlation, work) -> int:
    execution = Execution(record_path, correlation)
    try:
        execution.append("execution-requested")
    except _ERRORS:
        print("Launch execution receipt is unconfirmed; no command started.", file=sys.stderr)
        return 3
    try:
        result = work(execution)
    except (*_ERRORS, KeyboardInterrupt):
        execution.observed("unknown")
        raise
    execution.observed("execution-returned", exit_code=result)
    return result if result or not execution.failed else 3


tracking_write.register_verb("launch_attempt_execution", _apply, version=1)
