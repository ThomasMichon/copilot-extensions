"""Orphaned launcher-shell reaper predicates, extracted from ``__main__``.

Pure predicates and platform enumerators for the orphaned launcher-shell
sweep (copilot-extensions #102); see ``reap_cli.py`` for the stateful
``reap_orphan_launcher_shells`` caller that wires these together with the
real process table and kill action.
"""

from __future__ import annotations

import json
import platform
import subprocess
from collections.abc import Callable


def _core():
    from . import __main__ as core

    return core




# ═══════════════════════════════════════════════════════════════════════════
# Orphaned launcher-shell reaper (copilot-extensions #102)
# ═══════════════════════════════════════════════════════════════════════════
# After a worktree session ends cleanly its launcher shells (the pwsh running
# launch-session.ps1 and the `python -m agent_worktrees` waiter) exit with it.
# But a *force-closed* terminal (window closed with the X, a dropped SSH pipe)
# can strand them: the console dies, the shells are re-parented away from a now-
# dead pid, nothing runs under them -- yet they pin memory indefinitely. This
# sweep reclaims those, closing the same intent as the mux reaper
# (visions/agent-fabric §Features/reclaim-idle-process).
#
# SAFETY -- this KILLS processes, so it is engineered to fail SAFE. A live
# telemetry sampler was once wrongly killed because a non-elevated query made a
# hidden scheduled-task service (blank command line, exited parent) look exactly
# like an orphan. The lesson is baked in as independent layers, EVERY one of
# which must pass before a pid is even a candidate:
#   1. POSITIVE signature only. A pid is a candidate ONLY if its command line
#      positively matches an agent-worktrees launcher marker. A service with a
#      blank/absent command line can NEVER match -- we never reap "things that
#      merely look orphaned".
#   2. Service/daemon veto. A session-0 (service) pid, or one whose command line
#      bears a daemon/service/ACP marker, is skipped even if it matched (1).
#   3. Liveness gate. A shell with a live descendant (copilot/node, or a mux
#      client) is a LIVE session and is always spared.
#   4. Self-preservation. The reaper never touches its own process tree.
#   5. Orphan + idle gates. Only a shell whose parent has exited AND that has
#      been alive past the grace window is eligible.
#   6. Dry-run by DEFAULT. Unlike the mux reaper, nothing is killed unless the
#      caller explicitly passes --yes; the default is a report.

REAP_SHELL_GRACE_SECS = 3600  # 1h: an orphaned launcher shell must be this old

# Process image names this reaper is willing to consider (lowercased).
_LAUNCHER_SHELL_NAMES = frozenset(
    {
        "pwsh.exe",
        "powershell.exe",
        "python.exe",
        "pwsh",
        "powershell",
        "python",
        "python3",
    }
)
# Command-line substrings that POSITIVELY identify an agent-worktrees launcher
# shell (lowercased match). Nothing is EVER reaped without one of these.
_LAUNCHER_SIGNATURES = ("launch-session", "-m agent_worktrees", "agent_worktrees.__main__")
# Command-line substrings that VETO a reap even when a launcher signature is
# present -- services/daemons, ACP/stdio sessions, and the reaper's own verbs.
_LAUNCHER_REAP_VETOES = (
    "serve-service",
    "agent_dispatch",
    "agent-dispatch",
    "telemetry",
    "status-updater",
    "status-monitor",
    "vault",
    "--acp",
    "--stdio",
    "reap-shells",
    "reap_shells",
    "reap-sessions",
)
# Descendant image names that mark a LIVE session under a launcher shell ->
# spare it. Deliberately broad: over-sparing is safe, over-reaping is not.
_LIVE_DESCENDANT_NAMES = ("copilot", "node", "tmux", "psmux")
# Concrete process images the enumerators must snapshot **in addition to**
# _LAUNCHER_SHELL_NAMES, purely so the live-descendant veto above can see them.
# They are never reap candidates (the candidate loop gates on
# _LAUNCHER_SHELL_NAMES); they exist only to make the parent/child table
# complete. Without them the veto is dead code: a launcher whose foreground
# child is `psmux attach-session` looked childless, so an attached, working
# session was reaped out from under its terminal -- killing the launcher shell
# while its mux client kept rendering, leaving the pane painted but the console
# handed back to the parent shell.
_LIVE_DESCENDANT_IMAGES = frozenset(
    {
        "copilot.exe",
        "node.exe",
        "tmux.exe",
        "psmux.exe",
        "copilot",
        "node",
        "tmux",
        "psmux",
    }
)


