"""Linux/WSL re-mux: reparent a live **bare** Copilot into a ``wt-<id>`` tmux pane.

Companion to :mod:`agent_worktrees.reclaim`. A **bare** Copilot -- one launched
straight in a terminal (then ``/resume``) rather than through the picker's mux
launcher -- is invisible to the ``wt-<id>`` mux fleet view and lingers as an
un-reapable orphan when its terminal is closed or wedged. ``reclaim`` frees it
(reap-and-resume). This module offers the *other* remedy available only on
Linux/WSL, where the platform supports PTY reparenting: **adopt the running
process into a tmux pane** with ``reptyr``, so no conversation is lost and the
session becomes a first-class member of the mux fleet.

Windows has no equivalent (ConPTY cannot adopt a running process), so there the
story stays "resume in a fresh ``wt-<id>`` session + ``reclaim --bare-only`` the
orphan." This module is a hard no-op with a clear message off Linux/WSL.

The reparenting subtlety this module gets right: ``reptyr`` moves a process's
**controlling terminal** (its stdio + the foreground pty) to the new pane -- it
does **not** re-parent the process (its ``ppid`` is unchanged). So a ppid-only
homing check would still call the adopted process ``bare``. Verification (and
the fleet-wide :func:`agent_worktrees.reclaim.homing_of`) therefore treats a
process whose controlling tty is a **tmux pane pty** as ``mux``.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from . import reclaim, sessions

__all__ = [
    "is_windows",
    "reptyr_path",
    "ptrace_scope",
    "process_tty",
    "tmux_pane_ttys",
    "is_in_mux_pane",
    "remux_bare_copilot",
]

# reptyr needs to attach to a process that is NOT its descendant, which the
# yama LSM's "restricted ptrace" (scope 1) and stricter modes forbid without
# CAP_SYS_PTRACE. Above this scope we run reptyr under sudo.
_PTRACE_SCOPE_NEEDS_ROOT = 1


def is_windows() -> bool:
    return platform.system() == "Windows"


def reptyr_path() -> str | None:
    """Absolute path to ``reptyr`` if installed, else ``None``."""
    return shutil.which("reptyr")


def ptrace_scope() -> int | None:
    """Read ``/proc/sys/kernel/yama/ptrace_scope`` (``None`` if absent).

    ``None`` means the yama knob does not exist -- typically an unrestricted
    kernel (no yama LSM), where reptyr can attach without elevation.
    """
    try:
        return int(Path("/proc/sys/kernel/yama/ptrace_scope")
                   .read_text().strip())
    except (OSError, ValueError):
        return None


def _needs_sudo() -> bool:
    """True when reptyr will require elevation to attach a non-descendant."""
    if os.geteuid() == 0 if hasattr(os, "geteuid") else False:
        return False
    scope = ptrace_scope()
    return scope is not None and scope >= _PTRACE_SCOPE_NEEDS_ROOT


def _tty_from_stat(stat: str) -> str | None:
    """Decode the ``/dev/pts/N`` controlling tty from a ``/proc/<pid>/stat`` line.

    Field 7 (``tty_nr``) is a ``new_encode_dev`` device number::

        encode = (minor & 0xff) | (major << 8) | ((minor >> 8) << 20)

    A UNIX98 pty slave has major 136, so a controlling tty on that major maps to
    ``/dev/pts/<minor>``. Returns ``None`` for no tty (0) or a non-pts major.
    """
    rparen = stat.rfind(")")  # comm (field 2) may contain spaces/parens
    if rparen == -1:
        return None
    rest = stat[rparen + 1:].split()
    # After comm: state(0), ppid(1), pgrp(2), session(3), tty_nr(4).
    if len(rest) < 5:
        return None
    try:
        tty_nr = int(rest[4])
    except ValueError:
        return None
    if tty_nr == 0:
        return None
    major = (tty_nr >> 8) & 0xFFF
    minor = (tty_nr & 0xFF) | ((tty_nr >> 20) << 8)
    if major != 136:  # UNIX98 pty slave major
        return None
    return f"/dev/pts/{minor}"


def process_tty(pid: int) -> str | None:
    """Return the ``/dev/pts/N`` controlling terminal of ``pid`` (POSIX).

    Reads field 7 (``tty_nr``) of ``/proc/<pid>/stat`` and decodes it (see
    :func:`_tty_from_stat`). ``None`` when the process has no controlling tty or
    cannot be read.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return _tty_from_stat(stat)


