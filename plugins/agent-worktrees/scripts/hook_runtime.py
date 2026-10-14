"""Read-only runtime readiness and resident lifecycle receipts for hooks."""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import struct
from pathlib import Path


def resident_started(runtime: Path, launch_key: str) -> bool:
    receipt = runtime / ".session-context" / f"lifecycle-{launch_key}.json"
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return False
    return (
        isinstance(value, dict)
        and value.get("launchKey") == launch_key
        and value.get("state") in {"started", "completed"}
    )


def complete_runtime_python(runtime: Path, version: str) -> Path | None:
    if not version or "\\" in version or "/" in version:
        return None
    slot = runtime / "versions" / version
    try:
        marker = json.loads(
            (slot / ".install-complete.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(marker, dict) or marker.get("version") != version:
        return None
    for relative in (Path("Scripts") / "python.exe", Path("bin") / "python"):
        executable = slot / relative
        if executable.is_file():
            return executable
    return None


def version_key(version: str) -> tuple:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?", version)
    if match:
        major, minor, patch, dev = match.groups()
        return (0, int(major), int(minor), int(patch), dev is None, int(dev or 0))
    return (1, version.lower())


def runtime_python(runtime: Path) -> Path | None:
    for marker_name in ("current-version", "last-known-good"):
        try:
            version = (runtime / marker_name).read_text("utf-8").strip()
        except (OSError, UnicodeError):
            continue
        executable = complete_runtime_python(runtime, version)
        if executable is not None:
            return executable
    try:
        versions = sorted(
            (path.name for path in (runtime / "versions").iterdir() if path.is_dir()),
            key=version_key,
            reverse=True,
        )
    except OSError:
        return None
    for version in versions:
        executable = complete_runtime_python(runtime, version)
        if executable is not None:
            return executable
    return None


def session_launch_key(payload: dict) -> str:
    metadata = payload.get("_agentWorktrees")
    if not isinstance(metadata, dict):
        return ""
    version = metadata.get("pluginVersion")
    session_id = payload.get("sessionId")
    cwd = payload.get("cwd")
    source = payload.get("source", "")
    timestamp = payload.get("timestamp")
    if (
        not isinstance(version, str) or not version
        or not isinstance(session_id, str) or not session_id
        or not isinstance(cwd, str) or not os.path.isabs(cwd)
        or not isinstance(source, str)
        or isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(timestamp)
    ):
        return ""
    timestamp_text = (
        str(timestamp) if isinstance(timestamp, int)
        else f"f64:{struct.pack('>d', timestamp).hex()}"
    )
    identity = json.dumps(
        [session_id, os.path.realpath(cwd), source, version, timestamp_text],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()
