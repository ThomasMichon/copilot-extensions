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


def _pid_ppid(pid: int) -> int | None:
    """Parent pid of ``pid`` from ``/proc/<pid>/stat`` (POSIX). None on failure.

    Parses defensively: the ``comm`` field (2) is wrapped in parens and may
    itself contain spaces/parens, so split on the LAST ``)`` and read ppid as
    the second whitespace field after it (state, ppid, ...).
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        rparen = data.rfind(b")")
        if rparen == -1:
            return None
        after = data[rparen + 1:].split()
        # after = [state, ppid, pgrp, ...]
        return int(after[1])
    except (OSError, ValueError, IndexError):
        return None


def _descendants(root_pid: int) -> list[int]:
    """All descendant pids of ``root_pid`` (POSIX ``/proc``), excluding itself.

    Used to reap a stale process's *own* subtree (the wrapped stdio upstream
    child, mint helpers) WITHOUT signalling a foreign process group. Walking
    real parent->child links can never reach the process's parent (e.g. the
    host Copilot that spawned a bridge into its own group), so it cannot take
    down a live session. Best-effort; returns [] off POSIX or on any error.
    """
    if os.name == "nt":
        return []
    children: dict[int, list[int]] = {}
    try:
        entries = [int(e) for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return []
    for pid in entries:
        ppid = _pid_ppid(pid)
        if ppid is not None:
            children.setdefault(ppid, []).append(pid)
    out: list[int] = []
    seen: set[int] = set()
    stack = list(children.get(root_pid, []))
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        stack.extend(children.get(p, []))
    return out


def _terminate_tree(pid: int) -> bool:
    """Best-effort terminate a stale-version process AND its own descendants.

    A leaked bridge is a tree (the wrapped stdio upstream child, mint helpers),
    so we kill the whole tree, not just the root pid. Returns True if a terminate
    was issued (not a liveness guarantee).

    **Process-group safety (prevents a mass host-session kill).**
    A per-session agent-mcp *bridge* is spawned by its host Copilot into the
    host's OWN process group (the bridge is a child, not the group leader, so
    ``pgid != pid``). Signalling that group with ``killpg`` would hit the host
    Copilot and its whole terminal/pane -- killing a live session as a side
    effect of a version reap. So we only ever ``killpg`` a group this process's
    target actually *leads* (``pgid == pid``, e.g. a detached ``serve`` daemon
    or a standalone bridge that made its own group). For a stale process that is
    NOT its group's leader, we terminate the exact pid and walk its REAL
    descendants instead -- which can never reach the foreign parent.
    """
    if not _pid_alive(pid):
        return False
    if os.name == "nt":
        # taskkill /T walks the child TREE (descendants), not a process group,
        # so it already cannot reach a foreign parent -- safe as-is.
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

    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    try:
        my_pgid = os.getpgid(0)
    except OSError:
        my_pgid = None

    # Only group-kill a group our TARGET leads (its own detached group), and
    # never our own group. Otherwise the target shares a foreign group (its
    # host Copilot's) -- kill just the pid + its real descendants.
    own_group = pgid is not None and pgid == pid and pgid != my_pgid
    if own_group:
        targets: list[int] = []  # unused; killpg path below
    else:
        # Capture the subtree ONCE, before any signal reparents it.
        targets = [pid] + _descendants(pid)

    issued = False
    for sig in (signal.SIGTERM, signal.SIGKILL):  # SIGTERM drain, SIGKILL backstop
        try:
            if own_group:
                os.killpg(pgid, sig)  # type: ignore[arg-type]
                issued = True
            else:
                for tp in targets:
                    try:
                        os.kill(tp, sig)
                        issued = True
                    except ProcessLookupError:
                        pass
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
