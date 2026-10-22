"""Read-only, role-selected artifact integrity preflight; not release authentication."""

import argparse
from contextlib import contextmanager
import hashlib
import os
import stat
import sys
from pathlib import Path

from .planning import MAX_BYTES, PlanError, canonical, decode, digest, record
from .release import ROLES, validate_release

MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


def _identity(info):
    return info.st_dev, info.st_ino


def _content_stamp(info):
    # Windows readers deny writes/deletion; ChangeTime can advance on read-side
    # metadata updates. Python <=3.11 exposes creation time only as st_ctime.
    created = (info.st_birthtime_ns if sys.version_info >= (3, 12) else info.st_ctime_ns) if (
        os.name == "nt"
    ) else info.st_ctime_ns
    return info.st_size, info.st_mtime_ns, created


def _open_artifact(path):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if os.name != "nt":
        return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    # FILE_SHARE_READ only fences existing and new write/delete handles.
    handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, flags)
    except BaseException:
        close(handle)
        raise


@contextmanager
def _reader(path):
    descriptor = _open_artifact(path)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as incoming:
            yield incoming
    finally:
        os.close(descriptor)


def _file_stat(path):
    for entry in reversed((path, *path.parents)):
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PlanError("artifact path contains a link or reparse point")
        if entry != path and not stat.S_ISDIR(info.st_mode):
            raise PlanError("artifact parent is not an ordinary directory")
    if not stat.S_ISREG(info.st_mode):
        raise PlanError("artifact is not an ordinary file")
    return info


def _artifact(path, remaining):
    try:
        path = Path(path)
        if path.drive and not path.root:
            raise PlanError("drive-relative artifact paths are unsupported")
        path = path.absolute()
        before = _file_stat(path)
        if not 1 <= before.st_size <= MAX_ARTIFACT_BYTES or before.st_size > remaining:
            raise PlanError("artifact set exceeds byte bounds or contains an empty artifact")
        with _reader(path) as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before)
                    or opened.st_size != before.st_size):
                raise PlanError("artifact changed during preflight")
            if not 1 <= opened.st_size <= MAX_ARTIFACT_BYTES or opened.st_size > remaining:
                raise PlanError("artifact set exceeds byte bounds or contains an empty artifact")
            hasher = hashlib.sha256()
            size = 0
            while True:
                chunk = stream.read(min(CHUNK_BYTES, MAX_ARTIFACT_BYTES - size + 1,
                                        remaining - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_ARTIFACT_BYTES or size > remaining:
                    raise PlanError("artifact set exceeds byte bounds")
                hasher.update(chunk)
            after = os.fstat(stream.fileno())
            # Compare content timestamps from the same handle, not cached path metadata.
            if _content_stamp(after) != _content_stamp(opened):
                raise PlanError("artifact changed during preflight (descriptor-stamp)")
            if size != opened.st_size:
                raise PlanError("artifact changed during preflight (read-size)")
            if _identity(_file_stat(path)) != _identity(before):
                raise PlanError("artifact changed during preflight (named-identity)")
        return hasher.hexdigest(), size
    except PlanError:
        raise
    except (OSError, ValueError):
        raise PlanError("artifact could not be read as an unchanged ordinary file") from None


def verify_role_artifacts(release, sources, *, role):
    """Hash selected files once; never install, execute, authenticate or authorize."""
    release = decode(canonical(release))
    sources = decode(canonical(sources))
    validate_release(release)
    record(sources, "fleet-manager.artifact-sources", {"components"})
    if role not in ROLES:
        raise PlanError("unsupported installation role")
    files = sources["components"]
    if not isinstance(files, dict) or files.keys() - release["components"].keys():
        raise PlanError("artifact sources must reference declared components")
    for path in files.values():
        if (not isinstance(path, str) or not path or "\0" in path
                or len(path.encode("utf-8")) > 4096):
            raise PlanError("artifact source must be a bounded nonempty path")
    required = sorted(release["roles"][role])
    if set(required) - files.keys():
        raise PlanError("selected role artifact was not supplied")
    results = []
    total = 0
    for name in required:
        actual, size = _artifact(files[name], MAX_TOTAL_BYTES - total)
        if actual != release["components"][name]["artifact_sha256"]:
            raise PlanError("selected role artifact digest mismatch")
        total += size
        results.append({"component": name, "artifact_sha256": actual, "bytes": size})
    result = {
        "schema": "fleet-manager.artifact-preflight", "version": 1,
        "release": release["id"], "release_digest": digest(release), "role": role,
        "status": "integrity-match", "components": results, "total_bytes": total,
        "evidence": "explicit-local-file-bytes", "authenticity_verified": False,
        "activation_authorized": False, "live_readiness_verified": False,
        "mutations_performed": False,
    }
    canonical(result)
    return result


def _read(path):
    try:
        with path.open("rb") as stream:
            return decode(stream.read(MAX_BYTES + 1))
    except OSError:
        raise PlanError("preflight input file could not be read") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--role", choices=ROLES, required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    try:
        result = verify_role_artifacts(_read(args.release), _read(args.sources), role=args.role)
    except PlanError as exc:
        print(f"fleet-manager: {exc}", file=sys.stderr)
        return 1
    print(canonical(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