def _ancestor_chain_intact(
    ppid: int,
    by_pid: dict[int, dict],
    pid_alive: Callable[[int], bool] | None,
) -> bool:
    """Whether ``ppid`` and every ancestor above it, as far as verifiable, is
    still alive -- i.e. whether the process whose parent is ``ppid`` is
    genuinely parented rather than an orphan whose immediate parent happens to
    still be a live (but itself orphaned/stuck) intermediate node.

    Walking past the immediate parent matters for exactly the launcher-shell
    chains this reaper targets: ``agent-worktrees.ps1 -> python -> python ->
    pwsh launch-session.ps1`` stacks several of *this reaper's own* process
    names on top of each other, so an intermediate hop's parent can be dead
    even while the immediate parent (one level down) is still alive.

    Without a real ``pid_alive`` probe (pure/test mode), this degrades to the
    original single-hop snapshot-membership check: a filtered snapshot cannot
    distinguish "not enumerated" from "dead" for anything beyond one hop, so
    walking further would misclassify a live-but-unenumerated terminal as
    dead. With a real probe, walking continues past the immediate parent as
    long as each hop it can still see in ``by_pid`` is confirmed alive,
    stopping (and assuming intact) the moment it runs off the edge of what
    was enumerated -- never the moment it merely can't verify further.
    """
    if pid_alive is None:
        return ppid in by_pid
    cur = ppid
    guard = 0
    while cur > 0 and guard < 128:
        if not bool(pid_alive(cur)):
            return False
        node = by_pid.get(cur)
        if node is None:
            return True  # edge of the snapshot; alive so far, can't see further
        next_ppid = int(node.get("ppid", -1) or -1)
        if next_ppid <= 0 or next_ppid == cur:
            return True  # reached the top of a fully-verified chain
        cur = next_ppid
        guard += 1
    return True


