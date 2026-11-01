"""External bytecode namespaces derived from existing installed build evidence."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import uuid
from pathlib import Path


def _read_regular(path: Path) -> bytes:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
        raise ValueError("cache identity requires ordinary installed evidence")
    data = path.read_bytes()
    after = path.lstat()
    if (
        before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns, before.st_ctime_ns
    ) != (
        after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ):
        raise ValueError("installed cache identity changed while reading")
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate completion field")
        result[key] = value
    return result


def _installed_identity(target: Path) -> str | None:
    marker = target / ".install-complete.json"
    try:
        raw = _read_regular(marker)
        completion = json.loads(raw, object_pairs_hook=_unique_object)
        if (
            not isinstance(completion, dict)
            or set(completion) != {"version", "completed_at", "pid", "payload_hash"}
            or completion["version"] != target.name
            or target.name == "dev"
            or not isinstance(completion["completed_at"], str)
            or not completion["completed_at"]
            or type(completion["pid"]) is not int
            or completion["pid"] < 0
            or not isinstance(completion["payload_hash"], str)
            or re.fullmatch(r"[0-9a-f]{64}", completion["payload_hash"]) is None
        ):
            return None
        locations = [target / "Lib" / "site-packages"]
        locations += sorted((target / "lib").glob("python*/site-packages"))
        records = sorted(path for location in locations for path in location.glob("*.dist-info/RECORD"))
        if not records:
            return None
        digest = hashlib.sha256(raw)
        for record in records:
            if any(
                path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400
                for path in (target, record.parent, record.parent.parent)
            ):
                return None
            recorded = _read_regular(record)
            if re.search(rb",sha256=[A-Za-z0-9_-]{43},[0-9]+\r?(?:\n|\Z)", recorded) is None:
                return None
            digest.update(str(record.relative_to(target)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(recorded)
            digest.update(b"\0")
        if _read_regular(marker) != raw:
            return None
        return digest.hexdigest()
    except (OSError, ValueError, TypeError):
        return None


def cache_arguments(
    root: str | os.PathLike[str], *, runtime: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Keep cache writes external; reuse only a target's recorded installation."""
    owned_root = os.fspath(root)
    if not os.path.isabs(owned_root):
        raise ValueError("Python cache requires an absolute owned runtime root")
    target = Path(sys.prefix if runtime is None else runtime)
    if not target.is_absolute():
        raise ValueError("Python cache requires an absolute target runtime")
    if target.parent.name in {"Scripts", "bin"}:
        target = target.parent.parent
    identity = _installed_identity(target)
    scope = "installed-" + identity if identity is not None else "unsealed-" + uuid.uuid4().hex
    prefix = os.path.normpath(os.path.join(owned_root, "cache", "python", scope))
    if os.name == "nt" and not prefix.startswith("\\\\?\\"):
        prefix = "\\\\?\\UNC\\" + prefix[2:] if prefix.startswith("\\\\") else "\\\\?\\" + prefix
    return ["-X", "pycache_prefix=" + prefix]
