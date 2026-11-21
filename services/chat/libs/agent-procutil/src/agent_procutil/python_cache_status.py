"""Bounded cached observations; never a source of deletion authority."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import time
import uuid
from typing import Any, Sequence, cast

from . import python_cache_files as files
from . import python_cache_permissions as permissions
from . import python_cache_store as store

_NAME = "legacy-prefix-status.json"
_LIMIT = 32768


def read(root: Path) -> dict[str, Any] | None:
    location = store.installation_root(Path(root))
    if not files.present(location):
        return None
    with files.installation(location) as directory:
        try:
            with files.child(directory, _NAME, read=True) as entry:
                permissions.check_file(entry.path)
                value = json.loads(entry.read(_LIMIT))
        except FileNotFoundError:
            return None
    if (
        not isinstance(value, dict) or value.get("schema") != "agent-procutil.python-cache-reclamation"
        or type(value.get("version")) is not int or value["version"] != 1
        or type(value.get("observed_at")) not in (float, int)
        or not math.isfinite(value["observed_at"])
        or not isinstance(value.get("summary"), dict)
    ):
        raise ValueError("cached legacy bytecode observation is invalid")
    for key in ("measured_bytes", "eligible_bytes", "held_bytes", "unmeasured_prefixes"):
        number = value["summary"].get(key)
        if key == "unmeasured_prefixes" and number is None and value.get("inventory_complete") is False:
            continue
        if type(number) is not int or number < 0:
            raise ValueError("cached legacy bytecode measurement is invalid")
    reasons = value.get("reasons", {})
    if not isinstance(reasons, dict) or len(reasons) > 128 or any(
        not isinstance(key, str) or len(key) > 128 or type(count) is not int or count < 0
        for key, count in reasons.items()
    ):
        raise ValueError("cached legacy bytecode reasons are invalid")
    value["observation_age_seconds"] = max(0, time.time() - value["observed_at"])
    return cast(dict[str, Any], value)


def write(root: Path, report: dict[str, Any]) -> None:
    value = {key: item for key, item in report.items() if key != "candidates"}
    value["observed_at"] = time.time()
    encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > _LIMIT:
        raise ValueError("cached legacy bytecode observation exceeds its bound")
    location = store.installation_root(Path(root), create=True)
    with files.installation(location) as directory:
        temporary = location / ("." + uuid.uuid4().hex + ".tmp")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            directory.verify()
            destination = location / _NAME
            if files.present(destination):
                with files.child(directory, _NAME, read=True) as entry:
                    permissions.check_file(entry.path)
                    entry.verify()
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def describe(root: Path, *, argv: Sequence[str]) -> dict[str, Any]:
    """Return a cheap status line and an exact non-destructive inspection command."""
    import shlex
    command = (
        "& " + " ".join("'" + str(arg).replace("'", "''") + "'" for arg in argv)
        if os.name == "nt" else shlex.join(argv)
    )
    try:
        value = read(root)
    except (OSError, ValueError, TypeError):
        return {"observation": "unavailable", "inspect_command": command}
    if value is None:
        return {"observation": "not-measured", "inspect_command": command}
    return {"observation": "cached", "inspect_command": command, **value}
