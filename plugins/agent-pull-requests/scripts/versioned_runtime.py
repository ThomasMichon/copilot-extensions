#!/usr/bin/env python3
"""Immutable per-version runtime layout manager (dotfiles #581).

Never mutate a runtime venv in place. Each version installs into its own immutable
directory under ``<root>/versions/<version>``; the active version is published by a
``<root>/current-version`` **plain-text marker file**. Switching versions writes the
marker (atomic temp + rename) and the installer rewrites its **version-pinned
binstubs** (and scheduled task / deploy manifest) to point straight at
``versions/<version>/...`` -- so a running daemon (which already holds its own
immutable files open) is never edited underneath itself, rollback is a marker
rewrite (no rebuild), and the concurrent-venv-mutation race that spawns duplicate
daemons (#123) cannot happen.

On **Windows there is no directory junction at all**. The legacy ``current``/``venv``
junction (a reparse point) was blocked by Windows RedirectionGuard with WinError 448
("untrusted mount point") on managed devices whenever the installer *traversed* it
over a non-interactive logon; a marker file + pinned binstubs need no reparse point
at all, so those machines work with no special-casing and the legacy real-venv fork
is retired. On **POSIX** the active slot is still published by a plain ``venv``/
``.venv`` **symlink** (not a reparse point, never blocked by RedirectionGuard) that
the ``.sh`` binstub, systemd unit, and deploy-manifest resolve *through* -- the
marker is authoritative and the symlink is the stable runtime-facing path.
``current_version`` still falls back to reading an old link target during the
one-time migration.

This is a **stdlib-only** helper deliberately kept *out* of every runtime venv (no
vendored-lib fan-out): the bootstrapping python at install time runs it as
``python versioned_runtime.py <cmd> ...``. It owns only the ``versions/`` +
``current-version`` layout; venv *creation* and package install stay in the
per-plugin installer, which points them at the slot this returns.

Commands (all take ``--root <dir>``; ``--json`` for machine output)::

    slot     <version>              ensure versions/<version> exists; print its path
    activate <version>              publish current-version -> <version> (marker)
    current                         print the active version (from the marker)
    resolve  [--subpath P]          print versions/<current>/<P> (the concrete slot)
    list                            list installed versions (+ which is current)
    gc [--keep V ...] [--protect-pids] [--min-age-days N]
                                    remove version dirs that are not current,
                                    not kept, not (with --protect-pids) held by a
                                    live process running from the slot, and -- if
                                    a positive N is passed -- not younger than N
                                    days (an optional backstop; default off)

Exit code is 0 on success, non-zero on error; errors print to stderr.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

CURRENT_LINK = "current"
VERSIONS_DIR = "versions"
RUNNING_VERSION_FILE = "running-version.json"
# The active version is published as a plain-text marker file (no reparse point),
# replacing the legacy `current`/`venv` directory junction. A marker file is
# never blocked by RedirectionGuard (WinError 448 "untrusted mount point") the
# way traversing a junction is, and the runtime is selected by version-pinned
# binstubs the installer rewrites on cutover -- so no junction is needed at all.
CURRENT_VERSION_FILE = "current-version"
# The last version `activate()` published, stamped atomically alongside the
# marker. It is the fallback resolution target: if the `current-version` marker
# is ever missing/unreadable (a torn or deleted marker), a resolver prefers the
# last-known-good version -- whose slot, having been the active one, still exists
# -- over guessing the newest installed slot. See `resolve_python`.
LAST_KNOWN_GOOD_FILE = "last-known-good"

# The interpreter sub-paths inside a version slot, POSIX then Windows layout.
# The single place the "where is the slot's python" answer lives, so every
# resolver (this module, the shell resolvers, the binstubs) agrees.
SLOT_PYTHON_SUBPATHS = ("bin/python", "Scripts/python.exe")


# --------------------------------------------------------------------------
# Layout helpers
# --------------------------------------------------------------------------

def versions_root(root: Path) -> Path:
    return root / VERSIONS_DIR


def version_dir(root: Path, version: str) -> Path:
    return versions_root(root) / version


def current_link(root: Path, link_name: str = CURRENT_LINK) -> Path:
    return root / link_name


def list_versions(root: Path) -> list[str]:
    """Installed version directory names, sorted (newest-looking last)."""
    vroot = versions_root(root)
    if not vroot.is_dir():
        return []
    names = [p.name for p in vroot.iterdir() if p.is_dir()]
    return sorted(names, key=_version_key)


def _version_key(v: str):
    """Stdlib-only ordering key for supported ``X.Y.Z[-devN]`` versions."""
    supported = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?", v)
    if supported:
        major, minor, patch, dev = supported.groups()
        return (
            0,
            int(major),
            int(minor),
            int(patch),
            1 if dev is None else 0,
            int(dev or 0),
        )
    tokens = re.split(r"(\d+)", v.casefold())
    return (1, tuple((1, int(t)) if t.isdigit() else (0, t) for t in tokens))


# --------------------------------------------------------------------------
# current link: read
# --------------------------------------------------------------------------

def _is_link(link: Path) -> bool:
    """Whether ``link`` is a symlink (POSIX) or a directory junction (Windows).

    A POSIX symlink is caught by ``is_symlink``. A Windows junction is a reparse
    point that reports ``is_symlink() == False`` but carries the
    ``FILE_ATTRIBUTE_REPARSE_POINT`` flag; detect it via ``st_reparse_tag`` /
    the reparse attribute so a *real* directory (a legacy venv) is never mistaken
    for a link.
    """
    try:
        if link.is_symlink():
            return True
    except OSError:
        return False
    if os.name != "nt":
        return False
    try:
        st = os.lstat(link)
    except OSError:
        return False
    FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    tag = getattr(st, "st_reparse_tag", 0)
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(tag) or bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def _link_target(link: Path) -> Path | None:
    """Resolve the ``current`` link's target dir, whether symlink or junction.

    #637: never *traverse* the reparse point. ``Path.exists()``/``Path.resolve()``
    do an ``os.stat`` that follows the junction, which RedirectionGuard
    (PROCESS_MITIGATION_REDIRECTION_TRUST_POLICY) blocks with WinError 448
    ("untrusted mount point") over a non-interactive network logon (e.g. the
    installer run over SSH). Detect the link with lstat (``_is_link``) and read
    its target directly with ``os.readlink`` (handles POSIX symlinks and Windows
    junctions). A non-link -- a real dir at the link path, or absent -- has no
    link target. Returns the absolute target dir, or ``None`` when absent/broken.
    """
    try:
        if not _is_link(link):
            return None
        target = Path(os.readlink(link))
    except OSError:
        return None
    if not target.is_absolute():
        target = (link.parent / target)
    return target


def current_version(root: Path, link_name: str = CURRENT_LINK) -> str | None:
    """The active version name.

    Reads the ``current-version`` marker file -- a plain text file, so it is
    never blocked by RedirectionGuard/WinError 448 the way *traversing* a
    junction is. During migration off the legacy ``current``/``venv`` junction
    model, falls back to reading a still-present junction's target so a
    half-upgraded root still resolves its live version. Only trusts a name whose
    ``versions/<name>`` slot exists.
    """
    name = _read_current_marker(root)
    if name is None:
        # Legacy fallback: a pre-marker root still carrying the old junction.
        target = _link_target(current_link(root, link_name))
        name = target.name if target is not None else None
    if name and version_dir(root, name).exists():
        return name
    return None


def _read_current_marker(root: Path) -> str | None:
    try:
        txt = (root / CURRENT_VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return txt or None


def read_last_known_good(root: Path) -> str | None:
    """The last version ``activate()`` published, or ``None`` if never stamped."""
    try:
        txt = (root / LAST_KNOWN_GOOD_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return txt or None


def _write_last_known_good(root: Path, version: str) -> None:
    """Stamp ``last-known-good`` atomically (temp + os.replace), only-when-changed.

    Best-effort: a failure here never undoes the (already-written) marker.
    """
    if read_last_known_good(root) == version:
        return
    dest = root / LAST_KNOWN_GOOD_FILE
    tmp = dest.with_name(dest.name + f".tmp-{os.getpid()}")
    try:
        tmp.write_text(version + "\n", encoding="utf-8")
        os.replace(tmp, dest)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def slot_python(root: Path, version: str) -> Path | None:
    """The interpreter inside ``versions/<version>``, or ``None`` if not present.

    Checks the POSIX (``bin/python``) then Windows (``Scripts/python.exe``)
    layout, so it resolves correctly on either OS and under git-bash on Windows.
    """
    if not version:
        return None
    vdir = version_dir(root, version)
    for sub in SLOT_PYTHON_SUBPATHS:
        p = vdir / sub
        if p.is_file():
            return p
    return None


def resolve_python(root: Path) -> Path | None:
    """The single canonical way to resolve a versioned runtime's interpreter.

    Junction-free and uniform across every OS and every caller (this module, the
    shell resolvers, the binstubs, service launchers). Resolves in three tiers:

    1. the ``current-version`` marker (the source of truth, written atomically);
    2. ``last-known-good`` -- the last version ``activate()`` published -- when
       the marker is missing/unreadable or names an unresolvable slot;
    3. the newest complete installed slot (true first-run / torn marker).

    Never resolves through a ``venv``/``.venv`` link (a reparse point on Windows
    that RedirectionGuard blocks) and **never** falls back to a PATH python -- an
    unresolved runtime returns ``None`` so the caller degrades deliberately
    (e.g. self-provisions) rather than silently binding the system interpreter.
    Every tier requires a valid completion marker, so a partially built slot is
    never selected merely because it contains an interpreter-shaped file.
    """
    # Tier 1: the current-version marker.
    current = current_version(root) or ""
    if is_complete(root, current):
        p = slot_python(root, current)
        if p is not None:
            return p
    # Tier 2: the last activated version (its slot, having been active, survives).
    lkg = read_last_known_good(root) or ""
    if is_complete(root, lkg):
        p = slot_python(root, lkg)
        if p is not None:
            return p
    # Tier 3: newest complete slot.
    versions = list_versions(root)  # sorted, newest last
    for ver in reversed(versions):
        if is_complete(root, ver):
            p = slot_python(root, ver)
            if p is not None:
                return p
    return None


# --------------------------------------------------------------------------
# current link: write (atomic-ish swap)
# --------------------------------------------------------------------------

def _remove_link(link: Path) -> None:
    """Remove an existing ``current`` link without touching its target contents.

    A symlink/junction is unlinked, never recursed into (so we never delete the
    version dir it points at). ``os.rmdir`` removes a Windows junction; ``unlink``
    removes a POSIX symlink.

    #637: gate on ``_is_link`` (lstat, never traverses) rather than
    ``link.exists()`` -- an ``os.stat`` on a junction is blocked by
    RedirectionGuard with WinError 448 over a non-interactive logon. A non-link
    (absent, or a real dir the caller already moved aside) has no link to remove.
    """
    if not _is_link(link):
        return
    if link.is_symlink():
        link.unlink()
        return
    # Windows junction: it reports as a directory; rmdir removes the reparse point
    # only (leaving the target intact).
    try:
        os.rmdir(link)
    except OSError:
        # Last resort: unlink (some FS report the reparse point as a file).
        link.unlink()


def activate(root: Path, version: str, *, link_name: str = CURRENT_LINK,
             replace_nonlink: bool = False, link_free: bool = False) -> Path:
    """Mark ``versions/<version>`` as the active runtime.

    Always writes the ``current-version`` marker file atomically (temp +
    ``os.replace``) -- the marker is the source of truth on every OS.

    **Windows is structurally junction-free.** A directory junction is a reparse
    point that RedirectionGuard (PROCESS_MITIGATION_REDIRECTION_TRUST_POLICY)
    blocks with WinError 448 ("untrusted mount point") whenever a protected
    process *traverses* it over a non-interactive logon (the mesh-rollout / SSH
    path). So on Windows activate **never creates a junction** -- the runtime is
    selected purely by the marker + the **version-pinned binstubs** the installer
    rewrites on cutover. Any stale legacy ``venv``/``current`` junction is removed
    so it can't shadow the marker or dangle. ``--no-link`` (``link_free``) requests
    the same junction-free behavior on any OS.

    **POSIX keeps a ``venv``/``.venv`` symlink** into the active slot, because the
    ``.sh`` binstub, systemd unit, and deploy-manifest all resolve *through* that
    stable path -- and a POSIX symlink is not a reparse point, so RedirectionGuard
    never applies. A legacy real venv dir at the link path is left as-is unless
    ``replace_nonlink`` moves it aside on first migration.

    Returns the version dir; raises only if the version isn't installed.
    """
    vdir = version_dir(root, version)
    if not vdir.is_dir():
        raise FileNotFoundError(f"version not installed: {vdir}")
    root.mkdir(parents=True, exist_ok=True)

    # Publish the active version atomically (source of truth on every OS).
    dest = root / CURRENT_VERSION_FILE
    tmp = dest.with_name(dest.name + f".tmp-{os.getpid()}")
    tmp.write_text(version + "\n", encoding="utf-8")
    os.replace(tmp, dest)
    # Stamp last-known-good alongside the marker so a resolver can recover the
    # last-active version if the marker is ever torn/absent (resolve_python tier 2).
    _write_last_known_good(root, version)

    link = current_link(root, link_name)

    # Windows (always) and any --no-link caller: junction-free. Drop a stale
    # legacy junction so it can't shadow the marker; a real dir is left for the
    # installer to clean up.
    if os.name == "nt" or link_free:
        if _is_link(link):
            try:
                _remove_link(link)
            except OSError:
                pass
        return vdir

    # POSIX: (re)point the stable `venv`/`.venv` symlink at the slot so the
    # binstub / systemd unit / deploy-manifest resolve through it unchanged.
    # Best-effort -- never let a link failure undo the (already-written) marker.
    try:
        if not _is_link(link) and (link.exists() or link.is_symlink()):
            if not replace_nonlink:
                return vdir  # legacy real dir occupies the path; leave it be
            os.replace(link, link.with_name(f"{link.name}.legacy-{int(time.time())}"))
        _remove_link(link)
        os.symlink(vdir, link, target_is_directory=True)
    except OSError:
        pass
    return vdir


def _detach_file(path: Path) -> tuple[Path, Path] | None:
    """Atomically move ``path`` aside and return ``(detached, original)``."""
    detached = path.with_name(
        f".{path.name}.stale-{os.getpid()}-{time.time_ns()}"
    )
    try:
        os.replace(path, detached)
    except FileNotFoundError:
        return None
    return detached, path


def _discard_detached(pair: tuple[Path, Path] | None) -> None:
    if pair is None:
        return
    try:
        pair[0].unlink()
    except OSError:
        pass


def _restore_detached(pair: tuple[Path, Path] | None) -> None:
    """Restore a detached marker without overwriting a concurrent publisher."""
    if pair is None:
        return
    detached, original = pair
    try:
        os.link(detached, original)
    except FileExistsError:
        pass
    except OSError:
        if not original.exists():
            os.replace(detached, original)
            return
    _discard_detached(pair)


def _detach_marker_reference(
    root: Path, name: str, version: str
) -> tuple[Path, Path] | None:
    """Atomically detach a marker only while it references ``version``."""
    marker = root / name
    try:
        if marker.read_text(encoding="utf-8").strip() != version:
            return None
    except (OSError, UnicodeError):
        return None
    pair = _detach_file(marker)
    if pair is None:
        return None
    try:
        still_references = pair[0].read_text(encoding="utf-8").strip() == version
    except (OSError, UnicodeError):
        still_references = False
    if not still_references:
        _restore_detached(pair)
        return None
    return pair


def slot(root: Path, version: str, *, clean_incomplete: bool = False,
         link_name: str = CURRENT_LINK) -> Path:
    """Ensure ``versions/<version>`` exists (empty is fine) and return it.

    With ``clean_incomplete`` (dotfiles #935): if the slot already exists but is
    NOT marked complete -- a failed/partial/watchdog-killed prior build -- remove
    it first so the caller builds a FRESH venv rather than reusing a corpse via
    ``uv venv --allow-existing`` (which can inherit half-installed packages). A
    complete slot is left intact (idempotent, fast re-run). An incomplete slot
    is protected only while a live process owns it. Otherwise any
    ``current-version`` / ``last-known-good`` references are atomically detached
    before the slot is removed, including when the malformed slot is current.
    """
    vdir = version_dir(root, version)
    if clean_incomplete and vdir.is_dir() and not is_complete(root, version):
        was_current = current_version(root, link_name) == version
        completion_pair = _detach_file(marker_path(root, version))
        detached = [completion_pair]
        detached.extend([
            _detach_marker_reference(root, CURRENT_VERSION_FILE, version),
            _detach_marker_reference(root, LAST_KNOWN_GOOD_FILE, version),
        ])
        detached_completion_is_valid = False
        if completion_pair is not None:
            try:
                detached_completion_is_valid = (
                    validate_marker(_load_unique_json(completion_pair[0]), version)
                    is not None
                )
            except (OSError, UnicodeError, ValueError):
                pass
        if detached_completion_is_valid or is_complete(root, version):
            for pair in reversed(detached):
                _restore_detached(pair)
            return vdir
        if (was_current and not _reliable_process_enumeration()
                and not _recorded_ownership_is_stale(root, version)):
            for pair in reversed(detached):
                _restore_detached(pair)
            raise RuntimeError(
                f"cannot safely clean current incomplete runtime slot "
                f"without reliable process enumeration: {vdir}"
            )
        if version in _versions_with_live_process(root):
            for pair in reversed(detached):
                _restore_detached(pair)
            raise RuntimeError(f"incomplete runtime slot is still in use: {vdir}")
        if not _remove_slot(vdir, label="slot"):
            for pair in reversed(detached):
                _restore_detached(pair)
            raise RuntimeError(f"could not remove incomplete runtime slot: {vdir}")
        for pair in detached:
            _discard_detached(pair)
    vdir.mkdir(parents=True, exist_ok=True)
    return vdir


# --------------------------------------------------------------------------
# GC
# --------------------------------------------------------------------------

def _running_pids(root: Path) -> set[int]:
    """PIDs recorded as live in ``<root>/running-version.json`` (single object or
    a list), filtered to those still alive."""
    path = root / RUNNING_VERSION_FILE
    pids: set[int] = set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return pids
    entries = data if isinstance(data, list) else [data]
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("pid"), int):
            if _pid_alive(e["pid"]):
                pids.add(e["pid"])
    return pids


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
                return bool(ok) and code.value == STILL_ACTIVE
            finally:
                k32.CloseHandle(h)
        except Exception:
            return True  # ambiguous -> assume alive; never GC a maybe-live version
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _pid_image_path(pid: int) -> str | None:
    """Absolute path to the executable image backing ``pid``, or None.

    Windows: ``QueryFullProcessImageNameW`` on a limited-query handle. POSIX:
    ``/proc/<pid>/exe``. Best-effort -- any failure (process gone, access
    denied, unsupported platform) returns None, and callers treat an
    unresolvable pid conservatively (they simply cannot attribute it to a slot).
    """
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return None
            try:
                k32.QueryFullProcessImageNameW.argtypes = [
                    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                    ctypes.POINTER(wintypes.DWORD)]
                k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buf))
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return buf.value
                return None
            finally:
                k32.CloseHandle(h)
        except Exception:
            return None
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def _iter_all_pids() -> list[int]:
    """Best-effort list of live process ids on this machine (``[]`` if we cannot
    enumerate). Windows: psapi ``EnumProcesses``. POSIX: numeric ``/proc`` dirs.
    """
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            psapi = ctypes.windll.psapi
            n = 4096
            while True:
                arr = (wintypes.DWORD * n)()
                needed = wintypes.DWORD()
                if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr),
                                           ctypes.byref(needed)):
                    return []
                got = needed.value // ctypes.sizeof(wintypes.DWORD)
                if got < n:
                    return [int(arr[i]) for i in range(got)]
                n *= 2  # buffer was full -- grow and re-enumerate
        except Exception:
            return []
    try:
        return [int(e) for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return []


def _reliable_process_enumeration() -> bool:
    """Whether this host can enumerate processes for slot-image attribution."""
    return os.name == "nt" or Path("/proc").is_dir()


def _recorded_ownership_is_stale(root: Path, version: str) -> bool:
    """Whether explicit ownership records exist for ``version`` and are all dead."""
    try:
        data = json.loads((root / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    except Exception:
        return False
    entries = data if isinstance(data, list) else [data]
    matching: list[int] = []
    found_version = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        recorded_version = entry.get("version")
        if not isinstance(recorded_version, str):
            continue
        if _norm_version(recorded_version) != _norm_version(version):
            continue
        found_version = True
        pid = entry.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            return False
        matching.append(pid)
    return found_version and bool(matching) and all(
        not _pid_alive(pid) for pid in matching
    )


def _pid_cmdline_argv0(pid: int) -> str | None:
    """POSIX argv[0] of a process -- the path it was *launched* by, BEFORE any
    symlink resolution (e.g. ``versions/<v>/bin/python`` even when that file
    symlinks a shared base interpreter). ``None`` on Windows / unavailable.

    This is the symlink-robust complement to :func:`_pid_image_path`: a
    ``python -m venv`` / ``uv venv`` interpreter is often a symlink, so
    ``/proc/<pid>/exe`` resolves to the base interpreter *outside* the slot,
    while argv[0] preserves the in-slot launch path.
    """
    if os.name == "nt":
        return None
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    argv0 = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
    return argv0 or None


def _norm_version(v: str) -> str:
    """Normalize a version string for matching (the recorded PEP 440 form
    ``0.4.0.dev287`` vs the dir name ``0.4.0-dev287``)."""
    return v.replace("-", ".").replace("_", ".")


def _slot_of_path(path: str | None, versions_abs: str, versions: set[str]) -> str | None:
    """The version dir name a path lies under (``<versions>/<v>/...``), or None.

    Compares WITHOUT resolving symlinks (``os.path.abspath``, not
    ``Path.resolve``) so a symlinked venv interpreter launched by its in-slot
    path still attributes to its slot.
    """
    if not path:
        return None
    try:
        p = os.path.normcase(os.path.abspath(path))
    except (OSError, ValueError):
        return None
    base = os.path.normcase(versions_abs)
    if not base.endswith(os.sep):
        base += os.sep
    if not p.startswith(base):
        return None
    first = p[len(base):].split(os.sep, 1)[0]
    return first if first in versions else None


def _recorded_live_versions(root: Path, versions: set[str]) -> set[str]:
    """Versions explicitly recorded live in ``running-version.json`` -- the
    symlink-proof signal a daemon writes about itself (``{version, pid}``). The
    recorded PEP 440 string is matched to the actual dir name via
    :func:`_norm_version`."""
    path = root / RUNNING_VERSION_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    by_norm = {_norm_version(v): v for v in versions}
    entries = data if isinstance(data, list) else [data]
    out: set[str] = set()
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("pid"), int) and _pid_alive(e["pid"]):
            rec = e.get("version")
            if isinstance(rec, str):
                dirname = by_norm.get(_norm_version(rec))
                if dirname:
                    out.add(dirname)
    return out


def _versions_with_live_process(root: Path) -> set[str]:
    """Version names a **live process is currently running from** -- the precise
    "in use" set that GC must never reap.

    Three complementary, best-effort signals (a version is in use if ANY fires):

    1. ``running-version.json`` -- the version a daemon explicitly recorded for
       its live pid (symlink-proof; matched dir<-record via :func:`_norm_version`).
    2. argv[0] (``/proc/<pid>/cmdline``) resolving under ``versions/<v>/`` -- the
       un-resolved launch path, correct even when the venv interpreter SYMLINKS a
       shared base interpreter (``python -m venv`` / ``uv venv`` on Linux), where
       signal 3 would resolve outside the slot.
    3. the process image path (``QueryFullProcessImageNameW`` / ``/proc/<pid>/exe``)
       under ``versions/<v>/`` -- a COPIED venv python (Windows; ``uv --copies``)
       lands here directly.

    Candidate pids are the machine's live processes plus any recorded pids.
    """
    versions_abs = os.path.abspath(str(root / VERSIONS_DIR))
    versions = set(list_versions(root))
    in_use: set[str] = _recorded_live_versions(root, versions)
    for pid in set(_iter_all_pids()) | _running_pids(root):
        for cand in (_pid_cmdline_argv0(pid), _pid_image_path(pid)):
            v = _slot_of_path(cand, versions_abs, versions)
            if v:
                in_use.add(v)
    return in_use


_AV_RETRY_ATTEMPTS = 4
_AV_RETRY_BACKOFF = 0.5  # seconds; grows linearly per attempt


def _is_transient_lock(exc: OSError) -> bool:
    """True when ``exc`` looks like a *transient* file lock we should wait out
    rather than a real, permanent failure.

    On Windows a version-dir GC routinely races **Windows Defender (MsMpEng)**,
    which briefly holds a handle to the old slot's ``Scripts/python.exe`` right
    after the daemon released it -- surfacing as ``WinError 5`` (access denied),
    ``32``/``33`` (sharing violation / lock violation), or ``errno EACCES``. That
    is NOT a live daemon and NOT a corrupt slot: the directory is orphaned and
    reclaimable the moment the scanner lets go (dotfiles #911).
    """
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "errno", None) == errno.EACCES:
        return True
    return getattr(exc, "winerror", None) in (5, 32, 33)


def _rmtree_deferrable(d: Path, *, label: str) -> bool:
    """``shutil.rmtree`` with AV-tolerant retries. Returns True iff removed.

    A transient lock (see :func:`_is_transient_lock` -- typically Defender
    scanning a just-freed ``python.exe``) is retried a few times with a short
    backoff; if still held, the removal is **deferred to the next sweep** and
    reported calmly (an informational note, not an alarming "could not remove"
    error) -- the orphaned slot costs only disk and will be reclaimed next run.
    A genuinely non-transient ``OSError`` is still surfaced as an error. Fixes
    the noisy, non-retried ``WinError 5`` GC failure (dotfiles #911).
    """
    for attempt in range(_AV_RETRY_ATTEMPTS):
        try:
            shutil.rmtree(d)
            return True
        except OSError as exc:
            if not _is_transient_lock(exc):
                print(f"{label}: could not remove {d}: {exc}", file=sys.stderr)
                return False
            if attempt < _AV_RETRY_ATTEMPTS - 1:
                time.sleep(_AV_RETRY_BACKOFF * (attempt + 1))
    # Still locked after retries -> defer quietly to the next sweep.
    print(
        f"{label}: deferring {d.name} -- transiently locked (likely Windows "
        f"Defender scanning python.exe); will reclaim on the next sweep.",
        file=sys.stderr,
    )
    return False


# Optional recency backstop (days) for GC. The PRIMARY GC gate is now precise
# live-process usage: ``gc(protect_pids=True)`` keeps exactly the versions a live
# process is running from (see :func:`_versions_with_live_process`) and reaps the
# rest -- the invariant "reap a version iff no active process runs from it." The
# age floor is therefore OFF by default (0.0). A caller may still pass a positive
# ``min_age_days`` to *additionally* hold a just-superseded slot long enough for a
# STORED (not-currently-running) path-pinned launch reference to age out -- the
# historical dev14-prune concern (dotfiles#1221) on runtimes that keep such
# references (e.g. an agent-bridge codespace session whose launch command bakes
# ``versions/<v>/Scripts/python.exe`` but is not running at GC time). current and
# any ``--keep`` are always preserved regardless.
DEFAULT_GC_MIN_AGE_DAYS = 0.0


def _slot_age_days(root: Path, version: str) -> float:
    """Age of a version slot in days (from its dir mtime ~= install time).

    A slot we cannot stat is treated as infinitely old (eligible) so a genuinely
    broken/half-present dir is still collectable.
    """
    try:
        mtime = version_dir(root, version).stat().st_mtime
    except OSError:
        return float("inf")
    return max(0.0, (time.time() - mtime) / 86400.0)


def _junction_slot_names(root: Path) -> list[str]:
    """Legacy version slots that are directory *junctions* (Windows only, #846).

    :func:`list_versions` lists only real directories (``is_dir()``), which on
    Windows *traverses* a reparse point -- so a **broken-target** junction slot
    reports ``is_dir() == False`` and is dropped entirely (it can never be
    reclaimed), and even a **live-target** junction is a reparse point GC must
    remove as its exact entry, never by recursing into its target. Legacy
    layouts can leave such junction slots under ``versions/``; GC must still see
    them to reclaim them. Detect them with :func:`_is_link` (lstat, never
    traverses -- so RedirectionGuard's WinError 448 can't hide them). Always
    empty on POSIX and whenever no junction slots exist.
    """
    if os.name != "nt":
        return []
    vroot = versions_root(root)
    if not vroot.is_dir():
        return []
    try:
        entries = list(vroot.iterdir())
    except OSError:
        return []
    return [p.name for p in entries if _is_link(p)]


def _gc_candidate_versions(root: Path) -> list[str]:
    """Version slot names GC may reclaim: the normal real-directory slots plus
    any legacy Windows junction slots that :func:`list_versions` omits (#846).

    The shared :func:`list_versions` deliberately keeps a junction-free view for
    non-GC callers (current-version resolution, last-known-good, live-process
    attribution); the junction slots are surfaced *only* to GC, here.
    """
    names = set(list_versions(root))
    names.update(_junction_slot_names(root))
    return sorted(names, key=_version_key)


def _remove_slot(d: Path, *, label: str) -> bool:
    """Remove a version slot, junction-safe. Returns True iff removed (#846).

    A legacy Windows junction slot is a reparse point: remove it as its exact
    entry via :func:`_remove_link` (``os.rmdir`` / ``unlink``) so GC never
    traverses into -- or deletes the contents of -- the junction's target. A
    real directory is removed with the AV-tolerant :func:`_rmtree_deferrable`.
    """
    if _is_link(d):
        try:
            _remove_link(d)
            return True
        except OSError as exc:
            print(f"{label}: could not remove junction slot {d}: {exc}", file=sys.stderr)
            return False
    return _rmtree_deferrable(d, label=label)


def gc(root: Path, keep: list[str] | None = None,
       protect_pids: bool = False, link_name: str = CURRENT_LINK,
       min_age_days: float = DEFAULT_GC_MIN_AGE_DAYS) -> list[str]:
    """Remove version dirs that are not protected. Returns the removed names.

    Never removes: the ``current`` version, any name in ``keep`` (e.g. the
    previous-good for rollback), any version a live process is running from (when
    ``protect_pids``), and -- if a positive ``min_age_days`` is passed -- any slot
    younger than that (an optional backstop; see
    :data:`DEFAULT_GC_MIN_AGE_DAYS`).

    ``protect_pids`` is now **precise**: it protects exactly the versions whose
    directory a live process's executable resolves under (see
    :func:`_versions_with_live_process`) -- the real "reap iff no active process
    runs from it" invariant. If that scan finds nothing but pids ARE recorded
    live (a platform where process enumeration/image-path lookup is blocked), it
    falls back to the older conservative rule -- keep the newest non-current slot
    too -- so GC is never *less* safe than before.

    Legacy Windows junction slots (#846) are also reclaimed: they are enumerated
    via :func:`_gc_candidate_versions` (which :func:`list_versions` omits) and
    removed via :func:`_remove_slot` as the exact reparse-point entry, never
    traversing the junction target.
    """
    keep_set = set(keep or [])
    cur = current_version(root, link_name)
    if cur:
        keep_set.add(cur)

    if protect_pids:
        in_use = _versions_with_live_process(root)
        if in_use:
            keep_set |= in_use
        elif _running_pids(root):
            # Enumeration yielded nothing yet a live pid is recorded -> we cannot
            # attribute it to a slot on this platform; keep the newest non-current
            # version as its likely home (the pre-precision conservative rule).
            non_current = [v for v in list_versions(root) if v != cur]
            if non_current:
                keep_set.add(non_current[-1])

    removed: list[str] = []
    for v in _gc_candidate_versions(root):
        if v in keep_set:
            continue
        d = version_dir(root, v)
        # Optional recency backstop (default off): active-process usage is the
        # primary gate, but a caller may pass a floor to hold just-superseded
        # slots for a stored (not-running) pinned reference to age out. Never
        # applies to a legacy junction slot (#846): its ``stat()`` would traverse
        # the reparse point and report the *target's* mtime (possibly young),
        # shielding the junction indefinitely -- junction slots must always be
        # reclaimable, and ``_remove_slot`` only unlinks the entry anyway.
        if min_age_days > 0 and not _is_link(d) and _slot_age_days(root, v) < min_age_days:
            continue
        if _remove_slot(d, label="gc"):
            removed.append(v)
    return removed


# --------------------------------------------------------------------------
# Completion marker (dotfiles #935): assert a slot finished a HEALTHY build
# --------------------------------------------------------------------------
# A per-slot ``.install-complete.json`` marker turns "is versions/<v> a real
# install or a half-built corpse?" into a cheap positive check. The installer
# writes it ATOMICALLY right after the freshly-built slot passes its isolated
# health gate, so "marker present" == "this slot is a healthy, complete build".
# A crashed / watchdog-killed install never reaches the marker, so its slot is
# provably incomplete -> tossed and rebuilt on the next run (clean retry),
# instead of being silently reused via ``uv venv --allow-existing``.

COMPLETE_MARKER = ".install-complete.json"


def marker_path(root: Path, version: str) -> Path:
    return version_dir(root, version) / COMPLETE_MARKER


def _load_unique_json(path: Path):
    def unique_object(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate JSON field: {key}")
            out[key] = value
        return out

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
    )


def validate_marker(data, version: str) -> dict | None:
    """Validate the canonical completion-marker schema.

    JSON field order is intentionally irrelevant to Python readers. The shell
    resolver recognizes the canonical order emitted by :func:`mark_complete`;
    both enforce the same fields, types, and exact version value.
    """
    if not isinstance(data, dict):
        return None
    allowed = {"version", "completed_at", "pid", "payload_hash"}
    required = {"version", "completed_at", "pid"}
    if not required.issubset(data) or not set(data).issubset(allowed):
        return None
    if not isinstance(data["version"], str) or data["version"] != version:
        return None
    if not isinstance(data["completed_at"], str):
        return None
    if (
        not isinstance(data["pid"], int)
        or isinstance(data["pid"], bool)
        or data["pid"] < 0
    ):
        return None
    if "payload_hash" in data and not isinstance(data["payload_hash"], str):
        return None
    return data


def read_marker(root: Path, version: str) -> dict | None:
    """Parse a slot's completion marker, or None if absent/partial/mismatched."""
    try:
        data = _load_unique_json(marker_path(root, version))
    except Exception:
        return None
    return validate_marker(data, version)


def is_complete(root: Path, version: str, *, expect_hash: str | None = None) -> bool:
    """True iff versions/<version> exists AND carries a valid completion marker.

    A slot without a valid marker is a failed/partial build (crashed, killed by
    the install watchdog, or interrupted) -- it must be tossed + rebuilt, never
    reused. ``expect_hash``, when given, also requires the recorded payload hash
    to match, so a dev-checkout that changed the payload WITHOUT bumping the
    version forces a rebuild (marketplace: version==content, so the hash is
    belt-and-suspenders).
    """
    if not version_dir(root, version).is_dir():
        return False
    m = read_marker(root, version)
    if m is None:
        return False
    if expect_hash is not None and m.get("payload_hash") != expect_hash:
        return False
    return True


def mark_complete(root: Path, version: str, *, payload_hash: str | None = None,
                  pid: int | None = None) -> Path:
    """Atomically write the slot's completion marker (temp + os.replace).

    Call ONLY after the freshly-built slot passes its isolated health gate, so
    the marker's presence is a positive "healthy, complete install" assertion. A
    reader treats a partial/absent marker as incomplete, so a torn write (process
    killed mid-marker) is safe -- it just reads as not-yet-complete and rebuilds.
    """
    import time as _t

    vdir = version_dir(root, version)
    vdir.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "version": version,
        "completed_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
        "pid": pid if pid is not None else os.getpid(),
    }
    if payload_hash is not None:
        payload["payload_hash"] = payload_hash
    dest = marker_path(root, version)
    tmp = dest.with_name(dest.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, dest)  # atomic publish
    return dest


def toss_incomplete(root: Path, link_name: str = CURRENT_LINK) -> list[str]:
    """Remove non-current slots that lack a valid completion marker.

    These are failed/partial builds (dotfiles #935). ``current`` is always kept
    (a live daemon serves from it; a legacy slot may also predate the marker
    convention). Incomplete slots are never activated -- activate runs only after
    the health gate + marker -- so they are never the daemon's live version and
    are always safe to toss. Returns the tossed version names.
    """
    cur = current_version(root, link_name)
    tossed: list[str] = []
    for v in list_versions(root):
        if v == cur:
            continue
        if is_complete(root, v):
            continue
        d = version_dir(root, v)
        if _rmtree_deferrable(d, label="toss"):
            tossed.append(v)
    return tossed


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _emit(data, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(data))
    elif isinstance(data, list):
        for item in data:
            print(item)
    elif data is not None:
        print(data)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="versioned_runtime", description=__doc__)
    p.add_argument("--root", required=True, type=Path, help="runtime root dir")
    p.add_argument("--link-name", default=CURRENT_LINK,
                   help=f"name of the active-version link (default {CURRENT_LINK!r}; "
                        f"agent-bridge uses 'venv' so its task/binstubs resolve "
                        f"through the junction unchanged)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("slot", help="ensure versions/<version> exists")
    sp.add_argument("version")
    sp.add_argument("--clean-incomplete", action="store_true",
                    help="if the slot exists but lacks a valid completion marker "
                         "(a failed/partial prior build), remove it first so a "
                         "fresh venv is built (dotfiles #935)")
    ap = sub.add_parser("activate", help="publish current-version -> <version>")
    ap.add_argument("version")
    ap.add_argument("--replace-nonlink", action="store_true",
                    help="POSIX first-migration: if the link path is a real dir "
                         "(legacy venv), move it aside to <name>.legacy-<ts> before "
                         "laying the symlink (no effect on Windows, junction-free)")
    ap.add_argument("--no-link", action="store_true",
                    help="force junction-free on any OS: write only the "
                         "current-version marker and remove any stale legacy link "
                         "(the runtime is selected by version-pinned binstubs). "
                         "Windows is always junction-free regardless.")
    sub.add_parser("current", help="print the active version")
    rp = sub.add_parser("resolve", help="print current/<subpath>")
    rp.add_argument("--subpath", default="")
    sub.add_parser("resolve-python",
                   help="print the canonical interpreter path (marker -> "
                        "last-known-good -> newest complete slot; junction-free, "
                        "no PATH fallback). Exits 1 if no runtime is installed.")
    sub.add_parser("list", help="list installed versions")
    gp = sub.add_parser("gc", help="remove unreferenced version dirs")
    gp.add_argument("--keep", action="append", default=[],
                    help="version(s) to preserve (repeatable)")
    gp.add_argument("--protect-pids", action="store_true",
                    help="also protect versions a live recorded pid may run from")
    gp.add_argument("--min-age-days", type=float,
                    default=DEFAULT_GC_MIN_AGE_DAYS,
                    help="optional recency backstop: minimum slot age (days) "
                         "before an unprotected version is eligible for removal. "
                         "The primary gate is live-process usage (--protect-pids); "
                         "this only additionally holds a just-superseded slot for a "
                         "STORED (not-running) pinned launch reference to age out "
                         "(default: %(default)s = off)")
    gp.add_argument("--toss-incomplete", action="store_true",
                    help="also remove non-current slots lacking a completion "
                         "marker (failed/partial builds; dotfiles #935)")
    mp = sub.add_parser("mark-complete",
                        help="mark versions/<version> as a healthy, complete build")
    mp.add_argument("version")
    mp.add_argument("--payload-hash", default=None,
                    help="record the source payload hash for change detection")
    mp.add_argument("--pid", type=int, default=None)
    ip = sub.add_parser("is-complete",
                        help="exit 0 iff versions/<version> has a valid marker")
    ip.add_argument("version")
    ip.add_argument("--expect-hash", default=None,
                    help="also require the recorded payload hash to match")
    sub.add_parser("toss-incomplete",
                   help="remove non-current slots lacking a completion marker")

    args = p.parse_args(argv)
    root: Path = args.root
    link_name: str = args.link_name

    try:
        if args.cmd == "slot":
            _emit(str(slot(root, args.version, link_name=link_name,
                           clean_incomplete=args.clean_incomplete)), args.json)
        elif args.cmd == "activate":
            vdir = activate(root, args.version, link_name=link_name,
                            replace_nonlink=args.replace_nonlink,
                            link_free=args.no_link)
            _emit({"activated": args.version, "path": str(vdir)}
                  if args.json else str(vdir), args.json)
        elif args.cmd == "current":
            cur = current_version(root, link_name)
            if cur is None and not args.json:
                print("", end="")
                return 1
            _emit({"current": cur} if args.json else (cur or ""), args.json)
        elif args.cmd == "resolve":
            cur = current_version(root, link_name)
            if cur is None:
                print("no active version", file=sys.stderr)
                return 1
            base = version_dir(root, cur)
            out = base / args.subpath if args.subpath else base
            _emit(str(out), args.json)
        elif args.cmd == "resolve-python":
            py = resolve_python(root)
            if py is None:
                print("no runtime installed", file=sys.stderr)
                return 1
            _emit({"python": str(py)} if args.json else str(py), args.json)
        elif args.cmd == "list":
            vs = list_versions(root)
            cur = current_version(root, link_name)
            if args.json:
                _emit({"versions": vs, "current": cur}, True)
            else:
                for v in vs:
                    print(f"{'*' if v == cur else ' '} {v}")
        elif args.cmd == "gc":
            removed = gc(root, keep=args.keep, protect_pids=args.protect_pids,
                         link_name=link_name, min_age_days=args.min_age_days)
            if args.toss_incomplete:
                removed = list(removed) + toss_incomplete(root, link_name)
            _emit({"removed": removed} if args.json else removed, args.json)
        elif args.cmd == "mark-complete":
            dest = mark_complete(root, args.version,
                                 payload_hash=args.payload_hash, pid=args.pid)
            _emit({"marked": args.version, "path": str(dest)}
                  if args.json else str(dest), args.json)
        elif args.cmd == "is-complete":
            ok = is_complete(root, args.version, expect_hash=args.expect_hash)
            _emit({"version": args.version, "complete": ok}, args.json) \
                if args.json else None
            return 0 if ok else 1
        elif args.cmd == "toss-incomplete":
            tossed = toss_incomplete(root, link_name)
            _emit({"tossed": tossed} if args.json else tossed, args.json)
        else:  # pragma: no cover
            p.error(f"unknown command {args.cmd}")
    except Exception as exc:
        print(f"versioned_runtime: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
