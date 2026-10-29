"""Memoize positive project identity probes, not activation or policy decisions."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
from pathlib import Path

from .state import PluginStateError, read_json_object, write_json_object_atomic

_LOG = logging.getLogger(__name__)
_VERSION = 1


def _regular(info: os.stat_result, *, directory: bool = False) -> bool:
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    return kind(info.st_mode) and not (
        info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        if hasattr(info, "st_file_attributes") else False
    )


def _owned_directory(path: Path, info: os.stat_result) -> bool:
    if os.name != "nt":
        return info.st_uid == os.geteuid()
    try:
        from agent_procutil._daemon_instance_owner import assert_path_owner
    except ImportError:
        return False
    assert_path_owner(path)
    return True


def identity_inputs(root: Path, expected_remote: str, git: str | None) -> str | None:
    """Return a cheap complete input key only for non-indirect repository layouts.

    Git runs without global/system config or inherited GIT_* overrides. Linked
    gitfiles, common directories, includes and core.worktree can add dependencies
    outside this footprint; those layouts deliberately keep real Git probes.
    """
    if not git:
        return None
    try:
        executable = Path(git).resolve(strict=True)
        executable_info = executable.stat()
        dotgit = root / ".git"
        gitdir = dotgit if os.path.lexists(dotgit) else root
        if os.path.lexists(gitdir / "commondir"):
            return None
        directories = []
        for path in (root, gitdir, gitdir / "objects", gitdir / "refs"):
            info = path.lstat()
            if not _regular(info, directory=True) or not _owned_directory(path, info):
                return None
            directories.append((str(path), info.st_dev, info.st_ino, info.st_mode,
                                info.st_uid, info.st_gid))
        files = []
        for name in ("HEAD", "config", "config.worktree"):
            path = gitdir / name
            try:
                before = path.lstat()
            except FileNotFoundError:
                if name != "config.worktree":
                    return None
                files.append((name, None))
                continue
            if not _regular(before):
                return None
            raw = path.read_bytes()
            after = path.lstat()
            if any(getattr(before, field) != getattr(after, field) for field in (
                "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns",
            )):
                return None
            if name != "HEAD" and (
                b"include" in raw.lower() or re.search(rb"\bworktree\b", raw, re.I)
            ):
                return None
            files.append((name, hashlib.sha256(raw).hexdigest()))
        inputs = [
            _VERSION, str(root), expected_remote, str(executable),
            [executable_info.st_dev, executable_info.st_ino, executable_info.st_size,
             executable_info.st_mtime_ns, executable_info.st_ctime_ns],
            directories, files,
        ]
        return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    except (OSError, ValueError):
        return None  # unsupported/uncertain inputs still take the authoritative probe


def read_identities(path: Path) -> dict[str, str]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {}
    except OSError:
        _LOG.warning("Project identity memo is unreadable: %s", path)
        return {}
    try:
        if not _regular(info):
            raise PluginStateError("memo must be a regular non-reparse file")
        _, data = read_json_object(path)
        if type(data.get("version")) is not int or data["version"] != _VERSION:
            return {}
        verified = data.get("verified")
        if not isinstance(verified, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            or re.fullmatch("[0-9a-f]{64}", value) is None
            for key, value in verified.items()
        ):
            raise PluginStateError("memo verified entries are invalid")
        return verified
    except PluginStateError:
        _LOG.warning("Project identity memo is invalid or unreadable: %s", path)
        return {}


def write_identities(path: Path, verified: dict[str, str]) -> None:
    try:
        write_json_object_atomic(path, {"version": _VERSION, "verified": verified})
    except OSError:
        _LOG.warning("Project identity memo could not be published: %s", path)
