"""Bounded detection of generated session artifacts that should not be synced."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from agent_logger.sync.provenance import (
    is_link_or_reparse,
    windows_extended_path,
)

MAX_DETRITUS_DIRECTORIES = 10_000
MAX_DETRITUS_ENTRIES = 100_000
MAX_DETRITUS_DEPTH = 16
MAX_DETRITUS_ROOTS = 100


@dataclass(frozen=True)
class DetritusSummary:
    """Detected source subtrees and their bounded footprint."""

    roots: tuple[Path, ...] = ()
    file_count: int = 0
    byte_count: int = 0
    measurement_complete: bool = True

    def roots_below(self, prefix: Path) -> tuple[Path, ...]:
        result = []
        for root in self.roots:
            try:
                result.append(root.relative_to(prefix))
            except ValueError:
                continue
        return tuple(result)


def _scan_entries(directory: Path) -> list[tuple[str, Path, int, int]]:
    entries = []
    with os.scandir(windows_extended_path(directory)) as scan:
        for entry in scan:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            path = directory / entry.name
            if is_link_or_reparse(path, info.st_mode):
                continue
            entries.append((entry.name, path, info.st_mode, info.st_size))
    return entries


def _is_chromium_profile_root(
    entries: list[tuple[str, Path, int, int]],
) -> bool:
    root_entries = {
        name: mode
        for name, _path, mode, _size in entries
    }
    if not stat.S_ISREG(root_entries.get("Local State", 0)):
        return False
    for name, path, mode, _size in entries:
        if not stat.S_ISDIR(mode):
            continue
        if name != "Default" and re.fullmatch(r"Profile [0-9]+", name) is None:
            continue
        try:
            profile_entries = {
                child_name: child_mode
                for child_name, _child_path, child_mode, _child_size
                in _scan_entries(path)
            }
        except OSError:
            continue
        if (
            stat.S_ISREG(profile_entries.get("Preferences", 0))
            and stat.S_ISDIR(profile_entries.get("Network", 0))
        ):
            return True
    return False


def _measure_tree(root: Path) -> tuple[int, int, bool]:
    files = 0
    nbytes = 0
    complete = True
    pending = [root]
    directories = 0
    entries_seen = 0
    while pending:
        directory = pending.pop()
        directories += 1
        if directories > MAX_DETRITUS_DIRECTORIES:
            raise OSError(
                f"detritus scan exceeds {MAX_DETRITUS_DIRECTORIES} directories"
            )
        try:
            entries = _scan_entries(directory)
        except OSError:
            complete = False
            continue
        for _name, path, mode, size in entries:
            entries_seen += 1
            if entries_seen > MAX_DETRITUS_ENTRIES:
                raise OSError(
                    f"detritus scan exceeds {MAX_DETRITUS_ENTRIES} entries"
                )
            if stat.S_ISDIR(mode):
                pending.append(path)
            elif stat.S_ISREG(mode):
                files += 1
                nbytes += size
    return files, nbytes, complete


def discover_session_detritus(
    source: Path,
    include_sessions: set[str] | None,
) -> DetritusSummary:
    """Find generated browser profiles under ``session-state/<id>/files``."""
    state = source / "session-state"
    try:
        session_entries = _scan_entries(state)
    except FileNotFoundError:
        return DetritusSummary()

    summaries = []
    for sid, session_path, mode, _size in session_entries:
        if not stat.S_ISDIR(mode):
            continue
        if include_sessions is not None and sid not in include_sessions:
            continue
        summary = discover_session_tree_detritus(session_path)
        prefix = Path("session-state") / sid
        summaries.append(
            DetritusSummary(
                tuple(prefix / root for root in summary.roots),
                summary.file_count,
                summary.byte_count,
                summary.measurement_complete,
            )
        )
    return merge_summaries(*summaries)


def discover_session_tree_detritus(session: Path) -> DetritusSummary:
    """Find generated browser profiles below one session's ``files`` tree."""
    files_root = session / "files"
    try:
        files_mode = os.lstat(windows_extended_path(files_root)).st_mode
    except FileNotFoundError:
        return DetritusSummary()
    if not stat.S_ISDIR(files_mode) or is_link_or_reparse(files_root, files_mode):
        return DetritusSummary()

    pending: list[tuple[Path, int]] = [(files_root, 0)]
    roots: list[Path] = []
    file_count = 0
    byte_count = 0
    measurement_complete = True
    directories = 0
    entries_seen = 0
    while pending:
        directory, depth = pending.pop()
        directories += 1
        if directories > MAX_DETRITUS_DIRECTORIES:
            raise OSError(
                f"detritus discovery exceeds {MAX_DETRITUS_DIRECTORIES} directories"
            )
        entries = _scan_entries(directory)
        entries_seen += len(entries)
        if entries_seen > MAX_DETRITUS_ENTRIES:
            raise OSError(
                f"detritus discovery exceeds {MAX_DETRITUS_ENTRIES} entries"
            )
        if _is_chromium_profile_root(entries):
            roots.append(directory.relative_to(session))
            if len(roots) > MAX_DETRITUS_ROOTS:
                raise OSError(
                    f"detritus discovery exceeds {MAX_DETRITUS_ROOTS} roots"
                )
            files, nbytes, complete = _measure_tree(directory)
            file_count += files
            byte_count += nbytes
            measurement_complete = measurement_complete and complete
            continue
        if depth >= MAX_DETRITUS_DEPTH:
            continue
        for _name, path, mode, _size in entries:
            if stat.S_ISDIR(mode):
                pending.append((path, depth + 1))
    return DetritusSummary(
        tuple(sorted(roots)),
        file_count,
        byte_count,
        measurement_complete,
    )


def merge_summaries(*summaries: DetritusSummary) -> DetritusSummary:
    """Combine independent bounded detections."""
    return DetritusSummary(
        tuple(sorted({root for summary in summaries for root in summary.roots})),
        sum(summary.file_count for summary in summaries),
        sum(summary.byte_count for summary in summaries),
        all(summary.measurement_complete for summary in summaries),
    )


def is_excluded(relative: Path, roots: tuple[Path, ...]) -> bool:
    """Return whether *relative* is at or below a detected detritus root."""
    return any(relative == root or root in relative.parents for root in roots)


def rsync_exclude(root: Path) -> str:
    """Render one anchored rsync filter with literal path components."""
    escaped = root.as_posix().replace("\\", "\\\\")
    for character in ("*", "?", "[", "]"):
        escaped = escaped.replace(character, f"\\{character}")
    return f"--exclude=/{escaped}/***"