def select_orphan_launcher_shells(
    procs: list[dict],
    *,
    now: float,
    idle_grace_secs: float,
    self_pid: int,
    pid_alive: Callable[[int], bool] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Pure predicate: partition launcher shells into (reap, skipped).

    ``procs`` is a list of process dicts with keys ``pid``, ``ppid``, ``name``,
    ``cmdline``, ``create_epoch`` (float|None), ``session_id`` (int). Pure and
    deterministic -- no process I/O of its own -- so the full safety predicate is
    unit testable. ``skipped`` entries carry a ``reason`` for legibility.

    ``pid_alive`` is the **injected** parent-liveness probe. ``procs`` is a
    filtered snapshot (launcher shells plus live-descendant witnesses), so
    membership in it cannot answer "is this pid alive?": a launcher started from
    ``cmd.exe``/``bash``/Windows Terminal has a parent that was never enumerated
    and so looks parentless, i.e. an orphan. Callers with a real process table
    pass a probe (``locks.pid_alive``); when it is ``None`` the check degrades to
    the snapshot-membership test, which keeps this function pure for tests.
    """
    by_pid = {int(p["pid"]): p for p in procs if p.get("pid") is not None}
    children: dict[int, list[int]] = {}
    for p in procs:
        children.setdefault(int(p.get("ppid", -1) or -1), []).append(int(p["pid"]))

    def _descendants(pid: int) -> set[int]:
        out: set[int] = set()
        stack = list(children.get(pid, []))
        while stack:
            c = stack.pop()
            if c in out or c == pid:
                continue
            out.add(c)
            stack.extend(children.get(c, []))
        return out

    def _ancestors(pid: int) -> set[int]:
        out: set[int] = set()
        cur, guard = pid, 0
        while cur in by_pid and guard < 128:
            pp = int(by_pid[cur].get("ppid", -1) or -1)
            if pp in out or pp <= 0:
                break
            out.add(pp)
            cur = pp
            guard += 1
        return out

    self_tree = {self_pid} | _descendants(self_pid) | _ancestors(self_pid)

    reap: list[dict] = []
    skipped: list[dict] = []
    for p in procs:
        pid = int(p["pid"])
        name = (p.get("name") or "").lower()
        cmd = (p.get("cmdline") or "").lower()
        if name not in _LAUNCHER_SHELL_NAMES:
            continue  # not a shell we manage -- ignored silently, never listed
        if not any(sig in cmd for sig in _LAUNCHER_SIGNATURES):
            continue  # (1) no positive launcher signature -> never a candidate
        if pid in self_tree:
            skipped.append({"pid": pid, "reason": "self"})
            continue
        sid = p.get("session_id", -1)
        if int(sid if sid is not None else -1) == 0:
            skipped.append({"pid": pid, "reason": "service-session"})  # (2)
            continue
        if any(v in cmd for v in _LAUNCHER_REAP_VETOES):
            skipped.append({"pid": pid, "reason": "service-marker"})  # (2)
            continue
        live = False
        for d in _descendants(pid):
            dn = (by_pid.get(d, {}).get("name") or "").lower()
            if any(m in dn for m in _LIVE_DESCENDANT_NAMES):
                live = True
                break
        if live:
            skipped.append({"pid": pid, "reason": "live-descendant"})  # (3)
            continue
        ppid = int(p.get("ppid", -1) or -1)
        parent_alive = _ancestor_chain_intact(ppid, by_pid, pid_alive)
        if ppid > 0 and parent_alive:
            skipped.append({"pid": pid, "reason": "parent-alive"})  # (5)
            continue
        ce = p.get("create_epoch")
        if ce is None:
            skipped.append({"pid": pid, "reason": "age-unknown"})
            continue
        if now - float(ce) < idle_grace_secs:
            skipped.append({"pid": pid, "reason": "fresh"})  # (5)
            continue
        reap.append(p)
    return reap, skipped


def _enumerate_launcher_shells() -> list[dict] | None:
    """Snapshot launcher shells **plus live-session witness processes**.

    Returns a list of ``{pid, ppid, name, cmdline, create_epoch, session_id}``
    dicts, or ``None`` if enumeration is unavailable. Best-effort and never
    raises. The witness images (``_LIVE_DESCENDANT_IMAGES``: psmux/tmux/copilot/
    node) are included so :func:`select_orphan_launcher_shells` can see a live
    child; they are never reap candidates themselves.
    """
    if platform.system() == "Windows":
        return _enumerate_launcher_shells_windows()
    # Stage D: the real POSIX implementation lives in reap_cli.
    from . import reap_cli as _reap_cli

    return _core()._self_override("_enumerate_launcher_shells_posix", _reap_cli._enumerate_launcher_shells_posix)()


def _enumerate_launcher_shells_windows() -> list[dict] | None:
    # No -Filter: candidate selection (name + positive command-line signature)
    # happens in select_orphan_launcher_shells, but the parent-alive check
    # needs to walk the FULL ancestor chain up to the real console host
    # (Windows Terminal/conhost/explorer/etc.) -- a filtered snapshot that
    # only ever contains launcher/witness images can't see past them, so a
    # stacked chain's true root (a dead terminal) would look unverifiable
    # rather than confirmed-dead. See _ancestor_chain_intact.
    ps = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine,SessionId,"
        "@{n='Create';e={try{([DateTimeOffset]$_.CreationDate)"
        ".ToUnixTimeSeconds()}catch{$null}}} | ConvertTo-Json -Compress -Depth 3"
    )
    try:
        out = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = (out.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        data = [data]
    procs: list[dict] = []
    for d in data:
        try:
            procs.append(
                {
                    "pid": int(d.get("ProcessId")),
                    "ppid": int(d.get("ParentProcessId") or -1),
                    "name": (d.get("Name") or "").lower(),
                    "cmdline": d.get("CommandLine") or "",
                    "create_epoch": (float(d["Create"]) if d.get("Create") is not None else None),
                    "session_id": int(d.get("SessionId") or -1),
                }
            )
        except (TypeError, ValueError):
            continue
    return procs