def tmux_pane_ttys(mux_bin: str | None = None) -> set[str]:
    """Set of ``/dev/pts/N`` ttys backing every current tmux pane.

    Best-effort: an unavailable/no-server tmux yields an empty set.
    """
    mux = mux_bin or "tmux"
    try:
        r = subprocess.run(
            [mux, "list-panes", "-a", "-F", "#{pane_tty}"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if r.returncode != 0:
        return set()
    return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}


def is_in_mux_pane(pid: int, pane_ttys: set[str] | None = None) -> bool:
    """True when ``pid``'s controlling tty is one of tmux's pane ttys.

    The authoritative "this process now lives in a tmux pane" signal after a
    ``reptyr`` reparent (which moves the controlling terminal, not the ppid).
    """
    tty = process_tty(pid)
    if not tty:
        return False
    ttys = tmux_pane_ttys() if pane_ttys is None else pane_ttys
    return tty in ttys


def _reptyr_pane_cmd(pid: int, *, use_sudo: bool) -> list[str]:
    """The in-pane command that adopts ``pid`` into this pane."""
    base = ["reptyr", str(pid)]
    if use_sudo:
        # -A: use SUDO_ASKPASS (vault-backed) so a detached pane need not block
        # on a tty prompt; if unset, sudo still prompts when the operator
        # attaches to the pane.
        return ["sudo", "-A", *base]
    return base


def remux_bare_copilot(
    *,
    worktree_id: str | None = None,
    session_id: str | None = None,
    worktree_path: str | None = None,
    force_sudo: bool | None = None,
    verify_timeout: float = 6.0,
) -> dict:
    """Reparent the bare Copilot bound to a worktree/session into its mux pane.

    Resolves the exact **bare** (``homing == "bare"``) Copilot bound to the
    target via ``reclaim.resolve_bound_copilots`` (authoritative lock-file
    binding, never a cwd guess), ensures the worktree's ``wt-<id>`` tmux session
    exists, opens a pane running ``reptyr <pid>`` (under ``sudo -A`` when the
    yama ptrace policy requires it), then verifies the process now homes in a
    tmux pane.

    Returns a JSON-able dict::

        {ok, reason, worktree_id, session_id, pid, session, pane,
         used_sudo, verified}

    ``ok`` is ``False`` with a human ``reason`` on any guard failure (wrong
    platform, reptyr missing, no bare target, mux/reptyr error). Never raises.
    """
    def _fail(reason: str, **extra) -> dict:
        return {"ok": False, "reason": reason, "worktree_id": worktree_id,
                "session_id": session_id, "pid": None, "session": None,
                "pane": None, "used_sudo": False, "verified": False, **extra}

    if is_windows():
        return _fail(
            "re-mux is unsupported on Windows (ConPTY cannot adopt a running "
            "process); use `reclaim --bare-only` then resume in a fresh session")

    rp = reptyr_path()
    if not rp:
        return _fail(
            "reptyr not found -- install it (e.g. `sudo apt install reptyr`) "
            "to reparent a bare Copilot into a tmux pane")

    # Resolve the exact bare bound Copilot(s) for the target.
    table = reclaim.build_process_table()
    found = [
        f for f in reclaim.resolve_bound_copilots(
            session_id=session_id, worktree_id=worktree_id,
            worktree_path=worktree_path, table=table)
        if f["homing"] == "bare"
    ]
    # Never adopt the process subtree running this very command.
    me = os.getpid()
    found = [
        f for f in found
        if me not in ({f["pid"]} | reclaim.descendants_of(f["pid"], table))
    ]
    if not found:
        return _fail("no bare (un-muxed) bound Copilot found for the target "
                     "(nothing to re-mux)")
    if len(found) > 1:
        pids = ", ".join(str(f["pid"]) for f in found)
        return _fail(f"multiple bare Copilots match ({pids}); narrow with "
                     f"--session-id")

    target = found[0]
    pid = target["pid"]
    wt_id = target["worktree_id"] or worktree_id
    if not wt_id:
        return _fail("could not resolve a worktree id for the bare Copilot")

    use_sudo = _needs_sudo() if force_sudo is None else force_sudo
    pane_cmd = _reptyr_pane_cmd(pid, use_sudo=use_sudo)
    work_dir = target.get("cwd") or ""

    # When elevating, propagate the caller's SUDO_ASKPASS (if any) into the
    # detached pane so `sudo -A` can prompt non-interactively; if unset, sudo
    # falls back to a tty prompt the operator answers on attach. We forward the
    # ambient value only -- never a hardcoded path.
    pane_env: dict[str, str] = {}
    if use_sudo:
        askpass = os.environ.get("SUDO_ASKPASS")
        if askpass:
            pane_env["SUDO_ASKPASS"] = askpass

    # Ensure the wt-<id> session and drop the reptyr pane in. A bogus
    # pane_wrapper path makes the shared builder run the command directly (no
    # setup-script wrapper) while keeping the identity-clean `env -u` prefix.
    no_wrapper = "/nonexistent-remux-no-wrapper"
    session_name = sessions.mux_session_name(wt_id)
    if sessions.has_mux_session(wt_id):
        argv = sessions.build_mux_new_window_argv(
            wt_id, work_dir, pane_cmd, pane_env or None,
            mux="tmux", pane_wrapper=no_wrapper)
    else:
        argv = sessions.build_mux_new_session_argv(
            wt_id, work_dir, pane_cmd, pane_env or None,
            mux="tmux", pane_wrapper=no_wrapper)

    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _fail(f"failed to open the reptyr pane: {e}", pid=pid,
                     worktree_id=wt_id)
    if r.returncode != 0:
        return _fail(
            f"tmux rejected the reptyr pane: {r.stderr.strip() or r.returncode}",
            pid=pid, worktree_id=wt_id)
    pane = r.stdout.strip() or None

    # Verify: poll until the process's controlling tty is a tmux pane (reptyr
    # has completed the hand-over) or the window closes.
    verified = False
    deadline = time.time() + max(0.0, verify_timeout)
    while time.time() < deadline:
        if not sessions._is_process_alive(pid):
            break
        if is_in_mux_pane(pid):
            verified = True
            break
        time.sleep(0.3)

    return {
        "ok": True,
        "reason": ("adopted into the mux pane" if verified else
                   "reptyr pane opened; hand-over not yet confirmed"),
        "worktree_id": wt_id,
        "session_id": target["session_id"],
        "pid": pid,
        "session": session_name,
        "pane": pane,
        "used_sudo": use_sudo,
        "verified": verified,
    }
