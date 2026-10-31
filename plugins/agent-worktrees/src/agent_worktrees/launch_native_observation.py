"""Original native-session observations; never prompt acceptance or launch authority."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys

from . import launch_attempt, launch_execution, native_lifecycle_source, native_source_context
from . import locks, status_monitor_runtime, tracking, tracking_write

_FIELDS = {
    "schema", "version", "attempt_id", "resolution_id", "operation_id", "worktree_id",
    "creation_nonce", "native_session_id", "source_identity", "native_pid",
    "native_birth", "native_epoch", "ancestors", "at",
}
_REF = {"attempt_id", "resolution_id", "operation_id"}
_SCHEMA = "worktree-launch-native-observation"


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate native observation field")
        result[key] = value
    return result


def _reference(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw) > 512:
        raise ValueError("Invalid native observation reference")
    value = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(value, dict) or set(value) != _REF:
        raise ValueError("Invalid native observation reference")
    for item in value.values():
        launch_attempt.identity(item)
    return value


def _validate(value: object, attempt_id: str, operation_id: str) -> dict:
    if (
        not isinstance(value, dict) or set(value) != _FIELDS
        or value["schema"] != _SCHEMA or type(value["version"]) is not int
        or value["version"] != 1 or value["attempt_id"] != attempt_id
        or value["operation_id"] != operation_id
    ):
        raise ValueError("Invalid native observation")
    for name in _REF:
        launch_attempt.identity(value[name])
    launch_attempt._details({"worktree_id": value["worktree_id"]})
    if (
        any(c in value["worktree_id"] for c in "/\\")
        or value["worktree_id"] in (".", "..")
        or not isinstance(value["native_session_id"], str)
        or not isinstance(value["source_identity"], str)
        or not isinstance(value["creation_nonce"], str)
        or not 1 <= len(value["creation_nonce"]) <= 512
        or not isinstance(value["at"], str)
        or datetime.fromisoformat(value["at"]).tzinfo is None
    ):
        raise ValueError("Invalid native observation incarnation or timestamp")
    launch_execution.validate_epoch(value["native_epoch"])
    if value["native_epoch"] is None:
        raise ValueError("Unqualified native observation epoch")
    expected = native_source_context.identity(
        value["native_session_id"], value["native_pid"], value["native_birth"],
        value["native_epoch"],
    )
    if value["source_identity"] != expected:
        raise ValueError("Invalid native observation source")
    ancestors = value["ancestors"]
    if not isinstance(ancestors, list) or not 1 <= len(ancestors) <= native_source_context.MAX_ANCESTORS:
        raise ValueError("Invalid native observation ancestry")
    seen = set()
    for item in ancestors:
        if not isinstance(item, dict) or set(item) != {"pid", "start_time"}:
            raise ValueError("Invalid native observation ancestor")
        native_source_context.identity(
            value["native_session_id"], item["pid"], item["start_time"], value["native_epoch"],
        )
        if item["pid"] in seen:
            raise ValueError("Repeated native observation ancestor")
        seen.add(item["pid"])
    if {"pid": value["native_pid"], "start_time": value["native_birth"]} not in ancestors:
        raise ValueError("Native source is absent from original ancestry")
    return value


def read(directory: Path, attempt_id: str, operation_id: str) -> dict:
    """Read one exact observation without acquiring a session or writing state."""
    path = launch_attempt._path(directory, attempt_id, operation_id, native=True)
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Invalid native observation file")
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("Native observation exceeds its budget")
    return _validate(json.loads(raw, object_pairs_hook=_unique), attempt_id, operation_id)


def joined(directory: Path, attempt_id: str, operation_id: str) -> dict:
    """A historical evidence join, not a current liveness or accepted-turn verdict."""
    try:
        execution = launch_execution.read(directory, attempt_id, operation_id)
    except FileNotFoundError:
        return {"status": "unknown", "reason": "execution-receipt-missing"}
    try:
        observation = read(directory, attempt_id, operation_id)
    except FileNotFoundError:
        return {"status": "unknown", "reason": "native-observation-missing"}
    if (
        execution.get("native_epoch") is None
        or execution["native_epoch"] != observation["native_epoch"]
        or any(execution[name] != observation[name] for name in (
            "attempt_id", "resolution_id", "operation_id", "worktree_id", "creation_nonce",
        ))
    ):
        return {"status": "unknown", "reason": "native-execution-identity-unconfirmed"}
    spawn = next((e["details"] for e in execution["events"] if e["phase"] == "process-spawned"), None)
    if not spawn or spawn["boundary"] != "backend-command" or spawn["process_start_time"] is None:
        return {"status": "unknown", "reason": "backend-process-unconfirmed"}
    ancestors = observation["ancestors"]
    backend = {"pid": spawn["pid"], "start_time": spawn["process_start_time"]}
    native = {"pid": observation["native_pid"], "start_time": observation["native_birth"]}
    if backend not in ancestors or ancestors.index(backend) < ancestors.index(native):
        return {"status": "unknown", "reason": "backend-ancestry-unconfirmed"}
    return {
        "status": "native-session-observed",
        "native_session_id": observation["native_session_id"],
        "source_identity": observation["source_identity"],
        "prompt_acceptance": "unknown",
    }


def _apply(args: dict) -> dict:
    try:
        if (
            not isinstance(args, dict) or set(args) != {"tracking_dir", "observation", "witness"}
            or not isinstance(args["tracking_dir"], str) or not args["tracking_dir"]
            or not isinstance(args["observation"], dict)
        ):
            raise ValueError("Invalid native observation request")
        directory = Path(args["tracking_dir"])
        value = _validate(
            args["observation"], args["observation"].get("attempt_id"),
            args["observation"].get("operation_id"),
        )
        source = args["witness"]
        if (
            native_lifecycle_source.start_identity(value["native_session_id"], source) != value["source_identity"]
            or not isinstance(source, dict)
            or source.get("ancestors") != value["ancestors"]
            or any(source.get(name) != item for name, item in value["native_epoch"].items())
        ):
            raise ValueError("Original native observation is unconfirmed")
        execution = launch_execution.read(directory, value["attempt_id"], value["operation_id"])
        if (
            execution.get("native_epoch") != value["native_epoch"]
            or any(execution[name] != value[name] for name in (
                "resolution_id", "worktree_id", "creation_nonce",
            ))
        ):
            raise ValueError("Native execution reference changed")
        record = tracking.load_record(directory / f"{value['worktree_id']}.yaml")
        entry = record.session_entry(value["native_session_id"])
        if (
            record.creation_nonce != value["creation_nonce"]
            or entry is None or sum(
                a.source_identity == value["source_identity"] and not a.source_ambiguous
                for a in entry.activations
            ) != 1
        ):
            raise ValueError("Canonical native registration is unconfirmed")
        path = launch_attempt._path(directory, value["attempt_id"], value["operation_id"], native=True)
        with tracking._RecordLock(
            path.with_suffix(".yaml"), blocking=False, require_sidecar=True,
        ) as guard:
            if not guard.acquired:
                raise ValueError("Native observation is contended")
            try:
                existing = read(directory, value["attempt_id"], value["operation_id"])
            except FileNotFoundError:
                launch_attempt._write(path, value)
            else:
                if {k: v for k, v in existing.items() if k != "at"} != {
                    k: v for k, v in value.items() if k != "at"
                }:
                    raise ValueError("Native observation is already bound")
        return {"recorded": True}
    except (ValueError, OSError, TimeoutError, KeyError, TypeError, AttributeError):
        return {"error": "Could not retain native launch observation"}


def _dispatch(args: dict) -> dict:
    return tracking_write.dispatch(
        "launch_attempt_native_observation", args,
        read_lock_data=lambda: locks.read_lock(status_monitor_runtime._monitor_lock_path()),
        ensure_monitor=None, boot_wait_s=0.0, request_deadline_s=1.5, min_version=1,
    )


def observe_registration(
    record_path: Path, session_id: str, source_identity: str | None, *,
    payload: dict | None, witness: object, resident: bool,
) -> None:
    """Consume only the original hook's hint; no resident ambient lookup."""
    metadata = payload.get("_agentWorktrees") if isinstance(payload, dict) else None
    environment = metadata.get("environment") if isinstance(metadata, dict) else None
    if not isinstance(environment, dict):
        environment = {} if resident else os.environ
    raw = environment.get(launch_execution.NATIVE_HINT_ENV)
    if raw is None:
        return
    try:
        reference = _reference(raw)
        if not source_identity or not isinstance(witness, dict):
            raise ValueError("Native source is unqualified")
        matches = [
            (pid, birth) for token, pid, birth in native_source_context.candidates(session_id, witness)
            if token == source_identity
        ]
        if len(matches) != 1:
            raise ValueError("Native source is ambiguous")
        record = tracking.load_record(record_path)
        result = _dispatch({
            "tracking_dir": str(record_path.parent),
            "witness": witness,
            "observation": {
                "schema": _SCHEMA, "version": 1, **reference,
                "worktree_id": record.worktree_id, "creation_nonce": record.creation_nonce,
                "native_session_id": session_id, "source_identity": source_identity,
                "native_pid": matches[0][0], "native_birth": matches[0][1],
                "native_epoch": {name: witness[name] for name in (
                    "platform", "environment_id", "boot_id",
                )},
                "ancestors": witness["ancestors"], "at": datetime.now(timezone.utc).isoformat(),
            },
        })
        if result != {"recorded": True}:
            raise ValueError("Native observation persistence is unconfirmed")
    except (ValueError, OSError, TimeoutError, KeyError, TypeError, tracking_write.AmbiguousWriteOutcome, KeyboardInterrupt):
        print(
            "[agent-worktrees] Native launch observation is unconfirmed; prompt acceptance remains unknown.",
            file=sys.stderr,
        )


tracking_write.register_verb("launch_attempt_native_observation", _apply, version=1)
