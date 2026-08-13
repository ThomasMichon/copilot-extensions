"""Precise session->process reclamation for worktree-bound Copilot CLIs.

Resolves the *exact* live Copilot process(es) bound to a worktree's session via
Copilot's own ``inuse.<pid>.lock`` claim -- the authoritative pid<->session link
that the CLI itself writes into a session-state directory. Because the binding
comes from that lock file (not from a working-directory guess), a stray or
orphaned session can be freed **without "splashing"** onto a sibling session or
an unrelated worktree that merely shares a working directory.

Each resolved process is classified by **homing**:

* ``mux``  -- an ancestor is ``tmux``/``psmux``: the Copilot is wrapped by the
  multiplexer, so it is already legible to (and reapable by) the ``wt-<id>``
  mux-session fleet view (``restart``/reap).
* ``bare`` -- no multiplexer ancestor: a Copilot launched straight in a
  terminal (e.g. ``copilot`` in a shell, then ``/resume``). These are invisible
  to the mux-session fleet view, so they accumulate as un-reapable orphans when
  their terminal is closed or wedged. This is the case the reaper exists for.

This is the derive-don't-duplicate reclaim primitive behind
``agent-worktrees reap-session``: it stores no new state, deriving the
pid<->session<->worktree binding at read time from the owning layer's lock
files. Freeing an idle orphan **loses nothing** -- the session stays resumable
from its recovered on-disk state (agent-fabric vision, reclaim-idle-process).
"""

from __future__ import annotations

import os
import platform
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import locks, sessions, tracking

__all__ = [
    "build_process_table",
    "homing_of",
    "descendants_of",
    "resolve_bound_copilots",
    "find_bare_orphans",
    "bare_orphan_worktree_ids",
    "live_bridge_worktrees",
    "reap_bound_copilots",
]

_MUX_NAMES = ("psmux", "tmux")


# ---------------------------------------------------------------------------
# Process table (pid -> {ppid, name}) -- one dependency-free snapshot
# ---------------------------------------------------------------------------

def _process_table_windows() -> dict[int, dict]:
    """Snapshot every process via CreateToolhelp32Snapshot (pure ctypes)."""
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32FirstW.restype = wintypes.BOOL
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return {}
    table: dict[int, dict] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            name = Path(entry.szExeFile).name.lower()
            table[int(entry.th32ProcessID)] = {
                "ppid": int(entry.th32ParentProcessID),
                "name": name,
            }
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return table


def _process_table_posix() -> dict[int, dict]:
    """Snapshot every readable process via ``/proc/<pid>/stat``."""
    table: dict[int, dict] = {}
    proc = Path("/proc")
    try:
        entries = list(proc.iterdir())
    except OSError:
        return table
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat = (entry / "stat").read_text(errors="ignore")
        except OSError:
            continue
        # comm (field 2) may contain spaces/parens -- split on the LAST ')'.
        rparen = stat.rfind(")")
        lparen = stat.find("(")
        if rparen == -1 or lparen == -1:
            continue
        comm = stat[lparen + 1:rparen]
        rest = stat[rparen + 1:].split()
        # After comm: state (0), ppid (1) in the remaining fields.
        if len(rest) < 2:
            continue
        try:
            ppid = int(rest[1])
        except ValueError:
            continue
        table[pid] = {"ppid": ppid, "name": comm.lower()}
    return table


def build_process_table() -> dict[int, dict]:
    """Return a ``{pid: {"ppid": int, "name": str}}`` snapshot of all processes.

    One best-effort snapshot used for ancestry (mux vs. bare) and descendant
    (child-tree) resolution. Empty dict if enumeration fails.
    """
    try:
        if platform.system() == "Windows":
            return _process_table_windows()
        return _process_table_posix()
    except OSError:
        return {}


def homing_of(pid: int, table: dict[int, dict]) -> str:
    """Classify ``pid``'s homing by walking its ancestry.

    ``mux`` if any ancestor image name is ``tmux``/``psmux``; ``bare`` if the
    ancestry is walkable but contains no multiplexer; ``unknown`` if ``pid`` is
    absent from the table.
    """
    if pid not in table:
        return "unknown"
    seen: set[int] = set()
    cur = pid
    guard = 0
    while cur in table and cur not in seen and guard < 64:
        seen.add(cur)
        name = table[cur]["name"]
        if any(m in name for m in _MUX_NAMES):
            return "mux"
        cur = table[cur]["ppid"]
        guard += 1
    return "bare"


