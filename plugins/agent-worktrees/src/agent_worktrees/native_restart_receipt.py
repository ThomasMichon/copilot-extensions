"""Private, durable owner receipt; an uncertain native invocation is never retried."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path

from .launch_seed_state import state_path


_MAX_BYTES = 32768
_STATES = {"launch-uncertain", "mux-created"}


def validate_acknowledgement(request: dict, value: object) -> dict:
    """Bind the explicit status attestation to the exact newly observed execution."""
    from . import native_source_context, tracking

    expected = request["expected"]
    if (
        not isinstance(value, dict) or set(value) != {
            "native_session_id", "target_epoch", "source_identity", "native_incarnation",
            "session_incarnation", "head_revision", "owner_revision", "activation_ordinal",
            "status_reconciled",
        }
        or value["native_session_id"] != expected["native_session_id"]
        or value["target_epoch"] != expected["target_epoch"]
        or value["status_reconciled"] is not True
        or type(value["head_revision"]) is not int
        or value["head_revision"] != expected["head_revision"]
        or any(
            type(value[field]) is not int
            or not expected[field] < value[field] <= tracking.MAX_PERSISTED_COUNTER
            for field in ("owner_revision", "activation_ordinal")
        )
        or not isinstance(value["session_incarnation"], str)
        or re.fullmatch(r"[A-Za-z0-9.$_:-]{1,128}", value["session_incarnation"]) is None
        or not isinstance(value["native_incarnation"], str)
        or re.fullmatch(r"[1-9][0-9]{0,9}:[0-9]{1,32}", value["native_incarnation"]) is None
    ):
        raise ValueError("Invalid restart seed acknowledgement")
    pid, birth = value["native_incarnation"].split(":")
    if value["source_identity"] != native_source_context.identity(
        expected["native_session_id"], int(pid), birth, expected["target_epoch"],
    ) or value["source_identity"] == expected["source_identity"]:
        raise ValueError("Restart acknowledgement source is not the resumed execution")
    return value


def _path(yaml_path: Path) -> Path:
    path = state_path(yaml_path).parent / "restart-recovery" / "launch.json"
    for item in (yaml_path, path, *path.parents):
        if item.is_symlink():
            raise ValueError("Restart receipt path is indirect")
    return path


def _validate(value: object) -> dict:
    from .native_restart_launch import validate_request

    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema", "version", "request", "state", "seed_acknowledged",
            "restored", "automatic_eligible",
        } | ({"acknowledgement"} if value.get("version") == 2 else set())
        or value["schema"] != "native-restart-launch"
        or type(value["version"]) is not int or value["version"] not in {1, 2}
        or value["restored"] is not False or value["automatic_eligible"] is not False
    ):
        raise ValueError("Invalid restart launch receipt")
    validate_request(value["request"])
    if value["version"] == 1:
        if (
            not isinstance(value["state"], str) or value["state"] not in _STATES
            or value["seed_acknowledged"] is not False
        ):
            raise ValueError("Invalid restart launch receipt")
    elif value["state"] != "seed-acknowledged" or value["seed_acknowledged"] is not True:
        raise ValueError("Invalid restart seed acknowledgement state")
    else:
        validate_acknowledgement(value["request"], value["acknowledgement"])
    return value


def read(yaml_path: Path) -> dict | None:
    """Read without creating state; corrupt or exposed receipts block recovery."""
    from .native_restart_launch import decode_json

    path = _path(yaml_path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
            or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise ValueError("Restart launch receipt is not private owner state")
        payload = stream.read(_MAX_BYTES + 1)
    if len(payload.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("Restart launch receipt exceeds its budget")
    value = _validate(decode_json(payload))
    if value["request"]["expected"]["worktree_id"] != yaml_path.stem:
        raise ValueError("Restart launch receipt belongs to a different worktree")
    return value


def _sync(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write(yaml_path: Path, value: dict, *, reserve: bool) -> None:
    """Publish under launch admission, syncing file and directory before return.

    Initial publication cannot replace an existing receipt. A crash anywhere
    after reservation leaves uncertainty, not evidence of an absent launch.
    """
    value = _validate(value)
    if reserve and (value["version"] != 1 or value["state"] != "launch-uncertain"):
        raise ValueError("Restart reservation must precede native launch and acknowledgement")
    path = _path(yaml_path)
    payload = json.dumps(value, sort_keys=True, allow_nan=False) + "\n"
    if len(payload.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("Restart launch receipt exceeds its budget")
    path.parent.mkdir(mode=0o700, exist_ok=True)
    metadata = path.parent.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("Restart receipt directory is not private owner state")
    descriptor, staged = tempfile.mkstemp(prefix=".launch-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if reserve:
            os.link(staged, path)
        else:
            current = read(yaml_path)
            if (
                current is None or current["request"] != value["request"]
                or not (
                    current["version"] == value["version"] == 1
                    and current["state"] == "launch-uncertain" and value["state"] == "mux-created"
                    or current["version"] == 1 and value["version"] == 2
                )
            ):
                raise ValueError("Restart receipt transition is not owned")
            os.replace(staged, path)
        # Persist the link removal too: a leftover second link must not make a
        # valid reservation unreadable after a power loss.
        Path(staged).unlink(missing_ok=True)
        for directory in (path.parent, path.parent.parent, yaml_path.parent):
            _sync(directory)
    finally:
        Path(staged).unlink(missing_ok=True)
