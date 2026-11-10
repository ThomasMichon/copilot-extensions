"""Labeled, disposable scratch directories and a read-only temp-root report.

``labeled_scratch`` creates one timestamped directory per task under the OS
temporary folder (or an explicit root), records a ``scratch-owner.json``
breadcrumb and removes the directory on exit without following links. The
breadcrumb describes ownership; it never grants anyone else cleanup permission.

``scratch_report`` only lists top-level entries of a temporary root. It never
deletes, moves or opens anything except breadcrumbs it finds.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BREADCRUMB = "scratch-owner.json"
SCHEMA = "agent-procutil.scratch-owner/v1"
ROOT_ENV = "AGENT_SCRATCH_ROOT"
# Owner references recorded automatically when the launching environment has them.
OWNER_ENV = {
    "session_id": "COPILOT_AGENT_SESSION_ID",
    "worktree_id": "AGENT_WORKTREES_BIND_WORKTREE_ID",
    "launch_id": "AGENT_WORKTREES_LAUNCH_ID",
}
_PREFIX = re.compile(r"[A-Za-z][A-Za-z.]*(?:-[A-Za-z.]*)*-?\Z")


def scratch_root(root: str | os.PathLike[str] | None = None) -> Path:
    """Resolve an explicit root, ``AGENT_SCRATCH_ROOT`` or the OS temp folder."""
    selected = root if root is not None else os.environ.get(ROOT_ENV) or tempfile.gettempdir()
    path = Path(selected).expanduser()
    if not path.is_absolute():
        raise ValueError("scratch root must be an absolute directory")
    return path


def owner_references(extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    refs: dict[str, Any] = {
        key: os.environ[name] for key, name in OWNER_ENV.items() if os.environ.get(name)
    }
    refs.update(extra or {})
    return refs


def create_labeled_scratch(
    prefix: str,
    purpose: str,
    *,
    owner_refs: Mapping[str, Any] | None = None,
    retention: str | None = None,
    root: str | os.PathLike[str] | None = None,
) -> Path:
    """Create a private timestamped directory and write its ownership breadcrumb."""
    if not _PREFIX.match(prefix):
        raise ValueError("scratch prefix must be an alphabetic dash-separated label")
    if not purpose:
        raise ValueError("scratch purpose is required")
    base = scratch_root(root)
    base.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    label = prefix if prefix.endswith("-") else prefix + "-"
    path = Path(tempfile.mkdtemp(prefix=f"{label}{stamp}-", dir=str(base)))
    record = {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": purpose,
        "retention": retention or "disposable; removed by its creator when the task ends",
        "owner_refs": owner_references(owner_refs),
        "pid": os.getpid(),
    }
    try:
        with (path / BREADCRUMB).open("x", encoding="utf-8") as output:
            json.dump(record, output, indent=2, sort_keys=True)
    except BaseException:
        remove_scratch(path)
        raise
    return path


@contextlib.contextmanager
def labeled_scratch(
    prefix: str,
    purpose: str,
    *,
    owner_refs: Mapping[str, Any] | None = None,
    retention: str | None = None,
    cleanup: bool = True,
    root: str | os.PathLike[str] | None = None,
) -> Iterator[Path]:
    """Yield a labeled scratch directory, removing it on exit unless retained."""
    path = create_labeled_scratch(
        prefix, purpose, owner_refs=owner_refs, retention=retention, root=root,
    )
    try:
        yield path
    finally:
        if cleanup:
            remove_scratch(path)


def _is_link(st: os.stat_result) -> bool:
    attributes = getattr(st, "st_file_attributes", 0)
    return stat.S_ISLNK(st.st_mode) or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


# POSIX cleanup walks by directory descriptor so a directory swapped for a link
# after it was inspected is never followed. Windows instead pins every entered
# directory with a handle that denies delete/rename sharing.
_DIR_FD = os.name != "nt" and {os.open, os.unlink, os.rmdir} <= os.supports_dir_fd and (
    os.scandir in os.supports_fd
)
_READ_FLAGS = os.O_RDONLY | sum(
    getattr(os, name, 0) for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_BINARY", "O_CLOEXEC")
)
_DIR_FLAGS = os.O_RDONLY | sum(
    getattr(os, name, 0) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
)
_BREADCRUMB_LIMIT = 1 << 20


def _win_pin(path: str) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES; share read/write but not delete;
    # OPEN_EXISTING; FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT.
    handle = kernel32.CreateFileW(path, 0x0081, 0x0003, None, 3, 0x02200000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def _pin(parent: int | None, path: str, expected: os.stat_result) -> int:
    """Open a directory without following links and verify it is still ``expected``."""
    if os.name == "nt":
        fd = _win_pin(path)
    elif _DIR_FD and parent is not None:
        fd = os.open(os.path.basename(path), _DIR_FLAGS, dir_fd=parent)
    else:
        fd = os.open(path, _DIR_FLAGS)
    try:
        held = os.fstat(fd)
        if (
            _is_link(held) or not stat.S_ISDIR(held.st_mode)
            or (held.st_dev, held.st_ino) != (expected.st_dev, expected.st_ino)
        ):
            raise OSError("scratch directory changed during cleanup")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _ref(fd: int, path: str, name: str) -> tuple[str, int | None]:
    return (name, fd) if _DIR_FD else (os.path.join(path, name), None)


def _remove_link(target: str, st: os.stat_result, dir_fd: int | None = None) -> None:
    # A directory link (junction or symlink) is removed itself; its target is untouched.
    directory = bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_DIRECTORY)
    first, second = (os.rmdir, os.unlink) if directory else (os.unlink, os.rmdir)
    try:
        first(target, dir_fd=dir_fd)
    except OSError:
        second(target, dir_fd=dir_fd)


def _remove_file(target: str, st: os.stat_result, dir_fd: int | None = None) -> None:
    try:
        os.unlink(target, dir_fd=dir_fd)
    except PermissionError:
        if os.name != "nt" or st.st_mode & stat.S_IWRITE:
            raise
        os.chmod(target, stat.S_IWRITE)
        os.unlink(target)


def _clear(fd: int, path: str, keep: str | None = None) -> bool:
    """Remove a pinned directory's contents; report whether all of them went."""
    complete = True
    with os.scandir(fd if _DIR_FD else path) as entries:
        children = list(entries)
    for entry in children:
        if entry.name == keep:
            continue
        child_path = os.path.join(path, entry.name)
        target, dir_fd = _ref(fd, path, entry.name)
        try:
            st = entry.stat(follow_symlinks=False)
            if os.name == "nt" and stat.S_ISDIR(st.st_mode) and not _is_link(st):
                # Windows DirEntry.stat() leaves st_ino/st_dev zero.
                st = os.lstat(child_path)
            if _is_link(st):
                _remove_link(target, st, dir_fd)
            elif stat.S_ISDIR(st.st_mode):
                child = _pin(fd, child_path, st)
                try:
                    cleared = _clear(child, child_path)
                finally:
                    os.close(child)
                if cleared:
                    os.rmdir(target, dir_fd=dir_fd)
                else:
                    complete = False
            else:
                _remove_file(target, st, dir_fd)
        except FileNotFoundError:
            pass
        except OSError:
            complete = False
    return complete


