"""Reap superseded coordinator processes (single-active-coordinator invariant).

A graceful coordinator cutover (``zdd`` ``CutoverOrchestrator``) retires only the
*one* predecessor it replaces -- it sends ``old_client.shutdown()`` to the daemon
whose route it just took over. It does **not** reconcile the *full set* of live
coordinators. Across repeated updates and restarts that gap leaks processes:

- a cutover spawns its replacement as a **detached** ``serve --passive`` process;
  once *that* is superseded by a *later* cutover whose predecessor is a
  **different** process (e.g. a plain ``serve`` restart that re-published the
  routing table over the top), the earlier detached passive is never anyone's
  "old" and so is never sent ``/shutdown``;
- a plain (non-cutover) ``serve`` restart re-publishes routing to itself but does
  not retire the coordinator it displaced.

Each straggler keeps a bound port and tens of MB of RSS, indefinitely, until a
human cleans up. This module closes the gap: given the authoritative active
coordinator (the ``active`` entry of the routing table), it terminates every
*other* live coordinator process.

Design:

- **Pure core, IO at the edges.** :func:`is_coordinator_cmdline` and
  :func:`select_superseded_pids` are pure and unit-tested; enumeration and
  termination are thin, injectable seams.
- **Precise matching.** Only the coordinator (``agent_dispatch serve``) is
  matched -- never the supervisor (``agent_dispatch supervise serve``), the
  scheduler (``agent_dispatch schedule serve``), a worker, or the ``_cutover``
  helper itself. The subcommand token immediately after ``agent_dispatch`` /
  ``agent-dispatch`` must be exactly ``serve``.
- **Fail-soft.** Enumeration or termination failures are logged and swallowed;
  the reaper never raises into its caller (the queue must never fail to serve
  because a stray could not be reaped). It also refuses to act when it cannot
  anchor on an active pid, so it can never terminate the *only* coordinator.
"""

from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
from dataclasses import dataclass, field

from .procutil import no_window_kwargs

log = logging.getLogger(__name__)

_ENUM_TIMEOUT_S = 15.0
_COORD_NAMES = ("agent_dispatch", "agent-dispatch")


@dataclass(frozen=True)
class CoordProc:
    """A live coordinator (``agent_dispatch serve``) process."""

    pid: int
    cmdline: str


@dataclass
class ReapResult:
    """Outcome of a reap pass (best-effort; never raised)."""

    reaped: list[int] = field(default_factory=list)
    skipped_keep: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _basename(token: str) -> str:
    """The final path component of ``token`` (handles ``/`` and ``\\``)."""
    return token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def is_coordinator_cmdline(cmdline: str) -> bool:
    """True when ``cmdline`` is an ``agent_dispatch serve`` coordinator.

    Matches the module form (``python -m agent_dispatch serve ...``) and the
    binstub form (``.../agent-dispatch serve ...``). Deliberately rejects the
    supervisor (``... agent_dispatch supervise serve``), the scheduler
    (``... agent_dispatch schedule serve``), and the ``_cutover`` helper: the
    token immediately after the ``agent_dispatch`` / ``agent-dispatch`` program
    token must be exactly ``serve``.
    """
    if not cmdline:
        return False
    try:
        toks = shlex.split(cmdline, posix=(os.name != "nt"))
    except ValueError:
        toks = cmdline.split()
    for i in range(len(toks) - 1):
        if _basename(toks[i]) in _COORD_NAMES:
            return toks[i + 1] == "serve"
    return False


def parse_ps_output(text: str) -> list[CoordProc]:
    """Parse ``ps -eo pid=,args=`` output into coordinator procs (pure)."""
    procs: list[CoordProc] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_s, args = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if is_coordinator_cmdline(args):
            procs.append(CoordProc(pid=pid, cmdline=args))
    return procs


def parse_win_output(text: str) -> list[CoordProc]:
    """Parse ``<pid>\\t<commandline>`` lines (Win32_Process) into procs (pure)."""
    procs: list[CoordProc] = []
    for line in text.splitlines():
        if "\t" not in line:
            continue
        pid_s, cmd = line.split("\t", 1)
        try:
            pid = int(pid_s.strip())
        except ValueError:
            continue
        if is_coordinator_cmdline(cmd):
            procs.append(CoordProc(pid=pid, cmdline=cmd))
    return procs


def _iter_posix() -> list[CoordProc]:
    argv = ["ps", "-eo", "pid=,args="]
    out = subprocess.run(  # noqa: S603 -- fixed argv, no shell; ps via PATH by design
        argv, capture_output=True, text=True, timeout=_ENUM_TIMEOUT_S, check=False,
    )
    return parse_ps_output(out.stdout or "")


def _iter_windows() -> list[CoordProc]:
    # Enumerate via CIM (the same source the installers use). Best-effort: any
    # failure yields an empty list, so the reaper simply no-ops on Windows.
    ps_script = (
        "Get-CimInstance Win32_Process | "
        "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
    )
    argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script]
    out = subprocess.run(  # noqa: S603 -- fixed argv, no shell; powershell via PATH
        argv, capture_output=True, text=True, timeout=_ENUM_TIMEOUT_S, check=False,
        **no_window_kwargs(),
    )
    return parse_win_output(out.stdout or "")


def iter_coordinator_processes() -> list[CoordProc]:
    """Enumerate live coordinator processes (best-effort; ``[]`` on failure)."""
    try:
        return _iter_windows() if os.name == "nt" else _iter_posix()
    except Exception:  # best-effort: enumeration must never raise into a caller
        log.debug("coordinator enumeration failed", exc_info=True)
        return []


def select_superseded_pids(
    procs: list[CoordProc], keep_pids: set[int],
) -> list[int]:
    """Pure: pids in ``procs`` that are not in ``keep_pids`` (to be reaped)."""
    return [p.pid for p in procs if p.pid not in keep_pids]


def terminate_pid(pid: int) -> bool:
    """Terminate ``pid`` (SIGTERM on POSIX -> uvicorn drains + exits cleanly;
    ``TerminateProcess`` on Windows via ``os.kill``). ``True`` on a delivered
    signal or an already-gone process; ``False`` on a real failure."""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return True  # already gone -- the desired end state
    except (PermissionError, OSError):
        log.debug("could not terminate coordinator pid=%s", pid, exc_info=True)
        return False


def reap_superseded_coordinators(
    *,
    keep_pids,
    list_procs=iter_coordinator_processes,
    terminate=terminate_pid,
) -> ReapResult:
    """Terminate every live coordinator except those in ``keep_pids``.

    ``keep_pids`` must include the authoritative active coordinator (the routing
    table's ``active`` pid) and this process. If it is empty the pass is a no-op
    (the reaper never terminates the sole coordinator when it cannot anchor on an
    active). Best-effort and fail-soft: returns a :class:`ReapResult`, never
    raises.
    """
    keep = {int(p) for p in keep_pids if p}
    result = ReapResult(skipped_keep=sorted(keep))
    if not keep:
        result.errors.append("no active pid to anchor on; reap skipped")
        log.debug("reap skipped: empty keep set")
        return result
    try:
        procs = list_procs()
    except Exception as exc:  # best-effort: enumeration failure is non-fatal
        result.errors.append(f"enumeration failed: {exc}")
        return result
    for pid in select_superseded_pids(procs, keep):
        if terminate(pid):
            result.reaped.append(pid)
        else:
            result.errors.append(f"terminate pid={pid} failed")
    if result.reaped:
        log.info("reaped %d superseded coordinator(s): %s",
                 len(result.reaped), result.reaped)
    return result
