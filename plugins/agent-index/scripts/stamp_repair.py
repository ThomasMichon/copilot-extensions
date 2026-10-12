"""Bounded POSIX migration of creator-owned, group-writable stamp artifacts."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


def repairable(info: os.stat_result) -> bool:
    return (
        info.st_uid == os.geteuid()
        and bool(info.st_mode & stat.S_IWGRP)
        and not info.st_mode & (stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
    )


def destinations(root: Path) -> frozenset[Path]:
    home = Path.home()
    return frozenset(Path(os.path.abspath(path)) for path in (
        root, home / ".local", home / ".local/bin",
    ))


def verify_bindings(directory) -> None:
    for parent, path, fd in directory.bindings:
        info = os.fstat(fd)
        entry = os.stat(path, follow_symlinks=False) if parent is None else (
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        )
        if (info.st_dev, info.st_ino) != (entry.st_dev, entry.st_ino):
            raise ValueError(f"stamp directory identity changed: {path}")
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            if path not in directory.repair_paths or not repairable(info):
                if not directory.ancestor_private(info):
                    if path == directory.path:
                        raise ValueError(f"stamp directory is not privately owned by this user: {path}")
                    raise ValueError(f"stamp ancestor permits untrusted mutation: {path}")


def tighten_directories(directory) -> None:
    # First bind and validate the complete namespace, before changing any mode.
    verify_bindings(directory)
    for _, path, fd in directory.bindings:
        info = os.fstat(fd)
        if path in directory.repair_paths and repairable(info):
            verify_bindings(directory)
            os.fchmod(fd, stat.S_IMODE(info.st_mode) & ~stat.S_IWGRP)
            print(f"[agent-index] repaired group-writable stamp directory: {path}", file=sys.stderr)


def installed_launcher() -> bytes:
    return b"""#!/usr/bin/env bash
_name="agent-index"
_root="$HOME/.$_name"
_payload="$(cat "$_root/payload-dir" 2>/dev/null || true)"
_gate="$_payload/scripts/runtime-gate.sh"
if [ ! -f "$_gate" ]; then
    printf '%s\\n' "[$_name] payload lifecycle gate not found. Re-enable the plugin, then retry." >&2
    exit 127
fi
exec "$_gate" "$@"
"""
