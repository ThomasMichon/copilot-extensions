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
_MAX_RECEIPTS = 16
_STATES = {"launch-uncertain", "mux-created"}


def validate_execution_binding(request: dict, value: object) -> dict:
    """Bind fresh execution evidence to the exact original and target epoch."""
    from . import native_source_context, tracking

    expected = request["expected"]
    if (
        not isinstance(value, dict) or set(value) != {
            "native_session_id", "target_epoch", "source_identity", "native_incarnation",
            "session_incarnation", "head_revision", "owner_revision", "activation_ordinal",
        }
        or value["native_session_id"] != expected["native_session_id"]
        or value["target_epoch"] != expected["target_epoch"]
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


def validate_acknowledgement(request: dict, value: object) -> dict:
    """Require an explicit status attestation in addition to execution evidence."""
    if not isinstance(value, dict) or value.get("status_reconciled") is not True:
        raise ValueError("Invalid restart seed acknowledgement")
    validate_execution_binding(
        request, {key: item for key, item in value.items() if key != "status_reconciled"},
    )
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


def validate_successor(previous: dict, request: dict) -> None:
    """Require a later boot of the exact execution that acknowledged pickup."""
    if previous["version"] != 2:
        raise ValueError("unreconciled-restart launch has no seed acknowledgement")
    old = previous["request"]["expected"]
    expected = request["expected"]
    ack = previous["acknowledgement"]
    if (
        previous["request"]["request_id"] == request["request_id"]
        or any(expected[key] != old[key] for key in (
            "project", "worktree_id", "worktree_path", "native_session_id",
            "worktree_incarnation", "head_revision",
        ))
        or request["source_epoch"] != old["target_epoch"]
        or request["native_incarnation"] != ack["native_incarnation"]
        or expected["source_identity"] != ack["source_identity"]
        or expected["activation_ordinal"] != ack["activation_ordinal"]
        or expected["owner_revision"] < ack["owner_revision"]
    ):
        raise ValueError("unreconciled-restart request does not continue the acknowledged execution")


def _history(value: object) -> list[dict]:
    if isinstance(value, dict) and value.get("schema") == "native-restart-launch-history":
        if (
            set(value) != {"schema", "version", "receipts"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["receipts"], list)
            or not 2 <= len(value["receipts"]) <= _MAX_RECEIPTS
        ):
            raise ValueError("Invalid restart launch history")
        receipts = [_validate(item) for item in value["receipts"]]
    else:
        receipts = [_validate(value)]
    seen_requests: set[str] = set()
    seen_targets: list[dict] = [receipts[0]["request"]["source_epoch"]]
    for index, receipt in enumerate(receipts):
        request = receipt["request"]
        if request["request_id"] in seen_requests or request["expected"]["target_epoch"] in seen_targets:
            raise ValueError("Repeated restart launch history identity")
        if index:
            validate_successor(receipts[index - 1], request)
        seen_requests.add(request["request_id"])
        seen_targets.append(request["expected"]["target_epoch"])
    return receipts


def _read_all(yaml_path: Path) -> list[dict]:
    from .native_restart_launch import decode_json

    path = _path(yaml_path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
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
    receipts = _history(decode_json(payload))
    if any(value["request"]["expected"]["worktree_id"] != yaml_path.stem for value in receipts):
        raise ValueError("Restart launch receipt belongs to a different worktree")
    return receipts


def read(yaml_path: Path) -> dict | None:
    """Read the latest outcome without creating, clearing or evicting state."""
    receipts = _read_all(yaml_path)
    return receipts[-1] if receipts else None


def find(yaml_path: Path, request: dict) -> dict | None:
    """Read one exact retained request; a historical ACK is not current liveness."""
    for receipt in _read_all(yaml_path):
        if receipt["request"]["request_id"] == request["request_id"]:
            if receipt["request"] != request:
                raise ValueError("unreconciled-restart receipt request identity changed")
            return receipt
    return None


def _sync(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _stored(receipts: list[dict]) -> dict:
    return (
        receipts[0] if len(receipts) == 1 else {
            "schema": "native-restart-launch-history", "version": 1, "receipts": receipts,
        }
    )


def _encode(receipts: list[dict]) -> str:
    return json.dumps(_stored(receipts), sort_keys=True, allow_nan=False) + "\n"


def _require_acknowledgement_capacity(receipts: list[dict]) -> None:
    from . import native_source_context, tracking

    value = receipts[-1]
    expected = value["request"]["expected"]
    if any(expected[key] == tracking.MAX_PERSISTED_COUNTER for key in (
        "owner_revision", "activation_ordinal",
    )):
        raise ValueError("Restart reservation has no acknowledgement counter capacity")
    # Size a maximum valid future ACK; this synthetic value is never published.
    pid, birth = 9999999999, "9" * 32
    maximum = {
        **value, "version": 2, "state": "seed-acknowledged", "seed_acknowledged": True,
        "acknowledgement": {
            "native_session_id": expected["native_session_id"],
            "target_epoch": expected["target_epoch"],
            "source_identity": native_source_context.identity(
                expected["native_session_id"], pid, birth, expected["target_epoch"],
            ),
            "native_incarnation": f"{pid}:{birth}", "session_incarnation": "x" * 128,
            "head_revision": expected["head_revision"],
            "owner_revision": tracking.MAX_PERSISTED_COUNTER,
            "activation_ordinal": tracking.MAX_PERSISTED_COUNTER,
            "status_reconciled": True,
        },
    }
    if len(_encode([*receipts[:-1], maximum]).encode("utf-8")) > _MAX_BYTES:
        raise ValueError("Restart acknowledgement exceeds its budget")


def write(yaml_path: Path, value: dict, *, reserve: bool) -> None:
    """Publish under launch admission, syncing file and directory before return.

    Initial publication cannot replace an existing receipt. A crash anywhere
    after reservation leaves uncertainty, not evidence of an absent launch.
    """
    value = _validate(value)
    if reserve and (value["version"] != 1 or value["state"] != "launch-uncertain"):
        raise ValueError("Restart reservation must precede native launch and acknowledgement")
    path = _path(yaml_path)
    receipts = _read_all(yaml_path)
    initial = not receipts
    if reserve:
        if receipts:
            validate_successor(receipts[-1], value["request"])
        if len(receipts) >= _MAX_RECEIPTS:
            raise ValueError("Restart launch history is at capacity; retained outcomes cannot be evicted")
        receipts.append(value)
    else:
        current = receipts[-1] if receipts else None
        if (
            current is None or current["request"] != value["request"]
            or not (
                current["version"] == value["version"] == 1
                and current["state"] == "launch-uncertain" and value["state"] == "mux-created"
                or current["version"] == 1 and value["version"] == 2
            )
        ):
            raise ValueError("Restart receipt transition is not owned")
        receipts[-1] = value
    _history(_stored(receipts))
    if reserve:
        _require_acknowledgement_capacity(receipts)
    payload = _encode(receipts)
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
        if initial:
            os.link(staged, path)
        else:
            os.replace(staged, path)
        # Persist the link removal too: a leftover second link must not make a
        # valid reservation unreadable after a power loss.
        Path(staged).unlink(missing_ok=True)
        for directory in (path.parent, path.parent.parent, yaml_path.parent):
            _sync(directory)
    finally:
        Path(staged).unlink(missing_ok=True)