def descendants_of(pid: int, table: dict[int, dict]) -> set[int]:
    """Return every transitive child pid of ``pid`` (not including ``pid``)."""
    children: dict[int, list[int]] = {}
    for p, info in table.items():
        children.setdefault(info["ppid"], []).append(p)
    out: set[int] = set()
    stack = list(children.get(pid, []))
    while stack:
        c = stack.pop()
        if c in out or c == pid:
            continue
        out.add(c)
        stack.extend(children.get(c, []))
    return out


# ---------------------------------------------------------------------------
# Resolution -- the authoritative pid<->session<->worktree binding
# ---------------------------------------------------------------------------

def _session_cwd(entry: Path) -> str:
    """Read a session dir's recorded working directory (``workspace.yaml``)."""
    ws = entry / "workspace.yaml"
    if not ws.exists():
        return ""
    try:
        import yaml
        with open(ws, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    cwd = data.get("cwd", "")
    return cwd if isinstance(cwd, str) else ""


def _lock_pids(entry: Path) -> list[int]:
    """Parse the pids from a session dir's ``inuse.<pid>.lock`` files."""
    pids: list[int] = []
    for lock_file in entry.glob("inuse.*.lock"):
        parts = lock_file.stem.split(".")
        if len(parts) >= 2:
            try:
                pids.append(int(parts[1]))
            except ValueError:
                continue
    return pids


def _cwd_under(cwd: str, root: str) -> bool:
    """True when ``cwd`` is ``root`` or a descendant (case-insensitive on Win)."""
    if not cwd or not root:
        return False
    a = sessions._normalize_path(cwd)
    b = sessions._normalize_path(root)
    if platform.system() == "Windows":
        a, b = a.lower(), b.lower()
    return a == b or a.startswith(b + "/") or a.startswith(b + "\\")


def _worktree_id_from_path(cwd: str) -> str | None:
    """Best-effort worktree id from a checkout path (no project context needed).

    A linked worktree lives under a ``*.worktrees`` (or ``.worktrees/<project>``)
    directory whose leaf is the worktree id. Used to label a bound Copilot when
    the tracking-backed resolver has no active project (e.g. the command runs
    from a neutral cwd like ``~``).
    """
    if not cwd:
        return None
    # Parse with the flavor the *string* implies, not the host's -- a Windows
    # checkout path (drive letter or backslash) must split into segments even
    # when this runs on Linux (and vice versa), so the reaper is host-portable
    # and the pure logic is testable on any OS.
    if "\\" in cwd or (len(cwd) >= 2 and cwd[1] == ":" and cwd[0].isalpha()):
        parts = PureWindowsPath(cwd).parts
    else:
        parts = PurePosixPath(cwd).parts
    for i, seg in enumerate(parts):
        if seg.endswith(".worktrees") or seg == ".worktrees":
            # leaf under the .worktrees container is the worktree id
            return parts[-1] if len(parts) > i + 1 else None
    return None


def _resolve_worktree_id_for_cwd(cwd: str) -> str | None:
    """Resolve a session cwd to a worktree id, project-context-tolerant.

    Prefers the authoritative tracking lookup; falls back to a path heuristic
    when no active project is resolvable (the reaper must work from any cwd).
    """
    if not cwd:
        return None
    try:
        wt = tracking.find_worktree_id_by_cwd(cwd)
        if wt:
            return wt
    except Exception:
        pass
    return _worktree_id_from_path(cwd)


def resolve_bound_copilots(
    *,
    session_id: str | None = None,
    worktree_id: str | None = None,
    worktree_path: str | None = None,
    table: dict[int, dict] | None = None,
) -> list[dict]:
    """Resolve the live Copilot process(es) bound to a session/worktree.

    The binding is authoritative: a process is reported only when a live
    ``inuse.<pid>.lock`` for it exists inside a session-state dir AND the pid is
    a live Copilot process (guards pid-reuse). Detached parent-continuation
    sessions are skipped (they reuse a foreign cwd and must never be attributed
    to a worktree).

    Filtering (any combination; all applied):

    * ``session_id`` -- exact session dir name, or an unambiguous prefix.
    * ``worktree_id`` -- the session's cwd resolves to this worktree id.
    * ``worktree_path`` -- the session's cwd is at/under this path.

    With no filter, every bound Copilot on the machine is returned. Each item::

        {session_id, pid, cwd, worktree_id, homing}

    ``homing`` is ``mux``/``bare``/``unknown`` (see module docstring).
    """
    table = build_process_table() if table is None else table
    state_dir = sessions._session_state_dir()
    results: list[dict] = []
    if not state_dir.exists():
        return results

    _posix = platform.system() != "Windows"
    _pane_ttys: list = [None]  # lazy, memoized tmux pane-tty set (POSIX only)

    for entry in sorted(state_dir.iterdir()):
        if not entry.is_dir():
            continue

        sid = entry.name
        if session_id and not (sid == session_id or sid.startswith(session_id)):
            continue

        # Cheap gate FIRST: only a dir with a live bound Copilot is of interest.
        # The vast majority of session-state dirs are historical (no live lock),
        # so resolve the lock pids -- a cheap glob -- before paying for the
        # workspace.yaml read + worktree-id resolution below. This keeps the
        # scan O(live sessions) instead of O(all sessions ever), which matters
        # on the picker/list hot path where hundreds of dirs accumulate.
        live_pids = [
            pid for pid in _lock_pids(entry)
            if sessions._is_process_alive(pid)
            and sessions._is_copilot_process(pid)
        ]
        if not live_pids:
            continue

        if sessions._is_detached_session(entry):
            continue

        cwd = _session_cwd(entry)
        wt_id = _resolve_worktree_id_for_cwd(cwd) if cwd else None
        if wt_id is None:
            try:
                wt_id = tracking.find_worktree_id_by_session(sid)
            except Exception:
                wt_id = None

        if worktree_id and wt_id != worktree_id:
            continue
        if worktree_path and not _cwd_under(cwd, worktree_path):
            continue

        for pid in live_pids:
            homing = homing_of(pid, table)
            if homing == "bare" and _posix:
                # reptyr adopts a bare Copilot into a tmux pane by moving its
                # controlling terminal (not its ppid), so a ppid-only walk still
                # reads it as bare. Upgrade to mux when its tty is a tmux pane.
                # Fetch pane ttys ONCE, lazily, and only when a bare-by-ppid
                # candidate actually appears -- keeping the common (all-mux) case
                # off the tmux subprocess on the hot path.
                if _pane_ttys[0] is None:
                    from . import remux
                    _pane_ttys[0] = remux.tmux_pane_ttys()
                if _pane_ttys[0]:
                    from . import remux
                    tty = remux.process_tty(pid)
                    if tty and tty in _pane_ttys[0]:
                        homing = "mux"
            results.append({
                "session_id": sid,
                "pid": pid,
                "cwd": cwd,
                "worktree_id": wt_id,
                "homing": homing,
            })
    return results


# ---------------------------------------------------------------------------
# Surfacing -- read-only bare-orphan discovery (derive-don't-duplicate)
# ---------------------------------------------------------------------------

def find_bare_orphans(
    *, table: dict[int, dict] | None = None, self_pid: int | None = None,
) -> list[dict]:
    """Machine-wide **bare** (un-muxed) bound Copilots, minus this process's tree.

    A read-only convenience over :func:`resolve_bound_copilots` for *surfacing*
    orphans (e.g. ``doctor``, the picker): returns every bound Copilot whose
    ``homing`` is ``bare`` -- a session launched straight in a terminal, so it is
    invisible to the ``wt-<id>`` mux fleet view and lingers un-reapable when its
    terminal is closed or wedged. The caller's own session subtree is excluded
    (never report *this* Copilot, or one of its ancestors, as an orphan), mirroring
    the self-guard in ``cmd_reclaim``. Each item::

        {session_id, pid, worktree_id, cwd}

    Stores no state; everything is derived at read time. Best-effort: an empty
    list when nothing is bound (or enumeration failed).
    """
    table = build_process_table() if table is None else table
    me = os.getpid() if self_pid is None else self_pid
    out: list[dict] = []
    for f in resolve_bound_copilots(table=table):
        if f.get("homing") != "bare":
            continue
        subtree = {f["pid"]} | descendants_of(f["pid"], table)
        if me in subtree:
            continue
        out.append({
            "session_id": f["session_id"],
            "pid": f["pid"],
            "worktree_id": f["worktree_id"],
            "cwd": f["cwd"],
        })
    return out


def bare_orphan_worktree_ids(
    *, table: dict[int, dict] | None = None, self_pid: int | None = None,
) -> set[str]:
    """The set of worktree ids that currently host a **bare** bound Copilot.

    A convenience over :func:`find_bare_orphans` for row-level surfacing (the
    picker): reduces the machine-wide bare-orphan list to the distinct worktree
    ids so a caller can annotate each worktree's row with an "orphan" marker in
    one pass. Orphans whose cwd resolves to no worktree id are dropped (nothing
    to annotate). Best-effort; empty set when nothing is bound.
    """
    return {
        o["worktree_id"]
        for o in find_bare_orphans(table=table, self_pid=self_pid)
        if o.get("worktree_id")
    }


# ---------------------------------------------------------------------------
# Session-state lattice -- the bridge-lock layer (#4272)
# ---------------------------------------------------------------------------

def live_bridge_worktrees() -> set[str]:
    """Worktree ids that currently host a **live bridge-owned Copilot session**.

    The bridge-lock layer of the session-state lattice (#4272): agent-bridge
    writes a provable-liveness ``bridge.lock`` (see :mod:`agent_worktrees.locks`)
    into each Copilot session-state dir it owns, carrying the bound worktree id.
    This reads them **file-first** -- a cheap ``stat`` sweep of
    ``<session-state>/*/bridge.lock`` -- and returns the worktree ids whose lock
    is provably live (owner pid alive AND start-time matches; a crashed owner
    leaves a detectably stale lock that is skipped).

    This is the cwd-independent, cheap replacement for the off-hot-path
    ``bound_live`` reconciler at surfacing a **bare-resumed / bridge-owned**
    session (cwd=home, invisible to the mux + registered-session scans -- #1416):
    the lock *carries* the worktree id, so no attribution guess is needed.
    Best-effort: an enumeration hiccup yields an empty set, never an exception.
    """
    out: set[str] = set()
    try:
        state_dir = sessions._session_state_dir()
        if not state_dir.exists():
            return out
        for lock_path in state_dir.glob("*/bridge.lock"):
            data = locks.read_lock(lock_path)
            if data and locks.lock_is_live(data):
                wt = data.get("worktree_id")
                if wt:
                    out.add(str(wt))
    except OSError:
        return out
    return out


def resolve_bridge_bound(
    worktree_id: str,
    *,
    table: dict[int, dict] | None = None,
) -> list[dict]:
    """Reap targets for a worktree's **live bridge-owned** Copilot session(s).

    The Reclaim counterpart to :func:`live_bridge_worktrees`. A bare-resumed /
    bridge-owned session runs with ``cwd=home`` and is often not registered
    under the worktree, so :func:`resolve_bound_copilots` -- keyed on
    ``cwd -> worktree_id`` -- cannot see it. The ``bridge.lock`` (#4272) carries
    the bound ``worktree_id`` **and** the owner pid directly, so this reads it
    file-first and returns targets shaped like ``resolve_bound_copilots`` items
    (plus the ``bridge_lock`` path) for exactly the locks that are provably live
    and match ``worktree_id``. A crashed owner leaves a stale lock that
    :func:`locks.lock_is_live` rejects, so a dead session is never targeted.

    Best-effort: an enumeration hiccup yields an empty list, never an exception.
    """
    out: list[dict] = []
    if not worktree_id:
        return out
    table = build_process_table() if table is None else table
    try:
        state_dir = sessions._session_state_dir()
        if not state_dir.exists():
            return out
        for lock_path in state_dir.glob("*/bridge.lock"):
            data = locks.read_lock(lock_path)
            if not (data and locks.lock_is_live(data)):
                continue
            if str(data.get("worktree_id") or "") != worktree_id:
                continue
            pid = data.get("pid")
            if not isinstance(pid, int):
                continue
            out.append({
                "session_id": lock_path.parent.name,
                "pid": pid,
                "cwd": None,
                "worktree_id": worktree_id,
                "homing": homing_of(pid, table),
                "bridge_lock": str(lock_path),
            })
    except OSError:
        return out
    return out


def clear_bridge_locks(
    worktree_id: str,
    *,
    force_pids: set[int] | None = None,
    table: dict[int, dict] | None = None,
) -> list[dict]:
    """Remove ``bridge.lock`` residue for a worktree after a Reclaim.

    The bridge-lock counterpart to :func:`clear_lock_residue`: a Copilot reaped
    out from under agent-bridge can't run the bridge's on-exit lock cleanup, so
    its ``bridge.lock`` would linger. This unlinks each ``<session>/bridge.lock``
    whose recorded ``worktree_id`` matches and whose owner is no longer a live
    Copilot (or was just force-killed by this reclaim). A still-live bridge
    owner's lock is **kept** -- never strip a lock out from under a running
    session. Best-effort; a per-file ``OSError`` is skipped. Returns the removed
    residue as ``[{session_id, pid, path}]``.
    """
    removed: list[dict] = []
    if not worktree_id:
        return removed
    force_pids = set(force_pids or [])
    state_dir = sessions._session_state_dir()
    if not state_dir.exists():
        return removed
    for lock_path in state_dir.glob("*/bridge.lock"):
        data = locks.read_lock(lock_path)
        if not data:
            continue
        if str(data.get("worktree_id") or "") != worktree_id:
            continue
        pid = data.get("pid")
        live = (
            isinstance(pid, int)
            and sessions._is_process_alive(pid)
            and sessions._is_copilot_process(pid)
        )
        if live and pid not in force_pids:
            continue
        try:
            lock_path.unlink()
            removed.append({
                "session_id": lock_path.parent.name,
                "pid": pid,
                "path": str(lock_path),
            })
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------------------
# Reaping -- precise, subtree-scoped termination
# ---------------------------------------------------------------------------

def reap_bound_copilots(
    targets: list[dict],
    *,
    table: dict[int, dict] | None = None,
    include_children: bool = True,
) -> list[dict]:
    """Terminate exactly the resolved target processes (and their child tree).

    Precise by construction: only the pids in ``targets`` -- and, when
    ``include_children`` is set, their transitive **Copilot** descendants (the
    per-session preload/extension children the CLI spawns) -- are killed. No
    sibling session, and no unrelated process that merely shares a working
    directory, is touched.

    Returns one result per input target::

        {session_id, pid, worktree_id, homing, killed, children_killed}
    """
    from . import procs

    table = build_process_table() if table is None else table
    out: list[dict] = []
    for t in targets:
        pid = t["pid"]
        child_pids: list[int] = []
        if include_children:
            for c in descendants_of(pid, table):
                # Only reap Copilot descendants -- never an unrelated child that
                # a shell in the pane happened to spawn.
                if sessions._is_copilot_process(c):
                    child_pids.append(c)
        # Kill children first so the parent's exit can't re-home them.
        children_killed = 0
        for c in child_pids:
            if procs.terminate_pid(c):
                children_killed += 1
        killed = procs.terminate_pid(pid)
        out.append({
            "session_id": t.get("session_id"),
            "pid": pid,
            "worktree_id": t.get("worktree_id"),
            "homing": t.get("homing"),
            "killed": killed,
            "children_killed": children_killed,
        })
    return out


def clear_lock_residue(
    *,
    worktree_id: str | None = None,
    worktree_path: str | None = None,
    force_pids: set[int] | None = None,
    table: dict[int, dict] | None = None,
) -> list[dict]:
    """Remove ``inuse.<pid>.lock`` residue for a worktree's session dirs.

    The lock-file counterpart to :func:`reap_bound_copilots`: a *killed* Copilot
    can't run its own on-exit lock cleanup, and a crashed session leaves a
    **stale** lock whose pid is long dead -- either way the ``inuse.<pid>.lock``
    lingers and keeps the worktree looking bound. This sweeps the session-state
    dirs attributable to the worktree and unlinks every lock file that is NOT
    held by a live Copilot, so Reclaim/Repair leave the worktree with **zero**
    residue ("to the point where the pid lock file is removed").

    Removal rule per lock file:

    * pid in ``force_pids`` (a pid this reclaim just terminated -- it may not be
      reaped by the OS yet, so trust the kill) -> remove.
    * pid is NOT a live Copilot process (stale/dead) -> remove.
    * pid IS a live Copilot (a preserved muxed sibling on the same worktree) ->
      **kept** -- never strip a lock out from under a running session.

    Attribution matches :func:`resolve_bound_copilots`: a session dir belongs to
    the worktree when its recorded cwd resolves to ``worktree_id`` (or is at/under
    ``worktree_path``), with a tracking-by-session fallback. Best-effort; a
    per-file ``OSError`` is skipped, never raised. Returns the removed residue as
    ``[{session_id, pid, path}]``.
    """
    force_pids = set(force_pids or [])
    state_dir = sessions._session_state_dir()
    removed: list[dict] = []
    if not state_dir.exists():
        return removed
    for entry in sorted(state_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Cheap gate: skip a dir with no lock file at all before paying for the
        # cwd read + worktree resolution.
        if not any(entry.glob("inuse.*.lock")):
            continue
        cwd = _session_cwd(entry)
        wt = _resolve_worktree_id_for_cwd(cwd) if cwd else None
        if wt is None:
            try:
                wt = tracking.find_worktree_id_by_session(entry.name)
            except Exception:
                wt = None
        matches = False
        if worktree_id is not None and wt == worktree_id:
            matches = True
        if worktree_path is not None and _cwd_under(cwd, worktree_path):
            matches = True
        if not matches:
            continue
        for lock_file in entry.glob("inuse.*.lock"):
            parts = lock_file.stem.split(".")
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            live = (
                sessions._is_process_alive(pid)
                and sessions._is_copilot_process(pid)
            )
            if live and pid not in force_pids:
                continue
            try:
                lock_file.unlink()
                removed.append({
                    "session_id": entry.name,
                    "pid": pid,
                    "path": str(lock_file),
                })
            except OSError:
                pass
    return removed
