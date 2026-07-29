#!/usr/bin/env python3
"""Immutable per-version runtime layout manager (dotfiles #581).

Never mutate a runtime venv in place. Each version installs into its own immutable
directory under ``<root>/versions/<version>``; the active version is named by a
``<root>/current`` **directory junction** (Windows) / **symlink** (POSIX). Switching
versions is an atomic-ish swap of that link, not a file rewrite -- so a running
daemon (which already holds its own immutable files open) is never edited underneath
itself, rollback is a link swap (no rebuild), and the concurrent-venv-mutation race
that spawns duplicate daemons (#123) cannot happen.

This is a **stdlib-only** helper deliberately kept *out* of every runtime venv (no
vendored-lib fan-out): the bootstrapping python at install time runs it as
``python versioned_runtime.py <cmd> ...``. It owns only the ``versions/`` +
``current`` layout; venv *creation* and package install stay in the per-plugin
installer, which points them at the slot this returns.

Commands (all take ``--root <dir>``; ``--json`` for machine output)::

    slot     <version>              ensure versions/<version> exists; print its path
    activate <version>              atomically point current -> versions/<version>
    current                         print the active version (via the current link)
    resolve  [--subpath P]          print current/<P> (e.g. the venv python path)
    list                            list installed versions (+ which is current)
    gc [--keep V ...] [--protect-pids]   remove version dirs that are not current,
                                    not kept, and (with --protect-pids) not held by
                                    a live pid recorded in running-version.json

Exit code is 0 on success, non-zero on error; errors print to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

CURRENT_LINK = "current"
VERSIONS_DIR = "versions"
RUNNING_VERSION_FILE = "running-version.json"


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
    """Best-effort ordering key (PEP 440 when available, else raw string)."""
    try:
        from packaging.version import InvalidVersion, Version

        try:
            return (0, Version(v))
        except InvalidVersion:
            return (1, v)
    except Exception:
        return (1, v)


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

    A POSIX symlink resolves via ``os.readlink``; a Windows junction is a reparse
    point that ``Path.resolve()`` follows. Returns the absolute target dir, or
    ``None`` when the link is absent/broken.
    """
    if not link.exists() and not link.is_symlink():
        return None
    try:
        if link.is_symlink():
            target = Path(os.readlink(link))
            if not target.is_absolute():
                target = (link.parent / target)
            return target
        # Junction (Windows) or a real dir: resolve() follows the reparse point.
        return link.resolve()
    except OSError:
        return None


def current_version(root: Path, link_name: str = CURRENT_LINK) -> str | None:
    """The active version name (the basename of the ``current`` link target)."""
    target = _link_target(current_link(root, link_name))
    if target is None:
        return None
    name = target.name
    # Only trust it if it actually lives under versions/ and exists.
    if version_dir(root, name).exists():
        return name
    return None


# --------------------------------------------------------------------------
# current link: write (atomic-ish swap)
# --------------------------------------------------------------------------

def _make_link(link: Path, target: Path) -> None:
    """Create ``link`` -> ``target`` (dir). Symlink on POSIX, junction on Windows."""
    if os.name == "nt":
        _make_junction(link, target)
    else:
        os.symlink(target, link, target_is_directory=True)


def _make_junction(link: Path, target: Path) -> None:
    """Create a Windows directory **junction** (needs no privilege, unlike a
    symlink). Prefers the private ``_winapi.CreateJunction``; falls back to
    ``cmd /c mklink /J``."""
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))  # type: ignore[attr-defined]
        return
    except (ImportError, AttributeError, OSError):
        pass
    import subprocess

    res = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise OSError(f"mklink /J failed: {res.stderr.strip() or res.stdout.strip()}")


