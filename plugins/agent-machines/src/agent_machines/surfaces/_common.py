"""Shared surface IO: home resolution, backup-before-write, atomic writes, merges."""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def copilot_home() -> Path:
    return Path(os.path.expanduser("~")) / ".copilot"


def backups_root() -> Path:
    return Path(os.path.expanduser("~")) / ".agent-machines" / "backups"


@dataclass
class SurfaceResult:
    """The outcome of applying one logical surface."""

    surface: str
    file: str
    changed: bool
    dry_run: bool
    applied_keys: list[str] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)
    backup_path: str | None = None
    skipped_reason: str | None = None


def diff_keys(live: dict[str, Any], new: dict[str, Any], keys: list[str]) -> list[dict[str, Any]]:
    """The 'what changes and why' record: before/after for each changed key."""
    out: list[dict[str, Any]] = []
    for key in sorted(set(keys)):
        before, after = live.get(key), new.get(key)
        if before != after:
            out.append({"key": key, "before": before, "after": after})
    return out


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class SurfaceStateError(ValueError):
    """A managed live-state file is malformed and unsafe to change."""


def backup_file(path: Path, stamp: str | None = None) -> Path | None:
    """Copy ``path`` into ``~/.agent-machines/backups/<stamp>/`` before mutation."""
    if not path.exists():
        return None
    stamp = stamp or time.strftime("%Y%m%dT%H%M%S")
    dest_dir = backups_root() / stamp
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    shutil.copy2(path, dest)
    return dest


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def merge_enforce(live: Any, manifest: Any) -> Any:
    """``enforce``: the manifest value wins (deep for nested mappings)."""
    if isinstance(live, dict) and isinstance(manifest, dict):
        out = dict(live)
        for key, val in manifest.items():
            out[key] = merge_enforce(live.get(key), val)
        return out
    return manifest


def merge_floor(live: Any, manifest: Any) -> Any:
    """``ensure-present``: a floor -- add what is missing, never clobber live."""
    if isinstance(live, dict) and isinstance(manifest, dict):
        out = dict(live)
        for key, val in manifest.items():
            out[key] = merge_floor(out[key], val) if key in out else val
        return out
    if isinstance(live, list) and isinstance(manifest, list):
        out = list(live)
        out.extend(item for item in manifest if item not in out)
        return out
    return live if live is not None else manifest
