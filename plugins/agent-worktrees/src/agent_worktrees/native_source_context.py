"""Read-only original-hook process metadata; importable without the runtime."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from uuid import UUID


MAX_ANCESTORS = 16
_EPOCH_FIELDS = ("platform", "environment_id", "boot_id")


def _text(path: str, limit: int = 4096) -> str:
    with open(path, encoding="ascii") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Native source metadata exceeds its budget")
    return value.strip()


def epoch() -> dict[str, str]:
    """Read Linux/WSL identity, refusing unsupported or container environments."""
    if not sys.platform.startswith("linux"):
        raise ValueError("Native lifecycle source is unqualified on this platform")
    if any(Path(marker).exists() for marker in ("/.dockerenv", "/run/.containerenv")):
        raise ValueError("Container lifecycle sources are unqualified")
    cgroup = _text("/proc/1/cgroup")
    if any(tag in cgroup for tag in ("/docker", "/kubepods", "containerd", "/lxc", "libpod")):
        raise ValueError("Container lifecycle sources are unqualified")
    platform = "wsl" if "microsoft" in _text("/proc/sys/kernel/osrelease").lower() else "linux"
    boot = str(UUID(_text("/proc/sys/kernel/random/boot_id")))
    environment = _text("/etc/machine-id")
    if len(environment) != 32 or any(c not in "0123456789abcdef" for c in environment):
        raise ValueError("Invalid native environment identity")
    if platform == "wsl":
        boot += ":" + process(1)["start_time"]
    return {"platform": platform, "environment_id": environment, "boot_id": boot}


def process(pid: int) -> dict:
    """Read one PID/birth/parent observation, never its command line or environment."""
    fields = _text(f"/proc/{pid}/stat").rsplit(")", 1)[1].split()
    birth = fields[19]
    parent = int(fields[1])
    if not birth.isdecimal() or len(birth) > 32 or parent < 0:
        raise ValueError("Invalid native process identity")
    return {"pid": pid, "start_time": birth, "parent_pid": parent}


def capture() -> dict:
    """Capture the original hook ancestry before crossing any resident boundary."""
    try:
        observed_epoch = epoch()
        ancestors = []
        child = process(os.getpid())
        pid = child["parent_pid"]
        seen = set()
        while pid > 0 and pid not in seen and len(ancestors) < MAX_ANCESTORS:
            seen.add(pid)
            current = process(pid)
            fresh_child = process(child["pid"])
            if (
                fresh_child["start_time"] != child["start_time"]
                or fresh_child["parent_pid"] != pid
                or process(pid)["start_time"] != current["start_time"]
            ):
                raise ValueError("Original hook ancestry changed during capture")
            ancestors.append({"pid": pid, "start_time": current["start_time"]})
            child = current
            pid = current["parent_pid"]
        if not ancestors:
            raise ValueError("Original hook ancestry is unavailable")
        return {"version": 1, "qualified": True, **observed_epoch, "ancestors": ancestors}
    except (OSError, ValueError, IndexError) as exc:
        logging.getLogger(__name__).warning("Native lifecycle source unavailable: %s", exc)
        return {"version": 1, "qualified": False, "reason": "native-source-unavailable"}


def identity(session_id: str, pid: int, start_time: str, observed_epoch: dict) -> str:
    """Bind an opaque source to one native UUID and physical execution epoch."""
    if (
        str(UUID(session_id)) != session_id or type(pid) is not int or pid <= 0
        or not isinstance(start_time, str) or not start_time.isdecimal()
        or not start_time.isascii() or len(start_time) > 32
    ):
        raise ValueError("Invalid native lifecycle process identity")
    value = [observed_epoch[name] for name in _EPOCH_FIELDS]
    value.extend((session_id, pid, start_time))
    digest = hashlib.sha256(json.dumps(value, separators=(",", ":")).encode("ascii")).hexdigest()
    return "native-source-v1:" + digest


def candidates(session_id: str, witness: object) -> list[tuple[str, int, str]]:
    """Derive only captured original sources, never the resident's ancestry."""
    if (
        not isinstance(witness, dict) or type(witness.get("version")) is not int
        or witness["version"] != 1 or witness.get("qualified") is not True
        or {name: witness.get(name) for name in _EPOCH_FIELDS} != epoch()
    ):
        raise ValueError("Original native source epoch is unavailable or changed")
    ancestors = witness.get("ancestors")
    if not isinstance(ancestors, list) or not 1 <= len(ancestors) <= MAX_ANCESTORS:
        raise ValueError("Invalid original native source ancestry")
    result = []
    for item in ancestors:
        if not isinstance(item, dict) or set(item) != {"pid", "start_time"}:
            raise ValueError("Invalid original native source process")
        token = identity(session_id, item["pid"], item["start_time"], witness)
        result.append((token, item["pid"], item["start_time"]))
    if len(set(result)) != len(result):
        raise ValueError("Repeated original native source process")
    return result