def _read_regular(target: str, dir_fd: int | None = None) -> bytes | None:
    """Read a small regular file without following a final link or blocking on a FIFO."""
    try:
        fd = os.open(target, _READ_FLAGS, dir_fd=dir_fd)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > _BREADCRUMB_LIMIT:
            return None
        chunks = []
        size = 0
        while chunk := os.read(fd, min(65536, _BREADCRUMB_LIMIT + 1 - size)):
            size += len(chunk)
            if size > _BREADCRUMB_LIMIT:
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def _saved_breadcrumb(fd: int, path: str) -> bytes | None:
    target, dir_fd = _ref(fd, path, BREADCRUMB)
    try:
        if _is_link(os.stat(target, dir_fd=dir_fd, follow_symlinks=False)):
            return None
    except OSError:
        return None
    return _read_regular(target, dir_fd)


def _restore_breadcrumb(path: str, expected: os.stat_result, data: bytes) -> None:
    with contextlib.suppress(OSError):
        fd = _pin(None, path, expected)
        try:
            target, dir_fd = _ref(fd, path, BREADCRUMB)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | sum(
                getattr(os, name, 0) for name in ("O_NOFOLLOW", "O_BINARY", "O_CLOEXEC")
            )
            out = os.open(target, flags, 0o600, dir_fd=dir_fd)
            try:
                os.write(out, data)
            finally:
                os.close(out)
        finally:
            os.close(fd)


def _remove_directory(target: str, st: os.stat_result) -> bool:
    fd = _pin(None, target, st)
    try:
        saved = _saved_breadcrumb(fd, target)
        # The breadcrumb goes last so retained, partly-cleaned scratch stays labeled.
        if not _clear(fd, target, keep=BREADCRUMB):
            return False
        name, dir_fd = _ref(fd, target, BREADCRUMB)
        if saved is not None:
            os.unlink(name, dir_fd=dir_fd)
        else:
            try:
                os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                # Without a saved copy, a failed rmdir could not restore it.
                return False
    finally:
        # Windows cannot remove a directory while this pin is open.
        os.close(fd)
    try:
        os.rmdir(target)
    except FileNotFoundError:
        return True
    except OSError:
        if saved is not None:
            _restore_breadcrumb(target, st, saved)
        return False
    return True