def _remove_link(link: Path) -> None:
    """Remove an existing ``current`` link without touching its target contents.

    A symlink/junction is unlinked, never recursed into (so we never delete the
    version dir it points at). ``os.rmdir`` removes a Windows junction; ``unlink``
    removes a POSIX symlink.
    """
    if not link.exists() and not link.is_symlink():
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
             replace_nonlink: bool = False) -> Path:
    """Point the ``link_name`` link at ``versions/<version>``.

    POSIX: create a temp symlink and ``os.replace`` it over the link -- an
    atomic rename, so a concurrent reader sees either the old or the new target,
    never a missing link. Windows: junctions can't be atomically replaced, so
    remove + recreate; the window only affects a *new* resolution (the running
    daemon holds its own immutable files), and callers retry. Returns the version
    dir. Raises if the version isn't installed.

    ``link_name`` lets a runtime keep its historical path name as the selector
    (agent-bridge uses ``venv`` so its scheduled task/binstubs/cutover resolve
    through the junction unchanged). If the link path is currently a **real
    directory** (a legacy, pre-versioned venv), this refuses unless
    ``replace_nonlink`` is set, in which case the real dir is moved aside to
    ``<name>.legacy-<ts>`` first (the caller must ensure no process holds it open
    -- e.g. the daemon is stopped or already cut over to the new version).
    """
    vdir = version_dir(root, version)
    if not vdir.is_dir():
        raise FileNotFoundError(f"version not installed: {vdir}")
    link = current_link(root, link_name)
    root.mkdir(parents=True, exist_ok=True)

    # Legacy real dir occupying the link path (first migration to the versioned
    # layout). Never recurse-delete it implicitly; move it aside on request.
    if (link.exists() or link.is_symlink()) and not _is_link(link):
        if not replace_nonlink:
            raise FileExistsError(
                f"{link} is a real directory, not a link; pass replace_nonlink "
                f"to move it aside and lay the versioned link"
            )
        import time as _t

        aside = link.with_name(f"{link.name}.legacy-{int(_t.time())}")
        os.replace(link, aside)

    if os.name != "nt":
        tmp = link.with_name(link.name + ".tmp")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(vdir, tmp, target_is_directory=True)
        os.replace(tmp, link)  # atomic
        return vdir

    # Windows: remove + recreate the junction.
    _remove_link(link)
    _make_link(link, vdir)
    return vdir


def slot(root: Path, version: str) -> Path:
    """Ensure ``versions/<version>`` exists (empty is fine) and return it."""
    vdir = version_dir(root, version)
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


def gc(root: Path, keep: list[str] | None = None,
       protect_pids: bool = False, link_name: str = CURRENT_LINK) -> list[str]:
    """Remove version dirs that are not protected. Returns the removed names.

    Never removes: the ``current`` version, any name in ``keep`` (e.g. the
    previous-good for rollback), and -- when ``protect_pids`` -- any version whose
    directory a still-live recorded pid may be running from. Live-pid protection
    is coarse (we cannot map a pid to its version dir portably), so if *any* live
    pid is recorded we conservatively keep ``current`` (already kept) and skip GC
    of the most recent non-current version too, leaving a safe rollback target.
    """
    keep_set = set(keep or [])
    cur = current_version(root, link_name)
    if cur:
        keep_set.add(cur)

    if protect_pids and _running_pids(root):
        # A live daemon may still be serving from a non-current version mid-cutover;
        # keep the newest non-current version as its likely home.
        non_current = [v for v in list_versions(root) if v != cur]
        if non_current:
            keep_set.add(non_current[-1])

    removed: list[str] = []
    for v in list_versions(root):
        if v in keep_set:
            continue
        d = version_dir(root, v)
        try:
            shutil.rmtree(d)
            removed.append(v)
        except OSError as exc:
            print(f"gc: could not remove {d}: {exc}", file=sys.stderr)
    return removed


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
    ap = sub.add_parser("activate", help="point current -> versions/<version>")
    ap.add_argument("version")
    ap.add_argument("--replace-nonlink", action="store_true",
                    help="if the link path is a real dir (legacy venv), move it "
                         "aside to <name>.legacy-<ts> before laying the link")
    sub.add_parser("current", help="print the active version")
    rp = sub.add_parser("resolve", help="print current/<subpath>")
    rp.add_argument("--subpath", default="")
    sub.add_parser("list", help="list installed versions")
    gp = sub.add_parser("gc", help="remove unreferenced version dirs")
    gp.add_argument("--keep", action="append", default=[],
                    help="version(s) to preserve (repeatable)")
    gp.add_argument("--protect-pids", action="store_true",
                    help="also protect versions a live recorded pid may run from")

    args = p.parse_args(argv)
    root: Path = args.root
    link_name: str = args.link_name

    try:
        if args.cmd == "slot":
            _emit(str(slot(root, args.version)), args.json)
        elif args.cmd == "activate":
            vdir = activate(root, args.version, link_name=link_name,
                            replace_nonlink=args.replace_nonlink)
            _emit({"activated": args.version, "path": str(vdir)}
                  if args.json else str(vdir), args.json)
        elif args.cmd == "current":
            cur = current_version(root, link_name)
            if cur is None and not args.json:
                print("", end="")
                return 1
            _emit({"current": cur} if args.json else (cur or ""), args.json)
        elif args.cmd == "resolve":
            link = current_link(root, link_name)
            out = link / args.subpath if args.subpath else link
            _emit(str(out), args.json)
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
                         link_name=link_name)
            _emit({"removed": removed} if args.json else removed, args.json)
        else:  # pragma: no cover
            p.error(f"unknown command {args.cmd}")
    except Exception as exc:
        print(f"versioned_runtime: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
