"""Small OS utilities for the Session Host layer (packaged, not the spike copy)."""

from __future__ import annotations

import os
import sys

from agent_procutil import no_window_kwargs

_STILL_ACTIVE = 259


def pid_alive(pid: int | None) -> bool:
    """Cross-platform: is ``pid`` a currently-running process?"""
    if not pid:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _posix_descendant_pids(pid: int) -> list[int]:
    """Best-effort descendant pids of ``pid`` via ``/proc`` (Linux).

    Returns the transitive children of ``pid`` (shallow-to-deep order), or an
    empty list on any failure / non-Linux. Used to give :func:`kill_pid` a real
    tree-kill on POSIX (the Windows branch already collects the tree via
    ``taskkill /T``), so reaping a Session Host's copilot child also reaps the
    MCP-bridge / sub-agent processes copilot spawned (dotfiles #911).
    """
    if not sys.platform.startswith("linux"):
        return []
    children: dict[int, list[int]] = {}
    try:
        entries = [e for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return []
    for e in entries:
        try:
            with open(f"/proc/{e}/stat", "rb") as f:
                data = f.read()
            # comm (field 2) is parenthesized and may contain spaces/parens;
            # ppid is the 2nd whitespace token AFTER the final ')'.
            rparen = data.rfind(b")")
            ppid = int(data[rparen + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(e))
    out: list[int] = []
    seen: set[int] = set()
    stack = [pid]
    while stack:
        p = stack.pop()
        for c in children.get(p, ()):
            if c not in seen:
                seen.add(c)
                out.append(c)
                stack.append(c)
    return out


def kill_pid(pid: int | None, *, force: bool = False) -> None:
    """Best-effort tree-kill of a process by pid (cross-platform, idempotent).

    Used by the frontend to reap a Session Host process (and, via the host's
    kill-on-close job / process group, its child) once the host's session has
    reached its own stop or is being force-reaped. ``force`` sends SIGKILL
    (POSIX) for a prompt, definite death -- appropriate for a reap where the
    child has already been handled and we must not leave the host lingering.
    Swallows "already gone" and permission errors -- reaping is best-effort.
    """
    if not pid:
        return
    if sys.platform == "win32":
        import subprocess

        # taskkill /F already forces; /T collects the tree either way.
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            **no_window_kwargs(),
        )
        return
    import signal

    # Real tree-kill on POSIX (mirrors the Windows ``taskkill /T``): reap the
    # descendants copilot spawned (MCP bridges, sub-agents) too, not just the
    # single pid -- otherwise they orphan and accumulate (dotfiles #911).
    # Descendants first, then the root, so a parent can't respawn a child.
    sig = signal.SIGKILL if force else signal.SIGTERM
    for target in (*_posix_descendant_pids(pid), pid):
        try:
            os.kill(target, sig)
        except (ProcessLookupError, PermissionError):
            pass


def set_pdeathsig(sig: int | None = None) -> None:
    """POSIX/Linux: ask the kernel to signal THIS process when its PARENT dies.

    The Linux counterpart to the Windows kill-on-close job (``winjob``): it makes
    a Session Host's copilot child die together with the host even on a hard host
    kill, so a dropped SSH tunnel / host sleep on a remote (mesh or CodeSpace) far
    side never orphans copilot there (#66's orphan class, remote-side). Meant to
    run from a child ``preexec_fn`` (after fork, before exec). Best-effort: a
    no-op on non-Linux or where ``prctl`` is unavailable.
    """
    if not sys.platform.startswith("linux"):
        return
    import ctypes
    import signal as _signal

    pr_set_pdeathsig = 1  # from <sys/prctl.h>
    sig = int(sig if sig is not None else _signal.SIGKILL)
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(pr_set_pdeathsig, sig, 0, 0, 0)
    except (OSError, AttributeError):
        return
    # Race: if the parent already exited between fork and now, we were reparented
    # to init (ppid == 1) and PDEATHSIG will never fire -- exit rather than orphan.
    if os.getppid() == 1:
        os._exit(1)


def child_preexec():
    """Return a ``preexec_fn`` that arms :func:`set_pdeathsig` (POSIX only).

    ``None`` on Windows, where ``preexec_fn`` is unsupported (the host's
    kill-on-close job covers the same "child dies with host" guarantee there).
    """
    if sys.platform == "win32":
        return None

    def _fn() -> None:
        set_pdeathsig()

    return _fn


def reap_zombie(pid: int | None, *, attempts: int = 30, delay: float = 0.01) -> None:
    """POSIX: ``wait()`` on a just-killed child so it doesn't linger as a zombie.

    A Session Host spawned by *this* daemon is our direct child; SIGKILLing it
    leaves a ``<defunct>`` zombie until we reap it. This clears it. No-op on
    Windows (``taskkill /F`` fully removes the process) and when ``pid`` is not
    our child -- e.g. a host reattached from a *previous* daemon, which init
    reaps instead (``ChildProcessError``).
    """
    if not pid or sys.platform == "win32":
        return
    import time as _time

    for _ in range(attempts):
        try:
            reaped, _status = os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            return  # not our child (reattached host) -> reaped by init
        if reaped:
            return
        _time.sleep(delay)