def remove_scratch(path: str | os.PathLike[str]) -> bool:
    """Best-effort removal that never follows symlinks, junctions or reparse points.

    Returns whether the path is gone. Incomplete cleanup keeps the root
    ``scratch-owner.json`` breadcrumb so the retained directory stays labeled.
    """
    target = os.fspath(path)
    # A trailing separator or dot component makes lstat follow a directory link.
    separators = (os.sep,) if os.altsep is None else (os.sep, os.altsep)
    if not target or target.endswith(separators) or os.path.basename(target) in (".", ".."):
        return False
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        if _is_link(st):
            _remove_link(target, st)
        elif stat.S_ISDIR(st.st_mode):
            if not _remove_directory(target, st):
                return False
        else:
            _remove_file(target, st)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return not os.path.lexists(target)


def prefix_of(name: str) -> str:
    """Normalize a temp entry name to the producer label before its random suffix."""
    if "-" not in name:
        if name.startswith("tmp"):
            return "tmp*"
        match = re.match(r"[A-Za-z.]+(?=[0-9_])", name)
        return match.group(0) + "*" if match else name
    parts = name.split("-")
    kept: list[str] = []
    for part in parts[:-1]:
        if not part or re.search(r"[0-9_]", part):
            break
        kept.append(part)
    return "-".join(kept) + "-*" if kept else name


def _tree_bytes(path: str) -> int:
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if _is_link(st):
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(entry.path)
                    else:
                        total += st.st_size
        except OSError:
            continue
    return total


def _breadcrumb(path: str) -> dict[str, Any] | None:
    crumb = os.path.join(path, BREADCRUMB)
    try:
        if _is_link(os.lstat(crumb)):
            return None
    except OSError:
        return None
    raw = _read_regular(crumb)
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def scratch_report(
    root: str | os.PathLike[str] | None = None, *, now: float | None = None,
) -> list[dict[str, Any]]:
    """Group top-level entries by producer prefix; read-only, never deletes."""
    base = os.fspath(scratch_root(root))
    current = time.time() if now is None else now
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    with os.scandir(base) as entries:
        children = list(entries)
    for entry in children:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        link = _is_link(st)
        kind = "link" if link else "dir" if stat.S_ISDIR(st.st_mode) else "file"
        key = (prefix_of(entry.name), kind)
        group = groups.setdefault(key, {
            "prefix": key[0], "kind": kind, "count": 0, "labeled": 0, "unlabeled": 0,
            "bytes": 0, "oldest_age_days": 0.0, "newest_age_days": None, "purposes": {},
        })
        group["count"] += 1
        crumb = _breadcrumb(entry.path) if kind == "dir" else None
        if crumb is not None:
            group["labeled"] += 1
            purpose = str(crumb.get("purpose") or "unspecified")
            group["purposes"][purpose] = group["purposes"].get(purpose, 0) + 1
        else:
            group["unlabeled"] += 1
        group["bytes"] += _tree_bytes(entry.path) if kind == "dir" else (0 if link else st.st_size)
        age = max(0.0, (current - st.st_mtime) / 86400)
        group["oldest_age_days"] = round(max(group["oldest_age_days"], age), 2)
        newest = group["newest_age_days"]
        group["newest_age_days"] = round(age if newest is None else min(newest, age), 2)
    return sorted(groups.values(), key=lambda item: (-item["count"], item["prefix"], item["kind"]))


def _format(groups: list[dict[str, Any]]) -> str:
    header = f"{'prefix':<40} {'kind':<5} {'count':>6} {'labeled':>8} {'unlabeled':>9} "
    header += f"{'bytes':>14} {'oldest(d)':>10} {'newest(d)':>10}"
    lines = [header]
    for group in groups:
        lines.append(
            f"{group['prefix'][:40]:<40} {group['kind']:<5} {group['count']:>6} "
            f"{group['labeled']:>8} {group['unlabeled']:>9} {group['bytes']:>14} "
            f"{group['oldest_age_days']:>10} {group['newest_age_days']:>10}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent_procutil.scratch",
        description="Report-only view of a temporary root; never deletes anything.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="group temp entries by producer prefix")
    report.add_argument("--root", default=None, help="directory to inspect (default: OS temp)")
    report.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    groups = scratch_report(args.root)
    print(json.dumps(groups, indent=2) if args.json else _format(groups))
    return 0


if __name__ == "__main__":
    sys.exit(main())
