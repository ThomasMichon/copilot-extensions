"""Dependency-free, current-state policy for one reversible action attempt.

Native maintenance tools can load this leaf without importing a runtime venv.
Installation selection belongs to the caller, not to this reader.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

MAX_STATE_BYTES = 64 * 1024
ACTIONS = (
    "coordinator-replacement",
    "supervisor-self-update",
    "worker-admission",
)
STATE_NAME = "action-controls.json"


@dataclass(frozen=True)
class ActionDecision:
    action: str
    scope: str
    inhibited: bool
    source: str | None = None
    reason: str | None = None
    error: str | None = None


def _read_object(path: Path, *, missing_ok: bool = True) -> dict:
    """Read bounded ordinary local state; never wait on a pipe or follow a link."""
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError(f"linked action state path: {candidate}")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("state must be a regular file")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ValueError("state is owned by a different user")
        raw = stream.read(MAX_STATE_BYTES + 1)
    if len(raw) > MAX_STATE_BYTES:
        raise ValueError("state exceeds 64 KiB")
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("state must be a JSON object")
    return data


def read_controls(path: Path) -> dict[str, dict]:
    """Validate the entire explicit policy, including inactive records."""
    data = _read_object(path)
    for action, record in data.items():
        if action not in ACTIONS:
            raise ValueError(f"unsupported action: {action}")
        if not isinstance(record, dict) or type(record.get("disabled")) is not bool:
            raise ValueError(f"{action} must have a boolean disabled field")
        if record.get("reason") is not None and not isinstance(record["reason"], str):
            raise ValueError(f"{action} reason must be text")
    return data


def evaluate_action(
    action: str,
    root: Path,
    *,
    shared_bootstrap: Path | None = None,
    shared_bootstrap_fallback: Path | None = None,
) -> ActionDecision:
    """Fresh decision; an invalid explicit policy inhibits effects, not services.

    The optional shared bootstrap source retains its existing fail-open parse
    contract. Callers must supply it only for automatic default-install actions.
    """
    if action not in ACTIONS:
        raise ValueError(f"unsupported action: {action}")
    source = root / STATE_NAME
    try:
        controls = read_controls(source)
    except (OSError, UnicodeError, ValueError) as exc:
        return ActionDecision(action, str(root), True, str(source),
                              "invalid action policy", str(exc))
    record = controls.get(action, {})
    if record.get("disabled") is True:
        return ActionDecision(action, str(root), True, str(source), record.get("reason"))
    if shared_bootstrap is not None:
        shared_source = shared_bootstrap
        try:
            try:
                shared = _read_object(shared_source, missing_ok=False)
            except FileNotFoundError:
                if shared_bootstrap_fallback is None:
                    shared = {}
                else:
                    shared_source = shared_bootstrap_fallback
                    shared = _read_object(shared_source)
            if shared.get("active") is True:
                reason = shared.get("reason")
                return ActionDecision(
                    action, str(root), True, str(shared_source),
                    reason if isinstance(reason, str) else None,
                )
        except (OSError, UnicodeError, ValueError) as exc:
            return ActionDecision(
                action, str(root), False, str(shared_source),
                "invalid shared bootstrap state (legacy fail-open)", str(exc),
            )
    return ActionDecision(action, str(root), False)
