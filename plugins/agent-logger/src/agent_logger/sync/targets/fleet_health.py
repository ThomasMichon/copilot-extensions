"""Bounded fleet metadata discovery, separate from publication and retention."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from agent_logger.sync.meta import read_sync_meta
from agent_logger.sync.provenance import is_link_or_reparse, windows_extended_path
from agent_logger.sync.targets.base import FleetSyncStatus, SyncStatus
from agent_logger.sync.worktree_state_namespace import is_namespace_directory


@dataclass(frozen=True)
class FleetScanLimits:
    depth: int
    machines: int
    directories: int
    entries: int


class _FleetScanLimitError(OSError):
    pass


def read_fleet_status(root: Path, limits: FleetScanLimits) -> FleetSyncStatus:
    """Read an already validated root without entering session or typed payloads."""
    try:
        machines: dict[str, SyncStatus] = {}
        pending = [(root, Path())]
        visited_directories = 0
        visited_entries = 0
        while pending:
            directory, relative = pending.pop()
            visited_directories += 1
            if visited_directories > limits.directories:
                raise _FleetScanLimitError(
                    f"fleet scan exceeds {limits.directories} directories"
                )
            metadata_path = directory / "sync-meta.json"
            try:
                metadata_mode = os.lstat(windows_extended_path(metadata_path)).st_mode
            except FileNotFoundError:
                metadata_mode = None
            except OSError as exc:
                if not relative.parts:
                    raise
                machines[relative.as_posix()] = SyncStatus(supported=True, error=str(exc))
                if len(machines) > limits.machines:
                    raise OSError(f"fleet metadata exceeds {limits.machines} machines")
                continue
            if relative.parts and metadata_mode is not None and (
                stat.S_ISREG(metadata_mode) and not is_link_or_reparse(metadata_path, metadata_mode)
            ):
                machine = relative.as_posix()
                try:
                    machines[machine] = SyncStatus(
                        supported=True, metadata=read_sync_meta(directory),
                    )
                except OSError as exc:
                    machines[machine] = SyncStatus(supported=True, error=str(exc))
                if len(machines) > limits.machines:
                    raise OSError(f"fleet metadata exceeds {limits.machines} machines")
                continue
            if len(relative.parts) >= limits.depth:
                continue
            children: list[tuple[str, int]] = []
            try:
                with os.scandir(windows_extended_path(directory)) as entries:
                    for entry in entries:
                        visited_entries += 1
                        if visited_entries > limits.entries:
                            raise _FleetScanLimitError(
                                f"fleet scan exceeds {limits.entries} entries"
                            )
                        try:
                            mode = entry.stat(follow_symlinks=False).st_mode
                        except OSError:
                            continue
                        children.append((entry.name, mode))
            except _FleetScanLimitError:
                raise
            except OSError as exc:
                if not relative.parts:
                    raise
                machines[relative.as_posix()] = SyncStatus(supported=True, error=str(exc))
                if len(machines) > limits.machines:
                    raise OSError(f"fleet metadata exceeds {limits.machines} machines")
                continue
            if relative and any(
                name in {"session-state", "provenance", "archived"} for name, _mode in children
            ):
                machines[relative.as_posix()] = SyncStatus(supported=True, metadata=None)
                if len(machines) > limits.machines:
                    raise OSError(f"fleet metadata exceeds {limits.machines} machines")
                continue
            for name, mode in children:
                path = directory / name
                if not stat.S_ISDIR(mode) or is_link_or_reparse(path, mode):
                    continue
                if not relative.parts and is_namespace_directory(root, name):
                    continue
                pending.append((path, relative / name))
        return FleetSyncStatus(supported=True, machines=machines)
    except OSError as exc:
        return FleetSyncStatus(supported=True, error=str(exc))
