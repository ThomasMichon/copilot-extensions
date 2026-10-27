"""Prompt-free resolver receipts; correlation never authorizes execution or replay."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg
from . import output, tracking, tracking_write

_SCHEMA = "worktree-launch-resolution"
_PHASES = {"resolution-requested", "plan-ready", "forwarded", "remote-admission-returned", "unknown"}
_IDS = {"worktree_id", "seed_id", "request_id"}
_MAX_BYTES = 16384


def identity(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValueError("Invalid launch attempt identity")
    return value


def required(args, work: Callable[[], int]) -> int:
    # Structured target admission already owns a durable v2 request receipt.
    if getattr(args, "launch_request_b64", None) is not None:
        return work()
    try:
        identity(getattr(args, "launch_attempt_id", None))
        if (
            getattr(args, "dry_run", False)
            or getattr(args, "launch_request_status", None) is not None
            or sum(bool(getattr(args, name, None)) for name in ("new_worktree", "worktree_id", "base")) != 1
        ):
            raise ValueError("Launch attempt receipts require a non-preview explicit launch selector")
    except ValueError as exc:
        return output._json_error(str(exc), exit_code=2)
    return resolve(args, work)


def _details(value: object) -> dict:
    if not isinstance(value, dict) or set(value) - (_IDS | {"exit_code"}):
        raise ValueError("Invalid launch resolution metadata")
    for key, item in value.items():
        if key == "exit_code":
            if type(item) is not int:
                raise ValueError("Invalid launch resolution exit code")
        elif (
            not isinstance(item, str) or not 1 <= len(item) <= 512
            or any(ord(char) < 33 or ord(char) == 127 for char in item)
        ):
            raise ValueError("Invalid launch resolution identifier")
    return value


def _path(
    directory: Path, attempt_id: str, operation_id: str, *,
    execution: bool = False, native: bool = False,
) -> Path:
    if execution and native:
        raise ValueError("Conflicting launch receipt kinds")
    suffix = ".native.json" if native else ".execution.json" if execution else ".json"
    path = directory / "launch-attempts" / identity(attempt_id) / (identity(operation_id) + suffix)
    for item in (path, *path.parents):
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("Indirect launch resolution state")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ValueError("Invalid launch resolution state file")
    return path


def read(directory: Path, attempt_id: str, operation_id: str) -> dict:
    """Read one exact resolver operation without creating state."""
    path = _path(directory, attempt_id, operation_id)
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Invalid launch resolution state file")
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("Launch resolution state exceeds its budget")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "version", "attempt_id", "operation_id", "events"}
        or value["schema"] != _SCHEMA or type(value["version"]) is not int or value["version"] != 1
        or value["attempt_id"] != attempt_id or value["operation_id"] != operation_id
        or not isinstance(value["events"], list) or not 1 <= len(value["events"]) <= 2
    ):
        raise ValueError("Invalid launch resolution state")
    for index, event in enumerate(value["events"]):
        if (
            not isinstance(event, dict) or set(event) != {"at", "phase", "details"}
            or not isinstance(event["phase"], str) or event["phase"] not in _PHASES
            or (event["phase"] == "resolution-requested") != (index == 0)
            or not isinstance(event["at"], str)
        ):
            raise ValueError("Invalid launch resolution event")
        try:
            at = datetime.fromisoformat(event["at"])
        except ValueError as exc:
            raise ValueError("Invalid launch resolution timestamp") from exc
        if at.tzinfo is None:
            raise ValueError("Invalid launch resolution timestamp")
        _details(event["details"])
    return value


def _write(path: Path, value: dict) -> None:
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > _MAX_BYTES:
        raise ValueError("Launch resolution state exceeds its budget")
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        tracking._replace_with_retry(temporary, str(path))
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sync_directory(directory: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _mkdir(directory: Path) -> None:
    missing = []
    candidate = directory
    while not candidate.exists():
        missing.append(candidate)
        candidate = candidate.parent
    directory.mkdir(parents=True, exist_ok=True)
    for item in missing:
        _sync_directory(item)
        _sync_directory(item.parent)


def _apply(args: dict) -> dict:
    try:
        directory = Path(args["tracking_dir"])
        attempt_id, operation_id = identity(args["attempt_id"]), identity(args["operation_id"])
        phase, details = args["phase"], _details(args["details"])
        if not isinstance(phase, str) or phase not in _PHASES:
            raise ValueError("Invalid launch resolution phase")
        path = _path(directory, attempt_id, operation_id)
        _mkdir(path.parent)
        with tracking._RecordLock(path.with_suffix(".yaml"), require_sidecar=True):
            try:
                value = read(directory, attempt_id, operation_id)
            except FileNotFoundError:
                if phase != "resolution-requested":
                    raise ValueError("Launch resolution has no initial receipt") from None
                value = {
                    "schema": _SCHEMA, "version": 1, "attempt_id": attempt_id,
                    "operation_id": operation_id, "events": [],
                }
            if value["events"] and (phase == "resolution-requested" or len(value["events"]) == 2):
                raise ValueError("Launch resolution operation was already recorded")
            value["events"].append({
                "at": datetime.now(timezone.utc).isoformat(), "phase": phase, "details": details,
            })
            _write(path, value)
        return {"recorded": True}
    except (ValueError, OSError, TimeoutError):
        return {"error": "Could not retain launch resolution receipt"}


def _append(directory: Path, attempt_id: str, operation_id: str, phase: str, **details) -> None:
    from .launch_seed_state import _dispatch

    result = _dispatch("launch_attempt_resolution", {
        "tracking_dir": str(directory), "attempt_id": attempt_id, "operation_id": operation_id,
        "phase": phase, "details": details,
    }, min_version=1)
    if result != {"recorded": True}:
        raise ValueError("Could not retain launch resolution receipt")


def resolve(args, work: Callable[[], int]) -> int:
    """Save intent before effects and bind the actual returned identities before publication."""
    attempt_id = identity(args.launch_attempt_id)
    operation_id = uuid.uuid4().hex
    directory = cfg.tracking_dir()
    try:
        _append(directory, attempt_id, operation_id, "resolution-requested")
    except (ValueError, OSError, TimeoutError, tracking_write.AmbiguousWriteOutcome):
        return output._json_error("Launch receipt is unconfirmed; no resolution started", exit_code=3)
    outcome_attempted = False
    try:
        with output.capture_json_output() as captured:
            result = work()
        plan = json.loads(captured.getvalue())
        if not isinstance(plan, dict):
            raise ValueError("Invalid launch plan")
        launch = plan.get("launch") if isinstance(plan.get("launch"), dict) else plan
        command = launch.get("cmd")
        from .launch_seed_exec import native_setup_index

        native_setup = native_setup_index(command) if isinstance(command, list) else None
        if native_setup is not None:
            launch["cmd"] = [
                *command[:native_setup + 1],
                "-LaunchAttemptId", attempt_id, "-LaunchResolutionId", operation_id,
                *command[native_setup + 1:],
            ]
        if (
            isinstance(command, list)
            and command[:3] == [sys.executable, "-m", "agent_worktrees.launch_seed_exec"]
        ):
            from .launch_execution import Correlation

            delimiter = command.index("--", 3)
            launch["cmd"] = [
                *command[:delimiter], *Correlation(attempt_id, operation_id).options(),
                *command[delimiter:],
            ]
        phase = (
            "unknown" if result or "error" in plan or launch.get("action") in {"none", "error"} else
            "remote-admission-returned" if launch.get("action") == "remote" and launch.get("launch_request_id") else
            "forwarded" if launch.get("action") == "remote" else "plan-ready"
        )
        details = {
            target: launch[source]
            for source, target in (
                ("worktree_id", "worktree_id"), ("seed_id", "seed_id"), ("launch_request_id", "request_id"),
            )
            if launch.get(source) is not None
        }
        if "worktree_id" not in details and isinstance(plan.get("worktree"), dict):
            if plan["worktree"].get("id") is not None:
                details["worktree_id"] = plan["worktree"]["id"]
        outcome_attempted = True
        _append(directory, attempt_id, operation_id, phase, exit_code=result, **details)
    except (Exception, KeyboardInterrupt):
        # A completed allocation may exist. Never repeat the resolver or print its
        # possibly prompt-bearing exception to make the audit appear complete.
        if not outcome_attempted:
            try:
                _append(directory, attempt_id, operation_id, "unknown")
            except (ValueError, OSError, TimeoutError, tracking_write.AmbiguousWriteOutcome):
                print("Could not retain the unknown launch outcome.", file=sys.stderr)
        print("Launch resolution outcome is unconfirmed; inspect the saved attempt before retrying.", file=sys.stderr)
        return output._json_error("Launch resolution outcome is unconfirmed", exit_code=3)
    launch["launch_attempt_id"] = attempt_id
    launch["launch_attempt_receipt"] = {
        "version": 1, "operation_id": operation_id, "phase": phase, **details,
    }
    output._json_output(plan)
    return result


tracking_write.register_verb("launch_attempt_resolution", _apply, version=1)
