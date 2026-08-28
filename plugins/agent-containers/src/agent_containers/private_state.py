"""Owner-private state permissions and crash-durable JSON publication."""

from __future__ import annotations

import json
import os
import stat
import uuid
from pathlib import Path

_ACL_MODE_FILESYSTEMS = {
    "9p",
    "cifs",
    "drvfs",
    "fuseblk",
    "smb3",
}


def _mount_path(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _filesystem_type_from_mountinfo(path: Path, text: str) -> str | None:
    target = str(path.resolve(strict=False))
    best: tuple[int, str] | None = None
    for line in text.splitlines():
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        fields = left.split()
        after = right.split()
        if len(fields) < 5 or not after:
            continue
        mount_point = _mount_path(fields[4]).rstrip("/") or "/"
        if (
            mount_point != "/"
            and target != mount_point
            and not target.startswith(mount_point + "/")
        ):
            continue
        candidate = (len(mount_point), after[0].lower())
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best[1] if best else None


def filesystem_type(path: Path) -> str | None:
    """Return the backing filesystem type when the platform exposes it."""
    if os.name == "nt":
        return "windows"
    try:
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError:
        return None
    return _filesystem_type_from_mountinfo(path, mountinfo)


def strict_posix_modes(path: Path) -> bool:
    """Whether chmod modes are authoritative on this backing filesystem."""
    if os.name == "nt":
        return False
    fs_type = filesystem_type(path)
    if fs_type is None:
        return True
    return fs_type not in _ACL_MODE_FILESYSTEMS and not fs_type.startswith("fuse.")


def enforce_mode(path: Path, mode: int) -> None:
    """Apply a mode, enforcing it only where POSIX modes are meaningful."""
    strict = strict_posix_modes(path)
    try:
        path.chmod(mode)
    except OSError as exc:
        if strict:
            raise RuntimeError(
                f"Could not enforce mode {mode:04o} on {path}"
            ) from exc
        return
    if strict:
        try:
            actual = stat.S_IMODE(path.stat().st_mode)
        except OSError as exc:
            raise RuntimeError(f"Could not verify permissions on {path}") from exc
        if actual != mode:
            raise RuntimeError(
                f"{path} must have mode {mode:04o}, got {actual:04o}"
            )


def ensure_private_dir(path: Path) -> None:
    """Create or repair an owner-private state directory."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    enforce_mode(path, 0o700)


def fsync_directory(path: Path) -> None:
    """Best-effort directory fsync after atomic publication."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_json(
    path: Path,
    payload: object,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
) -> None:
    """Write owner-only JSON through a unique temp, replace, and fsync parent."""
    ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = -1
    try:
        fd = os.open(
            str(tmp),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            fd = -1
            json.dump(payload, stream, indent=indent, sort_keys=sort_keys)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        enforce_mode(tmp, 0o600)
        os.replace(tmp, path)
        enforce_mode(path, 0o600)
        fsync_directory(path.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        tmp.unlink(missing_ok=True)


def write_json_exclusive(
    path: Path,
    payload: object,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
) -> None:
    """Atomically publish one complete owner-only JSON file without clobber."""
    ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = -1
    try:
        fd = os.open(
            str(tmp),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            fd = -1
            json.dump(payload, stream, indent=indent, sort_keys=sort_keys)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        enforce_mode(tmp, 0o600)
        if os.name == "nt":
            os.rename(tmp, path)
        else:
            os.link(tmp, path)
            tmp.unlink()
        enforce_mode(path, 0o600)
        fsync_directory(path.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        tmp.unlink(missing_ok=True)
