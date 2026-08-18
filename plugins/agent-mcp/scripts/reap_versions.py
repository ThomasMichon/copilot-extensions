#!/usr/bin/env python3
"""Reap processes still running from a STALE (non-current) agent-mcp version slot.

An agent-mcp-local companion to the shared versioned-runtime primitive
(``scripts/versioned_runtime.py``). That primitive's ``gc`` only removes stale
version *directories*, and it deliberately *protects* any slot a live process
runs from -- so a leaked/orphaned bridge tree (or a warmth daemon) from a prior
version stays resident across an upgrade ("two runtime versions resident"). This
tool closes the gap: after a new version is activated, it terminates every
process still executing from a NON-current slot (and its tree), after which GC
can drop the now-idle dirs on its next pass.

It **reuses** the primitive's slot-attribution helpers (pid enumeration,
image/argv[0] -> slot mapping) rather than duplicating them, and lives in
agent-mcp only: the primitive is vendored byte-identically into every plugin and
must not carry a bridge-specific reaping policy.

Usage::

    reap_versions.py --root ~/.agent-mcp [--link-name .venv] [--json] [--exclude-pid PID ...]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# The primitive is a sibling module in this same scripts/ dir; import its
# (unmodified) attribution helpers so this companion never forks that logic.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from versioned_runtime import (
    CURRENT_LINK,
    VERSIONS_DIR,
    _iter_all_pids,
    _norm_version,
    _pid_alive,
    _pid_cmdline_argv0,
    _pid_image_path,
    _running_pids,
    _slot_of_path,
    current_version,
    list_versions,
)


def _pids_by_slot(root: Path) -> dict[str, set[int]]:
    """Map each version-dir name -> the set of live pids running from that slot.

    Same argv[0]/image-path attribution the primitive uses for GC protection,
    but keeps the pids so a caller can act on them (reap) rather than only
    learning *which* versions are in use.
    """
    versions_abs = os.path.abspath(str(root / VERSIONS_DIR))
    versions = set(list_versions(root))
    out: dict[str, set[int]] = {}
    for pid in set(_iter_all_pids()) | _running_pids(root):
        for cand in (_pid_cmdline_argv0(pid), _pid_image_path(pid)):
            v = _slot_of_path(cand, versions_abs, versions)
            if v:
                out.setdefault(v, set()).add(pid)
                break
    return out


def _terminate_tree(pid: int) -> bool:
    """Best-effort terminate a process AND its descendants.

    A leaked bridge is a tree (the wrapped stdio upstream child, mint helpers),
    so we kill the whole tree, not just the root pid. Returns True if a terminate
    was issued (not a liveness guarantee).
    """
    if not _pid_alive(pid):
        return False
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False
    import signal

    # Prefer the process group (reaps the upstream stdio child too), but NEVER
    # signal our own group. SIGTERM first, SIGKILL as the backstop.
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    try:
        my_pgid = os.getpgid(0)
    except OSError:
        my_pgid = None
    issued = False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            if pgid is not None and pgid != my_pgid:
                os.killpg(pgid, sig)
            else:
                os.kill(pid, sig)
            issued = True
        except ProcessLookupError:
            break
        except OSError:
            pass
        if not _pid_alive(pid):
            break
        time.sleep(0.2)
    return issued


def reap_stale(root: Path, *, link_name: str = CURRENT_LINK,
               exclude_pids: set[int] | None = None) -> list[dict]:
    """Terminate every process running from a NON-current version slot.

    The now-current slot, this process, and any ``exclude_pids`` are never
    touched. Returns a list of ``{"version", "pid", "terminated"}`` records.

    If there is no authoritative ``current-version`` marker (``current_version``
    returns ``None``) we cannot tell which slot is active, so we reap **nothing**
    rather than risk terminating the live runtime.
    """
    cur = current_version(root, link_name)
    if cur is None:
        return []
    cur_norm = _norm_version(cur)
    exclude = set(exclude_pids or ())
    exclude.add(os.getpid())
    reaped: list[dict] = []
    for version, pids in _pids_by_slot(root).items():
        if _norm_version(version) == cur_norm:
            continue
        for pid in sorted(pids):
            if pid in exclude:
                continue
            terminated = _terminate_tree(pid)
            reaped.append({"version": version, "pid": pid, "terminated": terminated})
    return reaped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, type=Path, help="runtime root dir")
    ap.add_argument("--link-name", default=CURRENT_LINK,
                    help=f"name of the active-version link (default {CURRENT_LINK!r})")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--exclude-pid", action="append", type=int, default=[],
                    help="pid(s) to spare in addition to self (repeatable)")
    args = ap.parse_args(argv)
    reaped = reap_stale(args.root, link_name=args.link_name,
                        exclude_pids=set(args.exclude_pid))
    if args.json:
        print(json.dumps({"reaped": reaped}))
    else:
        for r in reaped:
            print(f"{r['version']}:{r['pid']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
